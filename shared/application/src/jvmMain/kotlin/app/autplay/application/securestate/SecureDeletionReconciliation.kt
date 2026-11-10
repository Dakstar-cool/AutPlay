package app.autplay.application.securestate

/** Positive credential-first cancellation evidence, never a fabricated deletion-status receipt. */
public object SecureDeletionReconciliation {
    public fun matches(source: SecureRecord, recipient: SecureRecord, exactResponse: ByteArray): Boolean {
        val pending = recipient.payload as? PendingPayload ?: return false
        if (source.payload.kind != SecureRecordKind.DELETION_SOURCE || !source.owner.deletionVeto ||
            pending.kind != SecureRecordKind.DELETION_RECIPIENT || pending.wireKind != PendingWireKind.DELETION_CANCEL ||
            recipient.owner.stage != SecureStage.BINDING_COMMIT_PENDING || source.owner.account != recipient.owner.account ||
            source.owner.server != recipient.owner.server || source.owner.identityEpoch != recipient.owner.identityEpoch ||
            source.owner.identityThumbprint != recipient.owner.identityThumbprint || source.owner.apiOrigin != recipient.owner.apiOrigin ||
            source.owner.streamOrigin != recipient.owner.streamOrigin) return false
        val request = pending.requestCopy(); val selected = pending.receiptCopy()
        try {
            if (!selected.contentEquals(exactResponse)) return false
            val command = SecureJson.parse(request, 8192, canonical = false)
            val response = SecureJson.parse(exactResponse, 65536, canonical = false)
            return command.string("deletion_request_id") == source.owner.operation &&
                response.string("contract_version") == "v2" && response.number("schema_version") == 2L &&
                response.string("platform") == "MACOS" && response.string("operation_id") == recipient.owner.operation &&
                response.string("operation_id") == command.string("operation_id") &&
                response.string("user_id") == source.owner.account && response.string("server_instance_id") == source.owner.server &&
                response.string("binding_commit_id") == recipient.owner.bindingCommit &&
                response.number("code_generation") == SecureStateLimits.next(command.number("expected_code_generation"))
        } finally { request.fill(0); selected.fill(0) }
    }
}
