package app.autplay.application.server

import org.junit.Assert.*
import org.junit.Test

class InternetSourceSearchSessionTest {
    @Test fun lostTransportResponseReplaysSameImmutableAdmissionAndRejectsLateResponse() {
        var ids = 0
        val session = InternetSourceSearchSession("Artist Song (Live)", "context-one") { "operation-${++ids}" }
        val original = session.current
        val replay = session.retryTransport()
        assertEquals(original.operationId, replay.operationId)
        assertEquals(original.query, replay.query)
        assertEquals(original.catalogueContextId, replay.catalogueContextId)
        assertFalse(session.accepts(original))
        assertTrue(session.accepts(replay))
        assertEquals(1, ids)
    }

    @Test fun terminalAcquisitionRetryCreatesFreshOperationWithoutRebindingSelectedContext() {
        var ids = 0
        val session = InternetSourceSearchSession("Artist Song (Live)", "context-one") { "operation-${++ids}" }
        val failed = session.current
        val fresh = session.retryTerminalAcquisition()
        assertNotEquals(failed.operationId, fresh.operationId)
        assertEquals(failed.query, fresh.query)
        assertEquals(failed.catalogueContextId, fresh.catalogueContextId)
        assertFalse(session.accepts(failed))
        val replay = session.retryTransport()
        assertEquals(fresh.operationId, replay.operationId)
        assertEquals("context-one", replay.catalogueContextId)
        assertFalse(session.accepts(fresh))
        assertTrue(session.accepts(replay))
    }

    @Test fun freshReceiptStartsIndependentOperationAndCannotAcceptPriorReceiptAttempt() {
        var ids = 0
        val old = InternetSourceSearchSession("Artist Song", "old-context") { "operation-${++ids}" }
        val renewed = InternetSourceSearchSession("Artist Song", "fresh-context") { "operation-${++ids}" }
        assertNotEquals(old.current.operationId, renewed.current.operationId)
        assertEquals("old-context", old.current.catalogueContextId)
        assertEquals("fresh-context", renewed.current.catalogueContextId)
        assertFalse(renewed.accepts(old.current))
        assertEquals(old.current.operationId, old.retryTransport().operationId)
    }
}
