package app.autplay.application.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class SyncAttemptPolicyTest {
    @Test fun retryKeepsBoundedBackoffAndDeadLettersOnlyAfterLimit() {
        assertEquals(SyncAttemptPolicy.Decision("PENDING", 2_000), SyncAttemptPolicy.afterRetry(0, null))
        assertEquals(SyncAttemptPolicy.Decision("PENDING", 7_000), SyncAttemptPolicy.afterRetry(2, 7_000))
        assertEquals(SyncAttemptPolicy.Decision("DEAD_LETTER", 256_000), SyncAttemptPolicy.afterRetry(7, null))
    }
}
