package org.flowseal.tgwsproxy

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CfProxyDiagnosticRequestTest {
    @Test
    fun diagnosticValidationUsesOnlyModeDomainsAndSecurity() {
        val worker = CfProxyDiagnosticRequest.fromForm(
            worker = true,
            customEnabled = true,
            customText = "bad/unused",
            workerEnabled = true,
            workerText = "one.example\ntwo.example",
            noSecure = true,
        )
        assertEquals(null, worker.error)
        val workerRequest = worker.request!!
        assertEquals("worker", workerRequest.mode)
        assertEquals(listOf("one.example", "two.example"), workerRequest.domains)
        assertTrue(workerRequest.noSecure)

        val auto = CfProxyDiagnosticRequest.fromForm(
            worker = false,
            customEnabled = false,
            customText = "bad/disabled",
            workerEnabled = true,
            workerText = "bad/unused",
            noSecure = false,
        )
        val autoRequest = auto.request!!
        assertEquals("auto", autoRequest.mode)
        assertTrue(autoRequest.domains.isEmpty())
        assertFalse(autoRequest.noSecure)
    }

    @Test
    fun activeDomainErrorsAreModeSpecific() {
        val empty = CfProxyDiagnosticRequest.fromForm(
            worker = true, customEnabled = false, customText = "",
            workerEnabled = true, workerText = "", noSecure = false,
        )
        assertEquals(CfProxyDiagnosticError.WORKER_REQUIRED, empty.error)
        val malformed = CfProxyDiagnosticRequest.fromForm(
            worker = false, customEnabled = true, customText = "bad/path",
            workerEnabled = false, workerText = "", noSecure = false,
        )
        assertEquals(CfProxyDiagnosticError.INVALID_DOMAIN, malformed.error)
    }

    @Test
    fun testedDomainSnapshotDoesNotChangeWithFormText() {
        var formText = "original.example"
        val request = CfProxyDiagnosticRequest.fromForm(
            worker = false, customEnabled = true, customText = formText,
            workerEnabled = false, workerText = "", noSecure = true,
        ).request!!
        formText = "new.example"
        val state = CfProxyDiagnosticsState(request = request,
            result = CfProxyTestResult(mode = "custom", successCount = 1, totalCount = 6))
        assertEquals("original.example", state.request!!.domains.single())
        assertEquals("new.example", formText)
    }
}
