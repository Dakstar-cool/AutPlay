package app.autplay.playback

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Bundle
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
        .build()

    @UnstableApi
    fun buttons(context: Context, entryId: String?, preference: String?, enabled: Boolean): List<CommandButton> {
        fun button(action: String, selected: Boolean, icon: Int, selectedIcon: Int, label: Int, slot: Int): CommandButton =
            CommandButton.Builder(if (selected) selectedIcon else icon)
                .setDisplayName(context.getString(label))
                .setSessionCommand(SessionCommand(action, Bundle().apply { putString(EXTRA_QUEUE_ENTRY_ID, entryId) }))
                // Platform media sessions project custom actions through the overflow slot.
                .setSlots(slot, CommandButton.SLOT_OVERFLOW)
                .setEnabled(enabled)
                .build()
        return listOf(
            button(ACTION_DISLIKE, preference == "DISLIKED", CommandButton.ICON_THUMB_DOWN_UNFILLED,
                CommandButton.ICON_THUMB_DOWN_FILLED, R.string.action_dislike, CommandButton.SLOT_BACK_SECONDARY),
            button(ACTION_LIKE, preference == "LIKED", CommandButton.ICON_THUMB_UP_UNFILLED,
                CommandButton.ICON_THUMB_UP_FILLED, R.string.action_like, CommandButton.SLOT_FORWARD_SECONDARY),
        )
    }
}
