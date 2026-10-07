package app.autplay.download

import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.scheduler.Requirements

/** Restricts durable offline audio transfers without restricting metadata or playback. */
@UnstableApi
internal fun downloadNetworkRequirements(allowMeteredNetwork: Boolean): Requirements =
    Requirements(if (allowMeteredNetwork) Requirements.NETWORK else Requirements.NETWORK_UNMETERED)
