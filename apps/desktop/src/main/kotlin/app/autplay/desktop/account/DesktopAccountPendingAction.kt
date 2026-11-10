package app.autplay.desktop.account

import app.autplay.application.securestate.SecureStage

public enum class DesktopPendingPurpose {
    BINDING, SESSION_GRANT, ROOT_ROTATION, ADMISSION, TRUSTED, SELF_SOURCE, SELF_RECIPIENT,
    RECOVERY_SOURCE, RECOVERY_PREVIEW, DELETION_SOURCE, DELETION_PREVIEW, PROFILE_COMMAND, UNSUPPORTED,
}
public class DesktopPendingHandle internal constructor(internal val slot: String, internal val valueId: String,
    internal val profile: String?, public val purpose: DesktopPendingPurpose) {
    override fun toString(): String = "DesktopPendingHandle(<redacted>)"
}
public data class DesktopPendingAction(public val handle: DesktopPendingHandle, public val stage: SecureStage)
