"""Process-level assembly for authentication runtime dependencies."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_process_upload import ProcessVaultChunkWriter
from autplay.adapters.jamendo import JamendoProvider
from autplay.adapters.postgresql.admin_commands import SqlAlchemyAdminCommandRepository
from autplay.adapters.postgresql.admin_views_runtime import SqlAlchemyAdminViewService
from autplay.adapters.postgresql.auth_runtime import SqlAlchemyAuthUnitOfWorkFactory
from autplay.adapters.postgresql.discovery_automation_runtime import (
    SqlAlchemyDiscoveryAutomationRepository,
)
from autplay.adapters.postgresql.recommendations import (
    SqlAlchemyOfflinePackRepository,
    SqlAlchemyRecommendationRuntime,
)
from autplay.adapters.postgresql.self_device_pairing import SqlAlchemySelfPairingUnitOfWorkFactory
from autplay.adapters.postgresql.vault_uow import SqlAlchemyVaultUnitOfWorkFactory
from autplay.adapters.postgresql.wave import SqlAlchemyWaveService
from autplay.adapters.postgresql.web_admin_uow import SqlAlchemyWebAdminUnitOfWorkFactory
from autplay.adapters.postgresql.web_passkeys import SqlAlchemyWebPasskeyUnitOfWorkFactory
from autplay.adapters.security.tokens import Hs256AccessTokenCodec, OpaqueRefreshTokenCodec
from autplay.adapters.system import SystemClock, Uuid7Generator
from autplay.adapters.webauthn import DuoWebPasskeyVerifier
from autplay.application.account_deletion import AccountDeletionService
from autplay.application.account_recovery import AccountRecoveryService
from autplay.application.admin_commands import AdminCommandService
from autplay.application.auth import AuthService
from autplay.application.bulk_discovery import BulkDiscoveryService
from autplay.application.catalog_artist_sync import CatalogArtistMutationService
from autplay.application.discovery_automation import DiscoveryAutomationService
from autplay.application.guest_room import GuestRoomService
from autplay.application.imports import ImportService
from autplay.application.library import LibraryService
from autplay.application.manual_discovery import ManualDiscoveryService
from autplay.application.profile_pairing import ProfilePairingService
from autplay.application.public_access import PublicAccessService
from autplay.application.recommendations import (
    RecommendationService,
    StaticRecommendationVersionRegistry,
)
from autplay.application.self_device_pairing import SelfDevicePairingService
from autplay.application.social import SocialService
from autplay.application.sync import SyncService
from autplay.application.training_consent import TrainingConsentService
from autplay.application.vault_uploads import (
    CreateUploadCommand,
    UploadInfo,
    UploadStateError,
    VaultPrincipal,
    VaultUploadService,
)
from autplay.application.web_admin import WebAdminService
from autplay.application.web_passkeys import WebPasskeyService
from autplay.domain.auth import Principal
from autplay.domain.profile_pairing import load_private_key
from autplay.domain.resource_admission import LocalBridgeClaim, ResourceAdmissionError
from autplay.domain.vault import (
    ChunkWriteResult,
    OpaqueStorageKey,
    Sha256Digest,
    VaultCapacityError,
    VaultLimits,
)
from autplay.entrypoints.stream_http import AuthorizedStream
from autplay.entrypoints.vault_http import AdmittedChunk, UploadView
from autplay.runtime.settings import ApiSettings, StreamSettings
from autplay.runtime.vault_io import VaultIoSession


def build_auth_service(settings: ApiSettings, engine: Engine) -> AuthService:
    """Assemble authentication without opening a database connection eagerly."""

    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    access_ttl = timedelta(seconds=settings.access_token_ttl_seconds)
    return AuthService(
        unit_of_work_factory=SqlAlchemyAuthUnitOfWorkFactory(sessions),
        clock=SystemClock(),
        ids=Uuid7Generator(),
        access_tokens=Hs256AccessTokenCodec(
            settings.auth_signing_secret.get_secret_value(),
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            max_ttl=access_ttl,
        ),
        refresh_tokens=OpaqueRefreshTokenCodec(),
        access_token_ttl=access_ttl,
        refresh_token_ttl=timedelta(seconds=settings.refresh_token_ttl_seconds),
    )


def build_profile_pairing_service(
    settings: ApiSettings, engine: Engine
) -> ProfilePairingService | None:
    """Assemble M5B only when an operator supplied a persistent secret-file key."""
    pem = settings.profile_identity_private_key_pem
    if pem is None:
        return None
    return ProfilePairingService(
        sessionmaker(engine, class_=Session, expire_on_commit=False),
        private_key=load_private_key(pem.get_secret_value().encode("utf-8")),
        label_hint=settings.profile_label_hint,
        api_origin=settings.profile_api_origin,
        stream_origin=settings.profile_stream_origin,
        access_tokens=Hs256AccessTokenCodec(
            settings.auth_signing_secret.get_secret_value(),
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            max_ttl=timedelta(seconds=settings.access_token_ttl_seconds),
        ),
        access_ttl=timedelta(seconds=settings.access_token_ttl_seconds),
        self_device_pairing_enabled=settings.self_device_pairing_enabled,
        account_recovery_enabled=settings.account_recovery_enabled,
        account_deletion_enabled=settings.account_deletion_enabled,
        shared_training_consent_enabled=settings.shared_training_consent_enabled,
    )


def build_public_access_service(settings: ApiSettings, engine: Engine) -> PublicAccessService:
    """Assemble PA2 without modifying M5 enrollment authority."""
    return PublicAccessService(
        sessionmaker(engine, class_=Session, expire_on_commit=False),
        access_tokens=Hs256AccessTokenCodec(
            settings.auth_signing_secret.get_secret_value(),
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            max_ttl=timedelta(seconds=settings.access_token_ttl_seconds),
        ),
        access_ttl=timedelta(seconds=settings.access_token_ttl_seconds),
        source_hmac_secret=settings.public_access_source_hmac_secret.get_secret_value().encode(
            "utf-8"
        ),
    )


def build_self_device_pairing_service(
    settings: ApiSettings, engine: Engine
) -> SelfDevicePairingService | None:
    if not settings.self_device_pairing_enabled:
        return None
    ttl = timedelta(seconds=settings.access_token_ttl_seconds)
    return SelfDevicePairingService(
        SqlAlchemySelfPairingUnitOfWorkFactory(
            sessionmaker(engine, class_=Session, expire_on_commit=False)
        ),
        Hs256AccessTokenCodec(
            settings.auth_signing_secret.get_secret_value(),
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            max_ttl=ttl,
        ),
        ttl,
        settings.public_access_source_hmac_secret.get_secret_value().encode(),
    )


def build_account_recovery_service(
    settings: ApiSettings, engine: Engine
) -> AccountRecoveryService | None:
    if not settings.account_recovery_enabled:
        return None
    ttl = timedelta(seconds=settings.access_token_ttl_seconds)
    return AccountRecoveryService(
        sessionmaker(engine, class_=Session, expire_on_commit=False),
        Hs256AccessTokenCodec(
            settings.auth_signing_secret.get_secret_value(),
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            max_ttl=ttl,
        ),
        ttl,
        settings.public_access_source_hmac_secret.get_secret_value().encode(),
    )


def build_account_deletion_service(
    settings: ApiSettings, engine: Engine
) -> AccountDeletionService | None:
    if not settings.account_deletion_enabled:
        return None
    recovery = build_account_recovery_service(settings, engine)
    if recovery is None:
        raise ValueError("account deletion requires recovery")
    from autplay.entrypoints.privacy_deletion import build_deletion_ledger

    ledger = build_deletion_ledger(settings)
    if ledger is None:
        raise ValueError("account deletion requires independent decisions")
    return AccountDeletionService(recovery, ledger)


def build_web_admin_service(settings: ApiSettings, engine: Engine) -> WebAdminService | None:
    """Assemble optional browser authority with its dedicated CSRF derivation secret."""

    secret = settings.admin_web_csrf_hmac_secret
    if secret is None:
        return None
    return WebAdminService(
        SqlAlchemyWebAdminUnitOfWorkFactory(
            sessionmaker(engine, class_=Session, expire_on_commit=False)
        ),
        csrf_secret=secret.get_secret_value().encode("utf-8"),
    )


def build_web_passkey_service(settings: ApiSettings, engine: Engine) -> WebPasskeyService | None:
    if not settings.admin_passkeys_enabled:
        return None
    secret = settings.admin_web_csrf_hmac_secret
    origin = settings.admin_web_origin
    if secret is None or origin is None:
        raise ValueError("passkey configuration unavailable")
    return WebPasskeyService(
        SqlAlchemyWebPasskeyUnitOfWorkFactory(
            sessionmaker(engine, class_=Session, expire_on_commit=False)
        ),
        DuoWebPasskeyVerifier(origin),
        secret.get_secret_value().encode("utf-8"),
    )


def build_manual_discovery_service(settings: ApiSettings) -> ManualDiscoveryService | None:
    """Assemble the disabled-by-default Jamendo adapter without touching PostgreSQL."""

    if not settings.jamendo_enabled:
        return None
    client_id = settings.jamendo_client_id
    staging_root = settings.jamendo_staging_root
    if client_id is None or staging_root is None:
        raise RuntimeError("Jamendo configuration is unavailable")
    return ManualDiscoveryService(
        JamendoProvider(
            client_id.get_secret_value(), timeout_seconds=settings.jamendo_timeout_seconds
        ),
        staging_root=staging_root,
        max_download_bytes=settings.jamendo_max_download_bytes,
        minimum_request_interval_seconds=settings.jamendo_minimum_request_interval_seconds,
    )


def build_bulk_discovery_service(engine: Engine) -> BulkDiscoveryService:
    """Assemble short owner-scoped A1B preview/start transactions."""

    return BulkDiscoveryService(sessionmaker(engine, class_=Session, expire_on_commit=False))


def build_discovery_automation_service(engine: Engine) -> DiscoveryAutomationService:
    """Assemble A1C with one short PostgreSQL transaction per operation."""

    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    return DiscoveryAutomationService(SqlAlchemyDiscoveryAutomationRepository(sessions))


def build_admin_view_service(engine: Engine) -> SqlAlchemyAdminViewService:
    """Assemble owner-scoped read models with one short session per query."""

    return SqlAlchemyAdminViewService(sessionmaker(engine, class_=Session, expire_on_commit=False))


def build_admin_command_service(engine: Engine) -> AdminCommandService:
    """Assemble audited, idempotent Android administration commands for Web."""

    return AdminCommandService(
        SqlAlchemyAdminCommandRepository(
            sessionmaker(engine, class_=Session, expire_on_commit=False)
        )
    )


class _AdmittedVaultHttpService:
    """Short-transaction HTTP adapter around the P06 application use case."""

    def __init__(self, settings: ApiSettings, engine: Engine) -> None:
        self._uows = SqlAlchemyVaultUnitOfWorkFactory(
            sessionmaker(engine, class_=Session, expire_on_commit=False)
        )
        self._limits = _vault_limits(settings)
        self._root = settings.vault_root
        self._minimum_free_bytes = settings.vault_low_disk_bytes
        self._clock = SystemClock()
        self._ids = Uuid7Generator()
        self._ttl = timedelta(seconds=settings.vault_session_ttl_seconds)

    def create(
        self,
        principal: Principal,
        *,
        recording_id: UUID,
        expected_size: int,
        declared_sha256: str | None,
        idempotency_key: str,
    ) -> tuple[UploadView, bool]:
        digest = None if declared_sha256 is None else Sha256Digest(bytes.fromhex(declared_sha256))
        command = CreateUploadCommand(recording_id, expected_size, idempotency_key, digest)
        with self._uows() as unit:
            service = VaultUploadService(
                repository=unit.vault,
                limits=self._limits,
                ttl=self._ttl,
            )
            info, created = service.create(
                _vault_principal(principal),
                command,
                now=self._clock.now(),
                staging_key=OpaqueStorageKey(self._ids.new().hex),
            )
            unit.commit()
        return _upload_view(info), created

    def resolve_playback_variant(self, principal: Principal, user_track_ref_id: UUID) -> UUID:
        with self._uows() as unit:
            audio_variant_id = unit.vault.resolve_playback_variant(
                _vault_principal(principal), user_track_ref_id
            )
            unit.commit()
        return audio_variant_id

    def status(self, principal: Principal, upload_id: UUID) -> UploadView:
        expired: tuple[UploadInfo, OpaqueStorageKey] | None
        with self._uows() as unit:
            service = VaultUploadService(repository=unit.vault)
            expired = service.expire_if_due(
                _vault_principal(principal), upload_id, now=self._clock.now()
            )
            info = (
                expired[0]
                if expired is not None
                else service.status(_vault_principal(principal), upload_id)
            )
            unit.commit()
        return _upload_view(info)

    def append(
        self,
        principal: Principal,
        upload_id: UUID,
        *,
        offset: int,
        chunk_index: int,
        payload: bytes,
        payload_sha256: str,
    ) -> int:
        raise ResourceAdmissionError("capability_missing")

    def append_admitted(self, command: AdmittedChunk, io: VaultIoSession) -> int:
        """Run wholly inside the retained pipe worker, with no nested admission UoW."""
        io.deadline.check()
        with self._uows.for_upload(io.registered) as unit:
            info = unit.vault.get_owned_for_update(
                _vault_principal(command.principal), command.upload_id
            )
            if info.expires_at <= self._clock.now():
                raise UploadStateError()
            service = VaultUploadService(
                repository=unit.vault,
                limits=self._limits,
                chunk_writer=ProcessVaultChunkWriter(
                    io.child,
                    io.registered,
                    root=self._root,
                    limits=self._limits,
                    minimum_free_bytes=self._minimum_free_bytes,
                ),
            )
            result = service.append(
                _vault_principal(command.principal),
                command.upload_id,
                offset=command.offset,
                chunk_index=command.chunk_index,
                payload=command.payload,
                payload_sha256=Sha256Digest(bytes.fromhex(command.payload_sha256)),
            )
            actor = io.actor
            if not isinstance(actor, (Principal, LocalBridgeClaim)):
                raise ResourceAdmissionError("resource_purpose_mismatch")
            unit.commit_admitted_upload(actor, command.upload_id, stopped=io.deadline.stopped)
        return result.next_offset

    def complete(self, principal: Principal, upload_id: UUID) -> UploadView:
        expired: tuple[UploadInfo, OpaqueStorageKey] | None
        with self._uows() as unit:
            service = VaultUploadService(
                repository=unit.vault,
                limits=self._limits,
                ttl=self._ttl,
            )
            expired = service.expire_if_due(
                _vault_principal(principal), upload_id, now=self._clock.now()
            )
            if expired is None:
                info = service.complete(_vault_principal(principal), upload_id)
            unit.commit()
        if expired is not None:
            raise UploadStateError()
        return _upload_view(info)

    def cancel(self, principal: Principal, upload_id: UUID) -> None:
        with self._uows() as unit:
            VaultUploadService(repository=unit.vault).cancel(_vault_principal(principal), upload_id)
            unit.commit()


class _DirectVaultChunkWriter:
    """Reconcile direct staging bytes for an explicitly opted-in personal server."""

    def __init__(self, storage: FilesystemVaultStorage, *, minimum_free_bytes: int) -> None:
        self._storage = storage
        self._minimum_free_bytes = minimum_free_bytes

    def append_reconciled_chunk(
        self,
        key: OpaqueStorageKey,
        *,
        committed_size: int,
        expected_size: int,
        offset: int,
        payload: bytes,
        payload_sha256: Sha256Digest,
    ) -> ChunkWriteResult:
        remaining = expected_size - committed_size
        if self._storage.available_bytes() - remaining < self._minimum_free_bytes:
            raise VaultCapacityError()
        self._storage.prepare_upload_staging(key, committed_size)
        return self._storage.write_chunk(
            key, offset=offset, payload=payload, payload_sha256=payload_sha256
        )


class _DirectVaultHttpService:
    """Explicit pre-admission compatibility writer for an unmeasured personal server."""

    def __init__(self, settings: ApiSettings, engine: Engine) -> None:
        self._uows = SqlAlchemyVaultUnitOfWorkFactory(
            sessionmaker(engine, class_=Session, expire_on_commit=False)
        )
        self._limits = _vault_limits(settings)
        self._storage = FilesystemVaultStorage(settings.vault_root, limits=self._limits)
        self._writer = _DirectVaultChunkWriter(
            self._storage, minimum_free_bytes=settings.vault_low_disk_bytes
        )
        self._minimum_free_bytes = settings.vault_low_disk_bytes
        self._clock = SystemClock()
        self._ids = Uuid7Generator()
        self._ttl = timedelta(seconds=settings.vault_session_ttl_seconds)

    def create(
        self,
        principal: Principal,
        *,
        recording_id: UUID,
        expected_size: int,
        declared_sha256: str | None,
        idempotency_key: str,
    ) -> tuple[UploadView, bool]:
        digest = None if declared_sha256 is None else Sha256Digest(bytes.fromhex(declared_sha256))
        command = CreateUploadCommand(recording_id, expected_size, idempotency_key, digest)
        if self._storage.available_bytes() - expected_size < self._minimum_free_bytes:
            raise VaultCapacityError()
        with self._uows() as unit:
            service = VaultUploadService(
                repository=unit.vault,
                limits=self._limits,
                ttl=self._ttl,
            )
            info, created = service.create(
                _vault_principal(principal),
                command,
                now=self._clock.now(),
                staging_key=OpaqueStorageKey(self._ids.new().hex),
            )
            unit.commit()
        return _upload_view(info), created

    def resolve_playback_variant(self, principal: Principal, user_track_ref_id: UUID) -> UUID:
        with self._uows() as unit:
            audio_variant_id = unit.vault.resolve_playback_variant(
                _vault_principal(principal), user_track_ref_id
            )
            unit.commit()
        return audio_variant_id

    def status(self, principal: Principal, upload_id: UUID) -> UploadView:
        with self._uows() as unit:
            service = VaultUploadService(repository=unit.vault)
            expired = service.expire_if_due(
                _vault_principal(principal), upload_id, now=self._clock.now()
            )
            info = (
                expired[0]
                if expired is not None
                else service.status(_vault_principal(principal), upload_id)
            )
            unit.commit()
        if expired is not None:
            self._quarantine_expired(upload_id, expired[1])
        return _upload_view(info)

    def append(
        self,
        principal: Principal,
        upload_id: UUID,
        *,
        offset: int,
        chunk_index: int,
        payload: bytes,
        payload_sha256: str,
    ) -> int:
        with self._uows() as unit:
            service = VaultUploadService(
                repository=unit.vault,
                limits=self._limits,
                ttl=self._ttl,
                chunk_writer=self._writer,
            )
            expired = service.expire_if_due(
                _vault_principal(principal), upload_id, now=self._clock.now()
            )
            if expired is None:
                result = service.append(
                    _vault_principal(principal),
                    upload_id,
                    offset=offset,
                    chunk_index=chunk_index,
                    payload=payload,
                    payload_sha256=Sha256Digest(bytes.fromhex(payload_sha256)),
                )
            unit.commit()
        if expired is not None:
            self._quarantine_expired(upload_id, expired[1])
            raise UploadStateError()
        return result.next_offset

    def complete(self, principal: Principal, upload_id: UUID) -> UploadView:
        if self._storage.available_bytes() < self._minimum_free_bytes:
            raise VaultCapacityError()
        with self._uows() as unit:
            service = VaultUploadService(
                repository=unit.vault,
                limits=self._limits,
                ttl=self._ttl,
            )
            expired = service.expire_if_due(
                _vault_principal(principal), upload_id, now=self._clock.now()
            )
            if expired is None:
                info = service.complete(_vault_principal(principal), upload_id)
            unit.commit()
        if expired is not None:
            self._quarantine_expired(upload_id, expired[1])
            raise UploadStateError()
        return _upload_view(info)

    def cancel(self, principal: Principal, upload_id: UUID) -> None:
        with self._uows() as unit:
            staging_key = unit.vault.staging_key_for_owned(_vault_principal(principal), upload_id)
            VaultUploadService(repository=unit.vault).cancel(_vault_principal(principal), upload_id)
            unit.commit()
        try:
            self._storage.quarantine(staging_key, OpaqueStorageKey(f"cancelled-{upload_id.hex}"))
        except Exception as error:
            if getattr(error, "code", None) != "staged_file_not_found":
                raise

    def _quarantine_expired(self, upload_id: UUID, staging_key: OpaqueStorageKey) -> None:
        try:
            self._storage.quarantine(staging_key, OpaqueStorageKey(f"expired-{upload_id.hex}"))
        except Exception as error:
            if getattr(error, "code", None) != "staged_file_not_found":
                raise


def build_vault_http_service(
    settings: ApiSettings, engine: Engine
) -> _AdmittedVaultHttpService | _DirectVaultHttpService:
    """Assemble the API upload adapter without connecting to PostgreSQL eagerly."""

    if settings.direct_vault_upload_enabled:
        return _DirectVaultHttpService(settings, engine)
    return _AdmittedVaultHttpService(settings, engine)


def build_library_service(engine: Engine) -> LibraryService:
    """Assemble P07 owner-scoped commands and read projections for future sync."""

    return LibraryService(
        sessionmaker(engine, class_=Session, expire_on_commit=False), SystemClock().now
    )


def build_import_service(engine: Engine) -> ImportService:
    """Assemble imports; review publishes Artist closure in the same transaction."""

    return ImportService(sessionmaker(engine, class_=Session, expire_on_commit=False))


def build_catalog_artist_mutation_service(engine: Engine) -> CatalogArtistMutationService:
    """Expose the sole transaction-owned canonical Artist mutation boundary."""
    return CatalogArtistMutationService(engine)


def build_sync_service(settings: ApiSettings, engine: Engine) -> SyncService:
    """Assemble P09 using the existing access-token secret for cursor integrity."""
    return SyncService(
        engine, cursor_secret=settings.auth_signing_secret.get_secret_value().encode()
    )


def build_recommendation_service(engine: Engine) -> RecommendationService:
    """Assemble the CPU-only P11 graph without embeddings or model dependencies."""
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    runtime = SqlAlchemyRecommendationRuntime(sessions)
    return RecommendationService(
        snapshots=runtime,
        traces=runtime,
        registry=StaticRecommendationVersionRegistry(),
        ids=Uuid7Generator().new,
        clock=SystemClock().now,
        packs=SqlAlchemyOfflinePackRepository(runtime),
        atomic_writer=runtime,
    )


def build_wave_service(engine: Engine) -> SqlAlchemyWaveService:
    """Assemble the P13 durable Wave repository with per-operation sessions."""
    return SqlAlchemyWaveService(sessionmaker(engine, class_=Session, expire_on_commit=False))


def build_guest_room_service(engine: Engine) -> GuestRoomService:
    """Assemble the separate S1D guest capability authority."""
    return GuestRoomService(sessionmaker(engine, class_=Session, expire_on_commit=False))


def build_social_service(settings: ApiSettings, engine: Engine) -> SocialService:
    """Assemble S1C only with the same server identity used for contact cards."""
    pem = settings.profile_identity_private_key_pem
    key = None if pem is None else load_private_key(pem.get_secret_value().encode("utf-8"))
    return SocialService(sessionmaker(engine, class_=Session, expire_on_commit=False), key)


def build_training_consent_service(
    settings: ApiSettings, engine: Engine
) -> TrainingConsentService | None:
    if not settings.shared_training_consent_enabled:
        return None
    from autplay.entrypoints.training_consent_restore import build_training_consent_ledger

    ledger = build_training_consent_ledger(settings)
    if ledger is None:
        raise ValueError("independent training consent evidence required")
    return TrainingConsentService(
        sessionmaker(engine, class_=Session, expire_on_commit=False), ledger
    )


class _StreamLookup:
    """Open a short authorization query and release its session before file I/O."""

    def __init__(self, engine: Engine) -> None:
        self._uows = SqlAlchemyVaultUnitOfWorkFactory(
            sessionmaker(engine, class_=Session, expire_on_commit=False)
        )

    def resolve(self, principal: Principal, audio_variant_id: UUID) -> AuthorizedStream:
        with self._uows() as unit:
            resolved = unit.vault.resolve_stream(_vault_principal(principal), audio_variant_id)
            unit.commit()
        return AuthorizedStream(
            storage_key=resolved.storage_key,
            sha256=resolved.sha256,
            byte_size=resolved.byte_size,
            media_type=resolved.media_type,
            verified_at=resolved.verified_at,
        )


def build_stream_lookup(engine: Engine) -> _StreamLookup:
    """Assemble the owner-filtering lookup for the isolated stream process."""

    return _StreamLookup(engine)


def build_stream_auth_service(settings: StreamSettings, engine: Engine) -> AuthService:
    """Assemble auth for stream without importing API-only settings."""

    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    access_ttl = timedelta(seconds=900)
    return AuthService(
        unit_of_work_factory=SqlAlchemyAuthUnitOfWorkFactory(sessions),
        clock=SystemClock(),
        ids=Uuid7Generator(),
        access_tokens=Hs256AccessTokenCodec(
            settings.auth_signing_secret.get_secret_value(),
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            max_ttl=access_ttl,
        ),
        refresh_tokens=OpaqueRefreshTokenCodec(),
        access_token_ttl=access_ttl,
        refresh_token_ttl=timedelta(days=30),
    )


def _vault_limits(settings: ApiSettings) -> VaultLimits:
    return VaultLimits(
        max_object_bytes=settings.vault_max_object_bytes,
        max_chunk_bytes=settings.vault_max_chunk_bytes,
        io_block_bytes=settings.vault_stream_block_bytes,
    )


def _vault_principal(principal: Principal) -> VaultPrincipal:
    return VaultPrincipal(principal.user_id, principal.device_id)


def _upload_view(info: UploadInfo) -> UploadView:
    return UploadView(info.upload_session_id, info.received_size, info.expected_size, info.state)


__all__ = (
    "build_admin_command_service",
    "build_admin_view_service",
    "build_auth_service",
    "build_bulk_discovery_service",
    "build_catalog_artist_mutation_service",
    "build_discovery_automation_service",
    "build_guest_room_service",
    "build_import_service",
    "build_library_service",
    "build_manual_discovery_service",
    "build_profile_pairing_service",
    "build_public_access_service",
    "build_recommendation_service",
    "build_stream_auth_service",
    "build_stream_lookup",
    "build_sync_service",
    "build_vault_http_service",
    "build_wave_service",
    "build_web_admin_service",
)
