package app.autplay.download

import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.offline.DownloadManager
import androidx.media3.exoplayer.offline.DownloadNotificationHelper
import androidx.media3.exoplayer.offline.DownloadService
import androidx.media3.exoplayer.scheduler.PlatformScheduler
import androidx.media3.exoplayer.scheduler.Scheduler
import app.autplay.R
import app.autplay.AutPlayRuntime
import app.autplay.application.download.DownloadIntentRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/** Media3 is the sole owner of long-running offline transfer execution and progress. */
@UnstableApi
class AutPlayDownloadService : DownloadService(
    FOREGROUND_NOTIFICATION_ID,
    DEFAULT_FOREGROUND_NOTIFICATION_UPDATE_INTERVAL,
    DOWNLOAD_CHANNEL_ID,
    R.string.download_channel_name,
    0,
) {
    private lateinit var repository: DownloadIntentRepository
    private val listener = object : DownloadManager.Listener {
        override fun onDownloadChanged(manager: DownloadManager, download: androidx.media3.exoplayer.offline.Download, finalException: Exception?) {
            reconciliationScope.launch { repository.reconcile(download, finalException, System.currentTimeMillis()) }
        }
    }

    override fun onCreate() {
        super.onCreate()
        repository = DownloadIntentRepository(applicationContext, AutPlayRuntime.database(applicationContext))
        getDownloadManager().addListener(listener)
        reconciliationScope.launch { repository.reconcileAll(getDownloadManager(), System.currentTimeMillis()) }
    }

    override fun onDestroy() {
        getDownloadManager().removeListener(listener)
        super.onDestroy()
    }

    override fun getDownloadManager(): DownloadManager =
        MediaDownloadComponents.get(applicationContext).downloadManager

    override fun getScheduler(): Scheduler = PlatformScheduler(this, PLATFORM_SCHEDULER_JOB_ID)

    override fun getForegroundNotification(
        downloads: MutableList<androidx.media3.exoplayer.offline.Download>,
        notMetRequirements: Int,
    ): android.app.Notification = DownloadNotificationHelper(this, DOWNLOAD_CHANNEL_ID)
        .buildProgressNotification(this, R.drawable.ic_launcher, null, null, downloads, notMetRequirements)

    private companion object {
        // Completion reconciliation must outlive a service instance. Media3 may stop the service
        // immediately after the terminal callback; startup reconciliation covers process death.
        val reconciliationScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        const val DOWNLOAD_CHANNEL_ID = "autplay_downloads"
        const val FOREGROUND_NOTIFICATION_ID = 2201
        const val PLATFORM_SCHEDULER_JOB_ID = 2202
    }
}
