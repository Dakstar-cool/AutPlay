package app.autplay.work

import android.content.Context
import androidx.work.*
import app.autplay.application.portableaccount.PortableAccountDataCodec
import java.time.Duration

/** Work input is two stable UUIDs. Endpoints, cursors, documents and secrets remain outside it. */
object PortableAccountWork {
    internal const val ACCOUNT_ID: String = "autplay.portable.account_id"
    internal const val PROFILE_ID: String = "autplay.portable.profile_id"

    suspend fun enqueue(context: Context, accountId: String, profileId: String): Unit =
        enqueue(context, accountId, profileId, ExistingWorkPolicy.KEEP)

    suspend fun enqueueLocal(context: Context, accountId: String): Unit =
        enqueue(context, accountId, null, ExistingWorkPolicy.KEEP)

    internal suspend fun continuePass(context: Context, accountId: String, profileId: String?): Unit =
        enqueue(context, accountId, profileId, ExistingWorkPolicy.APPEND_OR_REPLACE)

    private suspend fun enqueue(context: Context, accountId: String, profileId: String?, policy: ExistingWorkPolicy) {
        PortableAccountDataCodec.uuid(accountId); profileId?.let(PortableAccountDataCodec::uuid)
        val request = OneTimeWorkRequestBuilder<PortableAccountDataWorker>()
            .setInputData(Data.Builder().putString(ACCOUNT_ID, accountId).putString(PROFILE_ID, profileId).build())
            .setConstraints(Constraints.Builder().setRequiredNetworkType(if (profileId == null) NetworkType.NOT_REQUIRED else NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, Duration.ofSeconds(10))
            .addTag("autplay-portable-account-data")
            .build()
        WorkManager.getInstance(context.applicationContext).enqueueUniqueWork("autplay-portable-$accountId-$profileId", policy, request).await()
    }
}
