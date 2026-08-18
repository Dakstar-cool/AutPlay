package app.autplay

import org.junit.Assert.assertEquals
import org.junit.Test

class BootstrapTest {
    @Test
    fun bootstrapLabelUsesProductName() {
        assertEquals("AutPlay", BOOTSTRAP_LABEL)
    }
}
