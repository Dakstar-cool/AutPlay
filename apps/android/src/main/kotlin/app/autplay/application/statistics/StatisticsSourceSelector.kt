package app.autplay.application.statistics

import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.ensureActive
import kotlin.coroutines.coroutineContext

/** Captured authenticated binding, or the standalone encrypted root, owns one UI read. */
data class StatisticsIdentity(
    val profileId: String?,
    val accountId: String?,
    val deviceId: String? = null,
    val bindingCommitId: String? = null,
)

enum class StatisticsSource { PROFILE_LOCAL, ACCOUNT }

data class SelectedStatistics(
    val identity: StatisticsIdentity,
    val source: StatisticsSource,
    val snapshot: OwnerProfileStatistics,
)

/** Manual reads and cache invalidation share availability, scope and currentness decisions. */
class StatisticsSourceSelector(
    private val accounts: PortableAccountStatisticsRepository,
    private val profiles: ProfileStatisticsRepository,
    private val requireCurrent: suspend (StatisticsIdentity) -> Unit,
) {
    suspend fun verify(identity: StatisticsIdentity) {
        coroutineContext.ensureActive()
        requireCurrent(identity)
    }

    suspend fun load(identity: StatisticsIdentity, remote: AccountStatisticsPort? = null): SelectedStatistics {
        verify(identity)
        val account = identity.accountId
        val available = account?.let {
            accounts.refreshAvailable(it, identity.profileId?.let(::ServerProfileId), remote)
        }
        coroutineContext.ensureActive()
        requireCurrent(identity)
        if (available != null) {
            check(available.accountId == account) { "ACCOUNT_CONTEXT_CHANGED" }
            return SelectedStatistics(identity, StatisticsSource.ACCOUNT, available)
        }
        // Reading retained profile history is not ownership adoption or an account-wide claim.
        val local = profiles.refresh(identity.profileId)
        coroutineContext.ensureActive()
        requireCurrent(identity)
        check(local.accountId == null) { "ACCOUNT_CONTEXT_CHANGED" }
        return SelectedStatistics(identity, StatisticsSource.PROFILE_LOCAL, local)
    }
}
