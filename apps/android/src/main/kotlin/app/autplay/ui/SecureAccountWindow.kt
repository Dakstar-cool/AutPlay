package app.autplay.ui

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.view.Window
import android.view.WindowManager
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.platform.LocalContext
import java.util.WeakHashMap

/** Multiple simultaneous password/QR surfaces share one claim and preserve the original flag. */
@Composable
internal fun SecureAccountWindow(enabled: Boolean) {
    val window = LocalContext.current.accountActivity()?.window
    DisposableEffect(window, enabled) {
        if (!enabled || window == null) return@DisposableEffect onDispose {}
        SecureAccountWindows.acquire(window)
        onDispose { SecureAccountWindows.release(window) }
    }
}

private object SecureAccountWindows {
    private class Claim(var count: Int, val originallySecure: Boolean)
    private val claims = WeakHashMap<Window, Claim>()
    fun acquire(window: Window) = synchronized(claims) {
        val claim = claims.getOrPut(window) { Claim(0, window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE != 0) }
        claim.count++
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
    }
    fun release(window: Window) = synchronized(claims) {
        val claim = claims[window] ?: return@synchronized
        claim.count--
        if (claim.count == 0) {
            claims.remove(window)
            if (!claim.originallySecure) window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
        }
    }
}

private fun Context.accountActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.accountActivity()
    else -> null
}
