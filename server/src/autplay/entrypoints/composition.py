"""Process-level assembly for authentication runtime dependencies."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.jamendo import JamendoProvider
from autplay.adapters.postgresql.admin_commands import SqlAlchemyAdminCommandRepository
from autplay.adapters.postgresql.admin_views_runtime import SqlAlchemyAdminViewService
from autplay.adapters.postgresql.auth_runtime import SqlAlchemyAuthUnitOfWorkFactory
from autplay.adapters.postgresql.recommendations import (
    SqlAlchemyOfflinePackRepository,
    SqlAlchemyRecommendationRuntime,
)
from autplay.adapters.postgresql.vault_uow import SqlAlchemyVaultUnitOfWorkFactory
from autplay.adapters.postgresql.wave import SqlAlchemyWaveService
from autplay.adapters.postgresql.web_admin_uow import SqlAlchemyWebAdminUnitOfWorkFactory
from autplay.adapters.security.tokens import Hs256AccessTokenCodec, OpaqueRefreshTokenCodec
from autplay.adapters.system import SystemClock, Uuid7Generator
from autplay.application.admin_commands import AdminCommandService
from autplay.application.auth import AuthService
from autplay.application.bulk_discovery import BulkDiscoveryService
from autplay.application.catalog_artist_sync import CatalogArtistMutationService
from autplay.application.imports import ImportService
from autplay.application.library import LibraryService
from autplay.application.manual_discovery import ManualDiscoveryService
from autplay.application.profile_pairing import ProfilePairingService
from autplay.application.recommendations import (
    RecommendationService,
    StaticRecommendationVersionRegistry,
)
from autplay.application.sync import SyncService
from autplay.application.vault_uploads import (
    CreateUploadCommand,
    UploadInfo,
    UploadStateError,
    VaultPrincipal,
    VaultUploadService,
)
from autplay.application.web_admin import WebAdminService
from autplay.domain.auth import Principal
from autplay.domain.profile_pairing import load_private_key
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest, VaultLimits
from autplay.entrypoints.stream_http import AuthorizedStream
from autplay.entrypoints.vault_http import UploadView
from autplay.runtime.settings import ApiSettings, StreamSettings


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
    )


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


class _VaultHttpService:
    """Short-transaction HTTP adapter around the P06 application use case."""

    def __init__(self, settings: ApiSettings, engine: Engine) -> None:
        self._uows = SqlAlchemyVaultUnitOfWorkFactory(
            sessionmaker(engine, class_=Session, expire_on_commit=False)
        )
        self._storage = FilesystemVaultStorage(settings.vault_root, limits=_vault_limits(settings))
        self._limits = _vault_limits(settings)
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
                storage=self._storage,
                limits=self._limits,
                ttl=self._ttl,
                minimum_free_bytes=self._minimum_free_bytes,
            )
            info, created = service.create(
                _vault_principal(principal),
                command,
                now=self._clock.now(),
                staging_key=OpaqueStorageKey(self._ids.new().hex),
            )
            unit.commit()
        return _upload_view(info), created

    def status(self, principal: Principal, upload_id: UUID) -> UploadView:
        expired: tuple[UploadInfo, OpaqueStorageKey] | None
        with self._uows() as unit:
            service = VaultUploadService(repository=unit.vault, storage=self._storage)
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
        expired: tuple[UploadInfo, OpaqueStorageKey] | None
        with self._uows() as unit:
            service = VaultUploadService(
                repository=unit.vault,
                storage=self._storage,
                limits=self._limits,
                ttl=self._ttl,
                minimum_free_bytes=self._minimum_free_bytes,
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
        expired: tuple[UploadInfo, OpaqueStorageKey] | None
        with self._uows() as unit:
            service = VaultUploadService(
                repository=unit.vault,
                storage=self._storage,
                limits=self._limits,
                ttl=self._ttl,
                minimum_free_bytes=self._minimum_free_bytes,
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
            VaultUploadService(repository=unit.vault, storage=self._storage).cancel(
                _vault_principal(principal), upload_id
            )
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


def build_vault_http_service(settings: ApiSettings, engine: Engine) -> _VaultHttpService:
    """Assemble the API upload adapter without connecting to PostgreSQL eagerly."""

    return _VaultHttpService(settings, engine)


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
    )


def build_wave_service(engine: Engine) -> SqlAlchemyWaveService:
    """Assemble the P13 durable Wave repository with per-operation sessions."""
    return SqlAlchemyWaveService(sessionmaker(engine, class_=Session, expire_on_commit=False))


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
    "build_import_service",
    "build_library_service",
    "build_manual_discovery_service",
    "build_profile_pairing_service",
    "build_recommendation_service",
    "build_stream_auth_service",
    "build_stream_lookup",
    "build_sync_service",
    "build_vault_http_service",
    "build_wave_service",
    "build_web_admin_service",
)
