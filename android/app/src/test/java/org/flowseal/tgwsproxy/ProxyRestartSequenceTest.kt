package org.flowseal.tgwsproxy

import org.junit.Assert.assertEquals
import org.junit.Test

class ProxyRestartSequenceTest {
    @Test
    fun old_poll_is_invalidated_before_starting_is_published() {
        val config = ProxyConfig().validate().normalized!!
        val gate = TrafficPollGate()
        var oldTicket = 0L
        gate.activate { oldTicket = it }
        val actions = mutableListOf<String>()

        ProxyRestartSequence.run(
            config,
            clearError = { actions += "clear error" },
            invalidateTraffic = {
                actions += "invalidate traffic"
                gate.invalidate()
            },
            publishStarting = {
                actions += "publish starting"
                gate.poll(oldTicket, { true }, {
                    throw IllegalStateException("old poll failed")
                }) { actions += "stale failure" }
            },
            requestRestart = { actions += "request restart" },
        )

        assertEquals(
            listOf("invalidate traffic", "clear error", "publish starting", "request restart"),
            actions,
        )
    }
}
