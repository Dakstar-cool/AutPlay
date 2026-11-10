package app.autplay.application.desktop.account

data class AccountIntentReviewItem(val changeId:AccountUuid,val eventType:String,val aggregateType:String,val localId:AccountUuid)
data class AccountIntentSelection(val selectionId:AccountUuid,val sourceScopeId:AccountUuid,val targetScopeId:AccountUuid,
    val reviewRevision:Long,val items:List<AccountIntentReviewItem>)
data class AccountIntentMaterializationResult(val links:List<AccountMaterializationLink>,val unavailableChangeIds:List<AccountUuid>)

/** Explicit same-account ROOT-only J intent conversion. Old authenticated events are never inputs. */
interface DesktopAccountIntentMaterialization {
    suspend fun freeze(lease:AccountLease,sourceScopeId:AccountUuid,selectionId:AccountUuid,reviewRevision:Long,changeIds:List<AccountUuid>):AccountIntentSelection
    suspend fun materialize(lease:AccountLease,selectionId:AccountUuid,reviewRevision:Long):AccountIntentMaterializationResult
}
