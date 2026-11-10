package app.autplay.application.desktop.account

import kotlinx.serialization.json.JsonObject

data class AccountInteractionProjection(val localId:AccountUuid,val serverEventId:AccountUuid?,val deviceId:AccountUuid?,
    val rawKind:String,val recommendation:JsonObject,val feedbackType:String?,val sourceCreatedAt:String?,val pending:Boolean) {
    override fun toString():String="AccountInteractionProjection"
}
interface DesktopAccountInteractions {
    /** Call only for an item actually visible in the captured presentation, never for delivery. */
    suspend fun presented(lease:AccountLease,requestedEventId:AccountUuid,occurredAtMs:Long,payload:JsonObject):AccountDomainReceipt
    suspend fun readPage(lease:AccountLease,after:AccountUuid?,limit:Int=100):List<AccountInteractionProjection>
}
