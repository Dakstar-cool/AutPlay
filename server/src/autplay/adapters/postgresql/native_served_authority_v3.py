"""Two-phase locked source3 head/BYTEA fetch; no public serving composition.

The head is deliberately unvalidated. No payload is decoded, serialized or
asserted until G has admitted its complete dependency closure and existing work
lease. Historical serving truth is a separate SQL validator/read path.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import fields
from types import TracebackType
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, SessionTransaction

from autplay.domain.native_connected_budget_v3 import NativeConnectedDependencyAdmissionV3
from autplay.domain.native_connected_original_v3 import NativeConnectedOriginalMeterV3
from autplay.domain.native_connected_request_v3 import (
    NativeConnectedV3Error,
    NativeP11UnvalidatedHeadV3,
)

NATIVE_P11_HEAD_BYTE_WORK_V3 = 262_144
NATIVE_P11_HEAD_NODE_VISITS_V3 = 4096
NATIVE_P11_HEAD_DIGEST_BYTES_V3 = 1024
NATIVE_P11_HEAD_SCRATCH_BYTES_V3 = 16_384
NATIVE_P11_ROOT_METADATA_BYTE_WORK_V3 = 16_384
NATIVE_P11_ROOT_METADATA_NODE_VISITS_V3 = 256
_HEAD_SCOPE_KEY = "native_v3_head_scope"
_ACTIVE_HEAD_KEY = "native_v3_active_head"
_PRIVATE_HEAD = object()

_SERVER_SQL = """
SELECT server_instance_id,identity_epoch FROM account.server_instance
ORDER BY server_instance_id LIMIT 2 FOR SHARE NOWAIT
"""

_OWNER_ADVISORY_SQL = """
SELECT pg_try_advisory_xact_lock(('x'||substr(encode(sha256(
 convert_to('autplay-sync-owner-publish-v1','UTF8')||uuid_send(:owner)),
 'hex'),1,16))::bit(64)::bigint)
"""

_HEAD_SQL = """
SELECT r.owner_user_id,r.served_request_id,r.original_request_id,
 r.device_id,r.journal_epoch,r.server_instance_id,r.identity_epoch,
 f.request_hash_format_version,
 CASE WHEN r.referenced_snapshot_id IS NULL THEN 3 ELSE 4 END AS root_proof_version,
 ofmt.original_capture_format_version AS original_capture_version,
 v.serving_contract_version,1 AS attribution_wire_version,2 AS normalized_event_wire_version,
 d.state,d.serving_provenance,d.inference_evidence_sha256,d.fallback_reason,
 r.root_proof_sha256,r.request_root_sha256,d.decision_sha256,c.connection_seal_sha256,
 b.bundle_sha256 AS capture_sha256,p.request_sha256 AS original_request_sha256,
 p.original_ranking_sha256,r.baseline_snapshot_id,r.temporal_snapshot_id,
 r.baseline_snapshot_sha256,r.temporal_snapshot_sha256,r.candidate_membership_sha256,
 r.original_policy_snapshot_sha256,r.original_availability_snapshot_sha256,
 r.eligibility_policy_sha256,v.pipeline_manifest_sha256,b.source_codec_sha256,
 p.original_profile_document_sha256,p.original_profile_sha256,
 p.original_profile_binary64_sha256,k.source_generation,
 v.pipeline_key,v.pipeline_version,
 v.surface,v.context,v.seed,v.interaction_watermark,b.native_interaction_watermark,
 v.catalog_snapshot,r.cutoff_at_ms,r.effective_limit,r.endpoint_max,r.exploration_units_e8,
 r.source_snapshot_track_count,r.candidate_universe_count,d.p11_item_count,d.served_item_count,
 b.source_event_count,b.source_evidence_count,b.profile_dimension_count,
 (extract(epoch FROM b.created_at)*1000000)::bigint AS original_created_at_us,
 (extract(epoch FROM r.created_at)*1000000)::bigint AS served_created_at_us,
 (extract(epoch FROM b.expires_at)*1000000)::bigint AS original_expires_at_us,
 (extract(epoch FROM p.expires_at)*1000000)::bigint AS replay_expires_at_us,
 (extract(epoch FROM d.served_truth_expires_at)*1000000)::bigint AS served_truth_expires_at_us,
 c.created_transaction::text AS created_transaction,c.consent_generation,c.consent_receipt_sha256,
 octet_length(r.root_document) AS root_byte_length,
 octet_length(b.bundle_document) AS capture_byte_length,
 octet_length(p.request_document) AS original_request_byte_length,
 octet_length(p.ranking_document) AS original_ranking_byte_length,
 octet_length(p.baseline_document) AS baseline_byte_length,
 octet_length(p.original_profile_document) AS original_profile_byte_length,
 k.source_cursor_byte_length,octet_length(p.pipeline_document) AS pipeline_byte_length,
 b.source_index_byte_length,
 p.original_items_byte_length,r.served_items_byte_length
FROM ml.recommendation_request_root_v3 r
JOIN ml.native_served_request_hash_format f
 ON f.served_request_id=r.served_request_id AND f.owner_user_id=r.owner_user_id
JOIN ml.native_atomic_capture_connection_v3 c
 ON c.served_request_id=r.served_request_id AND c.owner_user_id=r.owner_user_id
 AND c.original_request_id=r.original_request_id
JOIN ml.native_original_capture_v3 b
 ON b.original_request_id=c.original_request_id AND b.owner_user_id=c.owner_user_id
JOIN ml.native_original_capture_format ofmt
 ON ofmt.original_request_id=b.original_request_id AND ofmt.owner_user_id=b.owner_user_id
JOIN ml.native_capture_replay_v3 p
 ON p.original_request_id=b.original_request_id AND p.owner_user_id=b.owner_user_id
JOIN ml.native_capture_lineage_cursor_v3 k
 ON k.original_request_id=b.original_request_id AND k.owner_user_id=b.owner_user_id
JOIN ml.recommendation_request v
 ON v.recommendation_request_id=r.served_request_id AND v.user_id=r.owner_user_id
JOIN ml.recommendation_request o
 ON o.recommendation_request_id=r.original_request_id AND o.user_id=r.owner_user_id
JOIN ml.recommendation_serving_decision d
 ON d.recommendation_request_id=r.served_request_id
JOIN ml.recommendation_input_snapshot s
 ON s.recommendation_input_snapshot_id=p.baseline_snapshot_id AND s.user_id=p.owner_user_id
JOIN ml.recommendation_pipeline_version pv
 ON pv.pipeline_key=v.pipeline_key AND pv.version=v.pipeline_version
WHERE r.owner_user_id=:owner AND r.served_request_id=:served
 AND f.request_hash_format_version=3 AND ofmt.original_capture_format_version=3
 AND b.source_codec='NATIVE_SOURCE3_F64BITS_JSON_SAFE53_V1'
 AND b.source_codec_sha256=decode(
  'eda9439b265c5e92934f8f62a333782579a14aed116385271f158819045581a2','hex')
 AND r.source_codec_sha256=b.source_codec_sha256
 AND c.source_codec_sha256=b.source_codec_sha256
 AND p.pipeline_document_sha256=v.pipeline_manifest_sha256
 AND k.state='ACTIVE'
FOR UPDATE OF r,f,c,b,ofmt,p,k,v,o,d
FOR SHARE OF s,pv
"""

_ROOT_SQL = """
SELECT r.root_document
FROM ml.recommendation_request_root_v3 r
JOIN ml.native_atomic_capture_connection_v3 c
 ON c.served_request_id=r.served_request_id AND c.original_request_id=r.original_request_id
 AND c.owner_user_id=r.owner_user_id
WHERE r.owner_user_id=:owner AND r.served_request_id=:served
 AND r.original_request_id=:original AND r.root_proof_sha256=:sha
 AND octet_length(r.root_document)=:size AND c.created_transaction::text=:created_transaction
FOR UPDATE OF r,c
"""


def _transaction_binding(session: Session) -> tuple[int, int]:
    if (
        not session.in_transaction()
        or session.new
        or session.dirty
        or session.deleted
        or session.scalar(text("SHOW transaction_isolation")) != "read committed"
    ):
        raise NativeConnectedV3Error("CONNECTED_V3_HEAD_TRANSACTION")
    row = session.execute(text("SELECT pg_backend_pid(),pg_current_xact_id()::text")).one()
    backend, value = row
    if (
        type(backend) is not int
        or backend <= 0
        or type(value) is not str
        or not 1 <= len(value) <= 20
        or not value.isascii()
        or not value.isdigit()
        or not 1 <= int(value) <= 2**64 - 1
    ):
        raise NativeConnectedV3Error("CONNECTED_V3_HEAD_TRANSACTION")
    return backend, int(value)


def _clear_issued_head_v3(head: NativeP11UnvalidatedHeadV3) -> None:
    # Only this adapter's private DTO is scrubbed. Stale private aliases must
    # retain no owned payload after its resource lease has ended.
    for field in fields(head):
        value = getattr(head, field.name)
        if type(value) is bytes:
            object.__setattr__(head, field.name, b"")
        elif type(value) is str:
            object.__setattr__(head, field.name, "")
        elif type(value) is UUID:
            object.__setattr__(head, field.name, None)
        elif type(value) is int:
            object.__setattr__(head, field.name, 0)


def _release_head_resources_v3(meter: NativeConnectedOriginalMeterV3) -> None:
    failure: BaseException | None = None
    try:
        meter.release_scratch(NATIVE_P11_HEAD_SCRATCH_BYTES_V3)
    except BaseException as error:
        error.__traceback__ = error.__context__ = error.__cause__ = None
        failure = error
    try:
        meter.release_digests(NATIVE_P11_HEAD_DIGEST_BYTES_V3)
    except BaseException as error:
        error.__traceback__ = error.__context__ = error.__cause__ = None
        if failure is None:
            failure = error
    if failure is not None:
        raise failure from None


class NativeP11HeadLeaseV3:
    """A scoped private SQL head; borrowing never grants source authority."""

    __slots__ = (
        "_borrow_count",
        "_closed",
        "_head",
        "_issued_id",
        "_meter",
        "_scope",
        "_session",
        "_transaction_scope",
    )

    def __init__(
        self,
        token: object,
        *,
        session: Session,
        transaction_scope: SessionTransaction,
        scope: object,
        head: NativeP11UnvalidatedHeadV3,
        meter: NativeConnectedOriginalMeterV3,
    ) -> None:
        if token is not _PRIVATE_HEAD:
            raise NativeConnectedV3Error("CONNECTED_V3_HEAD_PRIVATE_LEASE")
        self._session: Session | None = session
        self._transaction_scope: SessionTransaction | None = transaction_scope
        self._scope: object | None = scope
        self._head: NativeP11UnvalidatedHeadV3 | None = head
        self._meter = meter
        self._closed = False
        self._borrow_count = 0
        self._issued_id = id(self)

    def check_current(self) -> None:
        if self._closed or self._issued_id != id(self):
            raise NativeConnectedV3Error("CONNECTED_V3_HEAD_SCOPE")
        self._meter.reserve(byte_work_bound=0, node_visit_bound=0)
        session = self._session
        if (
            session is None
            or session.get_transaction() is not self._transaction_scope
            or session.info.get(_HEAD_SCOPE_KEY) != (self._scope, id(self))
        ):
            raise NativeConnectedV3Error("CONNECTED_V3_HEAD_SCOPE")

    def close(self) -> None:
        if self._issued_id != id(self):
            raise NativeConnectedV3Error("CONNECTED_V3_HEAD_PRIVATE_LEASE")
        if self._closed:
            return
        if self._borrow_count:
            raise NativeConnectedV3Error("CONNECTED_V3_HEAD_BORROW_ACTIVE")
        session, scope, head = self._session, self._scope, self._head
        if head is not None:
            _clear_issued_head_v3(head)
        self._head = None
        self._session = None
        self._transaction_scope = None
        self._scope = None
        self._closed = True
        if session is not None:
            if session.info.get(_HEAD_SCOPE_KEY) == (scope, id(self)):
                session.info.pop(_HEAD_SCOPE_KEY, None)
            active = session.info.get(_ACTIVE_HEAD_KEY)
            if type(active) is tuple and len(active) == 2 and active[1] == id(head):
                session.info.pop(_ACTIVE_HEAD_KEY, None)
        head = None
        session = None
        scope = None
        _release_head_resources_v3(self._meter)

    def __enter__(self) -> NativeP11HeadLeaseV3:
        failure: BaseException | None = None
        try:
            self.check_current()
        except BaseException as error:
            error.__traceback__ = error.__context__ = error.__cause__ = None
            failure = error
            try:
                self.close()
            except BaseException as cleanup_error:
                cleanup_error.__traceback__ = cleanup_error.__context__ = (
                    cleanup_error.__cause__
                ) = None
                failure.add_note("CONNECTED_V3_HEAD_CLEANUP_REFUSED")
        if failure is not None:
            raise failure from None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_value is None:
            self.close()
            return
        try:
            self.close()
        except BaseException as cleanup_error:
            cleanup_error.__traceback__ = cleanup_error.__context__ = cleanup_error.__cause__ = None
            exc_value.add_note("CONNECTED_V3_HEAD_CLEANUP_REFUSED")


@contextmanager
def _borrow_head_v3(
    lease: NativeP11HeadLeaseV3, *, session: Session
) -> Iterator[NativeP11UnvalidatedHeadV3]:
    """Private G glue only: borrow the exact 76-field head in its SQL scope."""
    if type(lease) is not NativeP11HeadLeaseV3 or lease._session is not session:
        raise NativeConnectedV3Error("CONNECTED_V3_HEAD_SCOPE")
    lease.check_current()
    head = lease._head
    if type(head) is not NativeP11UnvalidatedHeadV3:
        raise NativeConnectedV3Error("CONNECTED_V3_HEAD_SCOPE")
    lease._borrow_count += 1
    try:
        yield head
        lease.check_current()
    finally:
        head = None
        lease._borrow_count -= 1


def _lock_head_owner_v3(session: Session, owner: UUID) -> tuple[UUID, int]:
    session.execute(text("SET LOCAL lock_timeout='5s'"))
    session.execute(text("SET LOCAL statement_timeout='30s'"))
    rows = session.execute(text(_SERVER_SQL)).all()
    if len(rows) != 1:
        raise NativeConnectedV3Error("CONNECTED_V3_HEAD_SERVER")
    server, epoch = rows[0]
    if type(server) is not UUID or type(epoch) is not int or not 1 <= epoch <= 2**63 - 1:
        raise NativeConnectedV3Error("CONNECTED_V3_HEAD_SERVER")
    present = session.scalar(
        text("SELECT user_id FROM account.user_account WHERE user_id=:owner FOR UPDATE NOWAIT"),
        {"owner": owner},
    )
    acquired = session.scalar(text(_OWNER_ADVISORY_SQL), {"owner": owner})
    if present != owner or acquired is not True:
        raise NativeConnectedV3Error("CONNECTED_V3_HEAD_OWNER_BUSY")
    return server, epoch


def read_native_p11_head_v3(
    session: Session,
    *,
    owner: UUID,
    served_request: UUID,
    meter: NativeConnectedOriginalMeterV3,
) -> NativeP11HeadLeaseV3:
    """Prepay scalar discovery and keep its head owned until explicit close."""
    if type(owner) is not UUID or type(served_request) is not UUID:
        raise NativeConnectedV3Error("CONNECTED_V3_HEAD_IDENTITY")
    if _HEAD_SCOPE_KEY in session.info:
        raise NativeConnectedV3Error("CONNECTED_V3_HEAD_OVERLAP")
    transaction_scope = session.get_transaction()
    if transaction_scope is None:
        raise NativeConnectedV3Error("CONNECTED_V3_HEAD_TRANSACTION")
    meter.reserve(
        byte_work_bound=NATIVE_P11_HEAD_BYTE_WORK_V3,
        node_visit_bound=NATIVE_P11_HEAD_NODE_VISITS_V3,
    )
    meter.retain_digests(NATIVE_P11_HEAD_DIGEST_BYTES_V3)
    admission_failure: BaseException | None = None
    try:
        meter.retain_scratch(NATIVE_P11_HEAD_SCRATCH_BYTES_V3)
    except BaseException as error:
        error.__traceback__ = error.__context__ = error.__cause__ = None
        admission_failure = error
        try:
            meter.release_digests(NATIVE_P11_HEAD_DIGEST_BYTES_V3)
        except BaseException as cleanup_error:
            cleanup_error.__traceback__ = cleanup_error.__context__ = cleanup_error.__cause__ = None
            admission_failure.add_note("CONNECTED_V3_HEAD_CLEANUP_REFUSED")
    if admission_failure is not None:
        raise admission_failure from None
    scope = object()
    session.info[_HEAD_SCOPE_KEY] = scope
    row = None
    head = None
    lease = None
    expected_fields = None
    failure: BaseException | None = None
    try:
        # The installed key/version limits (100 characters), closed context,
        # surface/state/provenance and NULL decision fields bound this 76-scalar
        # projection. Its 19 raw SHA values occupy 608 retained digest bytes.
        meter.charge(
            bytes_read=NATIVE_P11_HEAD_BYTE_WORK_V3,
            node_visits=NATIVE_P11_HEAD_NODE_VISITS_V3,
        )
        transaction = _transaction_binding(session)
        identity = _lock_head_owner_v3(session, owner)
        session.execute(
            text("SELECT app_private.lock_recommendation_request_v2(:request)"),
            {"request": served_request},
        )
        row = (
            session.execute(text(_HEAD_SQL), {"owner": owner, "served": served_request})
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise NativeConnectedV3Error("CONNECTED_V3_HEAD_UNAVAILABLE")
        expected_fields = {field.name for field in fields(NativeP11UnvalidatedHeadV3)}
        if set(row) != expected_fields:
            raise NativeConnectedV3Error("CONNECTED_V3_HEAD_SHAPE")
        head = NativeP11UnvalidatedHeadV3(**dict(row))
        if (
            (head.server_instance_id, head.identity_epoch) != identity
            or head.owner_user_id != owner
            or head.served_request_id != served_request
        ):
            raise NativeConnectedV3Error("CONNECTED_V3_HEAD_SERVER")
        if (
            session.get_transaction() is not transaction_scope
            or session.info.get(_HEAD_SCOPE_KEY) is not scope
        ):
            raise NativeConnectedV3Error("CONNECTED_V3_HEAD_SCOPE")
        session.info[_ACTIVE_HEAD_KEY] = (transaction, id(head))
        lease = NativeP11HeadLeaseV3(
            _PRIVATE_HEAD,
            session=session,
            transaction_scope=transaction_scope,
            scope=scope,
            head=head,
            meter=meter,
        )
        session.info[_HEAD_SCOPE_KEY] = (scope, id(lease))
        head = None
        row = None
        return lease
    except BaseException as error:
        # A constructor's traceback can retain its rejected DTO as ``self``.
        # Drop those frames before releasing either payload or scratch ownership.
        error.__traceback__ = error.__context__ = error.__cause__ = None
        row = None
        if head is not None:
            _clear_issued_head_v3(head)
        head = None
        expected_fields = None
        if lease is not None:
            if lease._head is not None:
                _clear_issued_head_v3(lease._head)
            lease._head = None
            lease._session = None
            lease._transaction_scope = None
            lease._scope = None
            lease._closed = True
            lease = None
        session.info.pop(_ACTIVE_HEAD_KEY, None)
        pin = session.info.get(_HEAD_SCOPE_KEY)
        if pin is scope or (type(pin) is tuple and len(pin) == 2 and pin[0] is scope):
            session.info.pop(_HEAD_SCOPE_KEY, None)
        failure = (
            NativeConnectedV3Error("CONNECTED_V3_HEAD_REFUSED")
            if isinstance(error, SQLAlchemyError)
            else error
        )
        try:
            _release_head_resources_v3(meter)
        except BaseException as cleanup_error:
            cleanup_error.__traceback__ = cleanup_error.__context__ = cleanup_error.__cause__ = None
            failure.add_note("CONNECTED_V3_HEAD_CLEANUP_REFUSED")
    assert failure is not None
    raise failure from None


def fetch_native_p11_root_v3(
    session: Session,
    head: NativeP11UnvalidatedHeadV3,
    *,
    admission: NativeConnectedDependencyAdmissionV3,
) -> bytes:
    """Fetch exact bounded root bytes only with G's privately issued admission."""
    if (
        type(head) is not NativeP11UnvalidatedHeadV3
        or type(admission) is not NativeConnectedDependencyAdmissionV3
    ):
        raise NativeConnectedV3Error("CONNECTED_V3_ROOT_ADMISSION")
    try:
        admission.charge(
            bytes_read=NATIVE_P11_ROOT_METADATA_BYTE_WORK_V3,
            node_visits=NATIVE_P11_ROOT_METADATA_NODE_VISITS_V3,
        )
        transaction = _transaction_binding(session)
        if session.info.get(_ACTIVE_HEAD_KEY) != (transaction, id(head)):
            raise NativeConnectedV3Error("CONNECTED_V3_ROOT_HEAD_PIN")
        admission.authorize_head_fetch(head, transaction_binding=transaction)
        session.info.pop(_ACTIVE_HEAD_KEY)
        # Work is consumed before SQL/driver allocation. A failed fetch cannot
        # refund the lease or create a new physical record's allowance.
        admission.charge(bytes_read=head.root_byte_length, node_visits=1)
        raw = session.scalar(
            text(_ROOT_SQL),
            {
                "owner": head.owner_user_id,
                "served": head.served_request_id,
                "original": head.original_request_id,
                "sha": head.root_proof_sha256,
                "size": head.root_byte_length,
                "created_transaction": head.created_transaction,
            },
        )
        if type(raw) is not bytes:
            raise NativeConnectedV3Error("CONNECTED_V3_ROOT_UNAVAILABLE")
        admission.bind_bytes(raw)
        return raw
    except SQLAlchemyError as error:
        error.__traceback__ = error.__context__ = error.__cause__ = None
    raise NativeConnectedV3Error("CONNECTED_V3_ROOT_REFUSED") from None
