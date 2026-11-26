package app.autplay.ui.desktop

import org.junit.Assert.assertEquals
import org.junit.Test

class DesktopConnectionStepTest {
    private val review = DesktopTransportReview("review", "https://api.example", "https://stream.example",
        "identity", null, null, apiUsesSystemTrust = true, streamUsesSystemTrust = true)

    @Test fun addressTrustAndLoginLeadToAConfirmedBinding() {
        val ready = DesktopAccountCondition.Ready
        assertEquals(DesktopConnectionStep.Address, DesktopConnectionView(ready).step)
        assertEquals(DesktopConnectionStep.Trust, DesktopConnectionView(ready, transportReview = review).step)
        assertEquals(DesktopConnectionStep.SignIn, DesktopConnectionView(ready, serverSelected = true).step)
        assertEquals(DesktopConnectionStep.Connected, DesktopConnectionView(ready, bindingId = "binding").step)
    }

    @Test fun errorsStayOnTheirStepAndServerChangesAlwaysRequireTrust() {
        val attention = DesktopAccountCondition.Attention
        assertEquals(DesktopConnectionStep.Address,
            DesktopConnectionView(attention, DesktopAccountIssue.ServerConnectionUnsupported).step)
        assertEquals(DesktopConnectionStep.SignIn,
            DesktopConnectionView(attention, DesktopAccountIssue.Authentication, serverSelected = true).step)
        assertEquals(DesktopConnectionStep.SignIn,
            DesktopConnectionView(attention, serverSelected = true, passwordLoginUnavailable = true).step)
        assertEquals(DesktopConnectionStep.Trust,
            DesktopConnectionView(attention, transportReview = review, bindingId = "previous").step)
        val pinned = DesktopTransportBootstrap("review", "https://api.example", "https://stream.example", null, null)
        assertEquals(DesktopConnectionStep.Trust,
            DesktopConnectionView(attention, transportBootstrap = pinned, serverSelected = true).step)
    }
}
