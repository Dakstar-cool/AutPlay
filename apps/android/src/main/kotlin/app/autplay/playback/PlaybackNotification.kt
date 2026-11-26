package app.autplay.playback

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.CommandButton
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionCommands
import app.autplay.MainActivity
import app.autplay.R

/** Notification navigation is consumed once, including launches into an existing Activity. */
@UnstableApi
internal object PlaybackNotification {
    const val ACTION_OPEN_PLAYER = "app.autplay.action.OPEN_PLAYER"
    const val ACTION_LIKE = "app.autplay.playback.LIKE"
    const val ACTION_DISLIKE = "app.autplay.playback.DISLIKE"
    const val ACTION_LIBRARY_NEXT = "app.autplay.playback.LIBRARY_NEXT"
    const val EXTRA_QUEUE_ENTRY_ID = "queue_entry_id"
    const val EXTRA_OPEN_HANDLED = "app.autplay.extra.PLAYER_OPEN_HANDLED"

    fun activityIntent(context: Context): Intent = Intent(context, MainActivity::class.java)
        .setAction(ACTION_OPEN_PLAYER)
        .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)

    fun sessionActivity(context: Context): PendingIntent = PendingIntent.getActivity(
        context, 0, activityIntent(context), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    @UnstableApi
    fun commands(): SessionCommands = androidx.media3.session.MediaSession.ConnectionResult.DEFAULT_SESSION_COMMANDS
        .buildUpon()
        .add(SessionCommand(ACTION_LIKE, Bundle.EMPTY))
        .add(SessionCommand(ACTION_DISLIKE, Bundle.EMPTY))
        .add(SessionCommand(ACTION_LIBRARY_NEXT, Bundle.EMPTY))
        .build()

    @UnstableApi
    fun buttons(context: Context, entryId: String?, preference: String?, enabled: Boolean, libraryNext: Boolean = false): List<CommandButton> {
        fun button(action: String, icon: Int, drawable: Int, label: Int): CommandButton =
            CommandButton.Builder(icon)
                .setCustomIconResId(drawable)
                .setDisplayName(context.getString(label))
                .setSessionCommand(SessionCommand(action, Bundle().apply { putString(EXTRA_QUEUE_ENTRY_ID, entryId) }))
                // Platform media sessions project custom actions through the overflow slot.
                .setSlots(CommandButton.SLOT_OVERFLOW)
                .setEnabled(enabled)
                .build()
        return listOf(
            CommandButton.Builder(CommandButton.ICON_NEXT)
                .apply {
                    if (libraryNext) setSessionCommand(SessionCommand(ACTION_LIBRARY_NEXT, Bundle().apply { putString(EXTRA_QUEUE_ENTRY_ID, entryId) }))
                    else setPlayerCommand(Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM)
                }
                .setDisplayName(context.getString(R.string.player_next_track))
                .setSlots(CommandButton.SLOT_FORWARD)
                .build(),
            CommandButton.Builder(CommandButton.ICON_PREVIOUS)
                .setPlayerCommand(Player.COMMAND_SEEK_TO_PREVIOUS)
                .setDisplayName(context.getString(R.string.player_previous_track))
                .setSlots(CommandButton.SLOT_BACK)
                .build(),
            button(ACTION_DISLIKE, CommandButton.ICON_UNDEFINED,
                if (preference == "DISLIKED") R.drawable.ic_autplay_heart_off_filled else R.drawable.ic_autplay_heart_off,
                R.string.action_dislike),
            button(ACTION_LIKE, if (preference == "LIKED") CommandButton.ICON_HEART_FILLED else CommandButton.ICON_HEART_UNFILLED,
                if (preference == "LIKED") R.drawable.ic_autplay_favorite_filled else R.drawable.ic_autplay_favorite,
                R.string.action_like),
        )
    }
}
