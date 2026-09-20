package org.flowseal.tgwsproxy

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class TrafficPollGateTest {
    @Test
    fun old_success_completing_after_restart_cannot_mutate_new_sample_or_notification() {
        val gate = TrafficPollGate()
        var oldTicket = 0L
        gate.activate { oldTicket = it }
        var sample = "none"
        val notifications = mutableListOf<String>()

        val oldResult = gate.poll(oldTicket, { true }, {
            gate.invalidate()
            gate.activate { newTicket ->
                gate.poll(newTicket, { true }, { "new" }) { result ->
                    sample = result.getOrThrow()
                    notifications += sample
                }
            }
            "old"
        }) { result ->
            sample = result.getOrThrow()
            notifications += sample
        }

        assertNull(oldResult)
        assertEquals("new", sample)
        assertEquals(listOf("new"), notifications)
    }

    @Test
    fun cancelled_old_failure_completing_after_stop_cannot_publish_failed() {
        val gate = TrafficPollGate()
        var ticket = 0L
        gate.activate { ticket = it }
        val states = mutableListOf<String>()

        val result = gate.poll(ticket, { true }, {
            gate.invalidate()
            throw IllegalStateException("stale Python error")
        }) { stats ->
            states += if (stats.isFailure) "failed" else "running"
        }

        assertNull(result)
        assertEquals(emptyList<String>(), states)
    }

    @Test
    fun inactive_job_cannot_apply_result_even_with_current_generation() {
        val gate = TrafficPollGate()
        var ticket = 0L
        gate.activate { ticket = it }
        var sample = "none"

        val result = gate.poll(ticket, { false }, { "old" }) { stats ->
            sample = stats.getOrThrow()
        }

        assertNull(result)
        assertEquals("none", sample)
    }
}
