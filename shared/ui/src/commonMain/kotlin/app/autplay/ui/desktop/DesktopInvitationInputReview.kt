package app.autplay.ui.desktop

/** Process-only explicit enrollment review. It contains no invitation or session credential. */
public class DesktopInvitationInputReview(
    public val id: String,
    public val accountLabel: String,
    public val serverFingerprint: String,
    public val expiresLabel: String,
) {
    override fun toString(): String = "DesktopInvitationInputReview(<redacted>)"
}
