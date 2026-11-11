"""Process-level assembly for authentication runtime dependencies."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from autplay.adapters.postgresql.media_source import PostgresMediaSourceReader
from autplay.adapters.postgresql.media_source_runtime import MediaSourceDatabaseRuntime
from autplay.adapters.postgresql.recommendations import (
    SqlAlchemyOfflinePackRepository,
    SqlAlchemyRecommendationRuntime,
)
from autplay.adapters.postgresql.self_device_pairing import (
    SqlAlchemySelfPairingUnitOfWorkFactory,
    SqlAlchemySelfPairingV2UnitOfWorkFactory,
)
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
from autplay.application.device_admission_service_v2 import DeviceAdmissionServiceV2
from autplay.application.device_compatibility import DeviceCompatibilityService
from autplay.application.device_deletion_service_v2 import DeviceDeletionServiceV2
from autplay.application.device_profile_service_v2 import DeviceProfileServiceV2
from autplay.application.device_recovery_service_v2 import DeviceRecoveryServiceV2
from autplay.application.device_self_service_v2 import DeviceSelfServiceV2
from autplay.application.discovery_automation import DiscoveryAutomationService
from autplay.application.guest_room import GuestRoomService
from autplay.application.imports import ImportService
from autplay.application.library import LibraryService
from autplay.application.manual_discovery import ManualDiscoveryService
from autplay.application.media_source import MediaSourceService
from autplay.application.portable_account import PortableAccountService
from autplay.application.profile_pairing import ProfilePairingService
from autplay.application.public_access import PublicAccessService
from autplay.application.recommendations import (
    RecommendationService,
    StaticRecommendationVersionRegistry,
    baseline_pipeline_definition,
)
from autplay.application.self_device_pairing import SelfDevicePairingService
from autplay.application.social import SocialService
from autplay.application.sync import SyncService
from autplay.application.sync_binding_v2 import SyncBindingV2Service
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
from autplay.domain.device_capability_state import DeviceCapabilityPolicy
from autplay.domain.device_compatibility import NegotiationPolicy
from autplay.domain.profile_pairing import load_private_key
from autplay.domain.resource_admission import LocalBridgeClaim, ResourceAdmissionError
from autplay.domain.vault import (
    ChunkWriteResult,
    OpaqueStorageKey,
    Sha256Digest,
    VaultCapacityError,
    VaultLimits,
)
from autplay.entrypoints.privacy_deletion import DeletionEvidenceRuntime
from autplay.entrypoints.stream_http import AuthorizedStream
from autplay.entrypoints.vault_http import AdmittedChunk, UploadView
from autplay.ports.media_source import ElapsedClock, MediaSourceAuthenticator, MediaSourcePolicy
from autplay.ports.sync_binding_v2 import BindingAccessAuthenticator
from autplay.runtime.settings import ApiSettings, StreamSettings
from autplay.runtime.vault_io import VaultIoSession


def build_identifier_sessions(
    settings: ApiSettings | StreamSettings,
    engine: Engine,
    *,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> sessionmaker[Session]:
    """Inject before opening sessions; standalone callers own restore lifecycle."""
    owner = (
        deletion_evidence
        if deletion_evidence is not None
        else DeletionEvidenceRuntime(settings, engine)
    )
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    owner.configure(sessions)
    return sessions


def build_auth_service(
    settings: ApiSettings,
    engine: Engine,
    *,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
    identity: ProfilePairingService | None = None,
) -> AuthService:
    """Assemble authentication without opening a database connection eagerly."""

    sessions = build_identifier_sessions(settings, engine, deletion_evidence=deletion_evidence)
    access_ttl = timedelta(seconds=settings.access_token_ttl_seconds)
    identity = identity or build_profile_pairing_service(
        settings, engine, deletion_evidence=deletion_evidence
    )
    return AuthService(
        unit_of_work_factory=SqlAlchemyAuthUnitOfWorkFactory(
            sessions,
            portable_identity=None
            if identity is None
            else identity.current_device_contract_identity,
        ),
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
    settings: ApiSettings,
    engine: Engine,
    *,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> ProfilePairingService | None:
    """Assemble M5B only when an operator supplied a persistent secret-file key."""
    pem = settings.profile_identity_private_key_pem
    if pem is None:
        return None
    return ProfilePairingService(
        build_identifier_sessions(settings, engine, deletion_evidence=deletion_evidence),
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
        portable_accounts_enabled=settings.portable_accounts_enabled,
        device_compatibility_v2_enabled=lambda: settings.device_compatibility_v2_enabled,
        media_source_snapshot_v1_enabled=settings.media_source_snapshot_v1_enabled,
    )


def build_device_compatibility_service(
    settings: ApiSettings,
    engine: Engine,
    *,
    identity: ProfilePairingService | None,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> DeviceCompatibilityService | None:
    """Keep negotiation composed when disabled; reuse the already assembled M5 owner."""
    if identity is None:
        return None
    return DeviceCompatibilityService(
        sessionmaker(engine, class_=Session, expire_on_commit=False),
        identity=identity,
        policy=_device_capability_policy(settings, deletion_evidence).d1,
        combined_policy=lambda: _device_capability_policy(settings, deletion_evidence),
    )


def _device_capability_policy(
    settings: ApiSettings,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> DeviceCapabilityPolicy:
    """The same live settings snapshot feeds BOTH shared revision producers."""
    return DeviceCapabilityPolicy(
        binding_enabled=settings.sync_binding_v2_enabled,
        d1=NegotiationPolicy(
            enabled=settings.device_compatibility_v2_enabled,
            portable_enabled=settings.portable_accounts_enabled,
            self_enabled=settings.self_device_pairing_enabled,
            recovery_enabled=settings.account_recovery_enabled,
            deletion_enabled=settings.account_deletion_enabled,
            deletion_evidence_ready=(
                deletion_evidence is not None and deletion_evidence.evidence_ready()
            ),
        ),
    )


def build_sync_binding_authenticator(
    authentication: AuthService,
    sessions: sessionmaker[Session],
    *,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> BindingAccessAuthenticator:
    """Keep the real codec/clock/auth rules; replace only the DB UoW owner."""
    # This returns only the access reader, never a session issuer. API-owned
    # transient factories still carry the same guard as its issuing factories.
    if deletion_evidence is not None:
        deletion_evidence.configure(sessions)
    original_units = authentication.unit_of_work_factory
    units = (
        original_units.using_sessions(sessions)
        if isinstance(original_units, SqlAlchemyAuthUnitOfWorkFactory)
        else SqlAlchemyAuthUnitOfWorkFactory(sessions)
    )
    return replace(authentication, unit_of_work_factory=units).authenticate_access_with_claims


def build_sync_binding_v2_service(
    settings: ApiSettings,
    engine: Engine,
    *,
    identity: ProfilePairingService | None,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> SyncBindingV2Service | None:
    if identity is None:
        return None
    # Off switches refuse first admission; they never remove negotiation/replay.
    return SyncBindingV2Service(
        sessionmaker(engine, class_=Session, expire_on_commit=False),
        identity=identity,
        policy=lambda: _device_capability_policy(settings, deletion_evidence),
    )


def build_media_source_service(
    settings: ApiSettings,
    database: MediaSourceDatabaseRuntime,
    *,
    identity: ProfilePairingService | None,
    clock: ElapsedClock,
) -> MediaSourceService:
    """Separate read-only pool; an absent initialized M5 view remains unavailable."""
    unavailable = MediaSourcePolicy(
        settings.media_source_snapshot_v1_enabled, None, settings.access_token_ttl_seconds
    )
    if isinstance(identity, ProfilePairingService):
        identity.configure_media_source_policy(enabled=settings.media_source_snapshot_v1_enabled)
    return MediaSourceService(
        PostgresMediaSourceReader(database.engine.sync_engine, clock=clock),
        policy=identity.current_media_source_policy
        if isinstance(identity, ProfilePairingService)
        else lambda: unavailable,
        clock=clock,
    )


def build_media_source_authenticator(
    authentication: AuthService,
    sessions: sessionmaker[Session],
    *,
    identity: ProfilePairingService | None,
    deletion_evidence: DeletionEvidenceRuntime,
) -> MediaSourceAuthenticator:
    """Actual codec/clock/R1 rules, with ALL reads in this operation's owned pool.

    R1 keeps its complete original receipt predicates and final-time evidence;
    M1 uses a read-only repeatable-read observation and the committed public M5
    capture instead of identity/resource/row locks. No reader or original-pool
    Session is cached or captured. The issuing-session deletion guard stays the
    same API-owned object; this UoW is used only to authenticate reads.
    """
    deletion_evidence.configure(sessions)
    units = SqlAlchemyAuthUnitOfWorkFactory(
        sessions,
        portable_identity=identity.media_source_portable_identity
        if isinstance(identity, ProfilePairingService)
        else None,
        portable_read_only=True,
    )
    return replace(authentication, unit_of_work_factory=units).authenticate_access_with_claims


def build_device_profile_v2_service(
    settings: ApiSettings,
    engine: Engine,
    *,
    identity: ProfilePairingService | None,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> DeviceProfileServiceV2 | None:
    """Retain replay handlers when disabled; M5 remains the sole identity owner."""
    if identity is None:
        return None
    access_ttl = timedelta(seconds=settings.access_token_ttl_seconds)
    return DeviceProfileServiceV2(
        build_identifier_sessions(settings, engine, deletion_evidence=deletion_evidence),
        identity=identity,
        enabled=lambda: settings.device_compatibility_v2_enabled,
        access_tokens=Hs256AccessTokenCodec(
            settings.auth_signing_secret.get_secret_value(),
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            max_ttl=access_ttl,
        ),
        access_ttl=access_ttl,
    )


def build_device_admission_v2_service(
    settings: ApiSettings,
    engine: Engine,
    *,
    identity: ProfilePairingService | None,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> DeviceAdmissionServiceV2 | None:
    """Keep bounded committed continuations when the admission flag is false."""
    if identity is None:
        return None
    return DeviceAdmissionServiceV2(
        build_identifier_sessions(settings, engine, deletion_evidence=deletion_evidence),
        identity=identity,
        enabled=lambda: settings.device_compatibility_v2_enabled,
    )


def build_device_self_v2_service(
    settings: ApiSettings,
    engine: Engine,
    *,
    identity: ProfilePairingService | None,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> DeviceSelfServiceV2 | None:
    """Compose bounded SELF receipts even when either independent admission gate is off."""
    if identity is None:
        return None
    sessions = build_identifier_sessions(settings, engine, deletion_evidence=deletion_evidence)
    ttl = timedelta(seconds=settings.access_token_ttl_seconds)
    access = Hs256AccessTokenCodec(
        settings.auth_signing_secret.get_secret_value(),
        issuer=settings.auth_issuer,
        audience=settings.auth_audience,
        max_ttl=ttl,
    )
    rates = SelfDevicePairingService(
        SqlAlchemySelfPairingUnitOfWorkFactory(sessions),
        access,
        ttl,
        settings.public_access_source_hmac_secret.get_secret_value().encode(),
    )
    return DeviceSelfServiceV2(
        SqlAlchemySelfPairingV2UnitOfWorkFactory(
            sessions, lambda session: identity.device_contract_identity(session, datetime.now(UTC))
        ),
        enabled=lambda: (
            settings.device_compatibility_v2_enabled and settings.self_device_pairing_enabled
        ),
        access_tokens=access,
        access_ttl=ttl,
        rates=rates,
    )


def build_device_recovery_v2_service(
    settings: ApiSettings,
    engine: Engine,
    *,
    identity: ProfilePairingService | None,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> DeviceRecoveryServiceV2 | None:
    """Retain exact committed replay/outcome when either independent gate is off."""
    if identity is None:
        return None
    ttl = timedelta(seconds=settings.access_token_ttl_seconds)
    recovery = AccountRecoveryService(
        build_identifier_sessions(settings, engine, deletion_evidence=deletion_evidence),
        Hs256AccessTokenCodec(
            settings.auth_signing_secret.get_secret_value(),
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            max_ttl=ttl,
        ),
        ttl,
        settings.public_access_source_hmac_secret.get_secret_value().encode(),
    )
    return DeviceRecoveryServiceV2(
        recovery,
        identity=identity,
        enabled=lambda: (
            settings.device_compatibility_v2_enabled and settings.account_recovery_enabled
        ),
    )


def build_public_access_service(
    settings: ApiSettings,
    engine: Engine,
    *,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> PublicAccessService:
    """Assemble PA2 without modifying M5 enrollment authority."""
    return PublicAccessService(
        build_identifier_sessions(settings, engine, deletion_evidence=deletion_evidence),
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


def build_portable_account_service(
    settings: ApiSettings,
    engine: Engine,
    *,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> PortableAccountService | None:
    if not settings.portable_accounts_enabled:
        return None
    ttl = timedelta(seconds=settings.access_token_ttl_seconds)
    return PortableAccountService(
        build_identifier_sessions(settings, engine, deletion_evidence=deletion_evidence),
        Hs256AccessTokenCodec(
            settings.auth_signing_secret.get_secret_value(),
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            max_ttl=ttl,
        ),
        ttl,
        settings.public_access_source_hmac_secret.get_secret_value().encode(),
    )


def build_self_device_pairing_service(
    settings: ApiSettings,
    engine: Engine,
    *,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> SelfDevicePairingService | None:
    if not settings.self_device_pairing_enabled:
        return None
    ttl = timedelta(seconds=settings.access_token_ttl_seconds)
    return SelfDevicePairingService(
        SqlAlchemySelfPairingUnitOfWorkFactory(
            build_identifier_sessions(settings, engine, deletion_evidence=deletion_evidence)
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
    settings: ApiSettings,
    engine: Engine,
    *,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> AccountRecoveryService | None:
    if not settings.account_recovery_enabled:
        return None
    ttl = timedelta(seconds=settings.access_token_ttl_seconds)
    return AccountRecoveryService(
        build_identifier_sessions(settings, engine, deletion_evidence=deletion_evidence),
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
    settings: ApiSettings,
    engine: Engine,
    *,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> AccountDeletionService | None:
    if not settings.account_deletion_enabled:
        return None
    owner = (
        deletion_evidence
        if deletion_evidence is not None
        else DeletionEvidenceRuntime(settings, engine)
    )
    recovery = build_account_recovery_service(settings, engine, deletion_evidence=owner)
    if recovery is None:
        raise ValueError("account deletion requires recovery")
    return AccountDeletionService(recovery, owner.ledger)


def build_device_deletion_v2_service(
    settings: ApiSettings,
    engine: Engine,
    *,
    identity: ProfilePairingService | None,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> DeviceDeletionServiceV2 | None:
    """Keep all six terminal handlers mounted across independent admission gates."""
    if identity is None:
        return None
    owner = (
        deletion_evidence
        if deletion_evidence is not None
        else DeletionEvidenceRuntime(settings, engine)
    )
    ttl = timedelta(seconds=settings.access_token_ttl_seconds)
    recovery = AccountRecoveryService(
        build_identifier_sessions(settings, engine, deletion_evidence=owner),
        Hs256AccessTokenCodec(
            settings.auth_signing_secret.get_secret_value(),
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            max_ttl=ttl,
        ),
        ttl,
        settings.public_access_source_hmac_secret.get_secret_value().encode(),
    )
    return DeviceDeletionServiceV2(
        AccountDeletionService(recovery, owner.ledger),
        identity=identity,
        enabled=lambda: settings.device_compatibility_v2_enabled,
        deletion_enabled=lambda: settings.account_deletion_enabled,
        recovery_enabled=lambda: settings.account_recovery_enabled,
        evidence_ready=owner.evidence_ready,
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


def build_admin_view_service(
    engine: Engine, *, vault_root: Path | None = None
) -> SqlAlchemyAdminViewService:
    """Assemble owner-scoped read models with one short session per query."""

    return SqlAlchemyAdminViewService(
        sessionmaker(engine, class_=Session, expire_on_commit=False), vault_root=vault_root
    )


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


def build_recommendation_service(
    engine: Engine, settings: ApiSettings | None = None
) -> RecommendationService:
    """Assemble the CPU-only P11 graph without embeddings or model dependencies."""
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    capture = None
    if settings is not None and settings.native_recommendation_capture_enabled:
        from autplay.adapters.postgresql.sona_native_capture import SqlAlchemySonaNativeCapture

        consent = build_training_consent_service(settings, engine)
        if consent is None:
            raise ValueError("native capture requires shared training consent authority")
        capture = SqlAlchemySonaNativeCapture(consent)
    runtime = SqlAlchemyRecommendationRuntime(
        sessions, native_capture=capture, snapshot_policy_version="2"
    )
    return RecommendationService(
        snapshots=runtime,
        traces=runtime,
        registry=StaticRecommendationVersionRegistry(baseline_pipeline_definition("2")),
        ids=Uuid7Generator().new,
        clock=SystemClock().now,
        packs=SqlAlchemyOfflinePackRepository(runtime),
        atomic_writer=runtime if capture is not None else None,
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


def build_stream_auth_service(
    settings: StreamSettings,
    engine: Engine,
    *,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> AuthService:
    """Assemble auth for stream without importing API-only settings."""

    sessions = build_identifier_sessions(settings, engine, deletion_evidence=deletion_evidence)
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
    "build_device_admission_v2_service",
    "build_device_compatibility_service",
    "build_device_deletion_v2_service",
    "build_device_profile_v2_service",
    "build_device_recovery_v2_service",
    "build_device_self_v2_service",
    "build_discovery_automation_service",
    "build_guest_room_service",
    "build_identifier_sessions",
    "build_import_service",
    "build_library_service",
    "build_manual_discovery_service",
    "build_profile_pairing_service",
    "build_public_access_service",
    "build_recommendation_service",
    "build_stream_auth_service",
    "build_stream_lookup",
    "build_sync_binding_v2_service",
    "build_sync_service",
    "build_vault_http_service",
    "build_wave_service",
    "build_web_admin_service",
)
