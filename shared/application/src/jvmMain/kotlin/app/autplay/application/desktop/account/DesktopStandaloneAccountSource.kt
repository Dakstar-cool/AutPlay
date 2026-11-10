package app.autplay.application.desktop.account

/** Read-only local intent metadata for an explicit selection; no payload, path or wire authority. */
data class StandaloneAccountFactChoice(
    val changeId: String,
    val kind: String,
    val occurredAtMs: Long,
    val eligible: Boolean,
)

data class StandaloneAccountFactPage(
    val facts: List<StandaloneAccountFactChoice>,
    val nextChangeId: String?,
)

/** Process-only review of the already-open owner, before any account/source registration exists. */
data class StandaloneAccountReviewSnapshot(val facts: List<AccountSelectedFact>,
    val dependencies: Map<String, kotlinx.serialization.json.JsonObject>)

interface DesktopStandaloneAccountReview {
    suspend fun readPage(afterChangeId: String?, limit: Int = 100): StandaloneAccountFactPage
    suspend fun readSelected(selectedChangeIds: List<String>): StandaloneAccountReviewSnapshot
}

/** One canonical, already-open standalone owner bound to J's retained source registration. */
interface DesktopStandaloneAccountSource : DesktopSelectedSourcePort {
    val source: AccountSourceRegistration
    suspend fun readPage(afterChangeId: String?, limit: Int = 100): StandaloneAccountFactPage
}
