package org.flowseal.tgwsproxy

import java.util.ArrayDeque
import java.util.concurrent.Executor
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProxyLifecycleCoordinatorTest {
    private class QueuedExecutor : Executor {
        private val tasks = ArrayDeque<Runnable>()
        override fun execute(command: Runnable) { tasks.addLast(command) }
        fun drain() { while (tasks.isNotEmpty()) tasks.removeFirst().run() }
    }

    private val config = ProxyConfig().validate().normalized!!

    @Test
    fun stop_during_in_flight_start_never_publishes_running() {
        val executor = QueuedExecutor()
        val events = mutableListOf<String>()
        var stops = 0
        lateinit var coordinator: ProxyLifecycleCoordinator
        coordinator = ProxyLifecycleCoordinator(executor, object : ProxyLifecycleCoordinator.Backend {
            override fun start(config: NormalizedProxyConfig) { coordinator.stop() }
            override fun stop() { stops++ }
        }, object : ProxyLifecycleCoordinator.Listener {
            override fun started(config: NormalizedProxyConfig) { events += "running" }
            override fun stopped() { events += "stopped" }
            override fun failed(error: Throwable) { events += "failed" }
        })

        coordinator.start(config)
        executor.drain()

        assertEquals(listOf("stopped"), events)
        assertEquals(1, stops)
    }

    @Test
    fun stop_failure_is_published_as_failed_not_stopped() {
        val executor = QueuedExecutor()
        val events = mutableListOf<String>()
        val coordinator = ProxyLifecycleCoordinator(executor, object : ProxyLifecycleCoordinator.Backend {
            override fun start(config: NormalizedProxyConfig) = Unit
            override fun stop(): Unit = throw IllegalStateException("stop failed")
        }, object : ProxyLifecycleCoordinator.Listener {
            override fun started(config: NormalizedProxyConfig) { events += "running" }
            override fun stopped() { events += "stopped" }
            override fun failed(error: Throwable) { events += error.message.orEmpty() }
        })

        coordinator.start(config)
        executor.drain()
        coordinator.stop()
        executor.drain()

        assertEquals(listOf("running", "stop failed"), events)
    }

    @Test
    fun destroy_during_start_stops_backend_without_running_event() {
        val executor = QueuedExecutor()
        var running = false
        var stopped = false
        lateinit var coordinator: ProxyLifecycleCoordinator
        coordinator = ProxyLifecycleCoordinator(executor, object : ProxyLifecycleCoordinator.Backend {
            override fun start(config: NormalizedProxyConfig) { coordinator.destroy() }
            override fun stop() { stopped = true }
        }, object : ProxyLifecycleCoordinator.Listener {
            override fun started(config: NormalizedProxyConfig) { running = true }
            override fun stopped() = Unit
            override fun failed(error: Throwable) = Unit
        })

        coordinator.start(config)
        executor.drain()

        assertFalse(running)
        assertTrue(stopped)
    }

    @Test
    fun destroy_stop_failure_is_failed_not_stopped() {
        val executor = QueuedExecutor()
        val events = mutableListOf<String>()
        val coordinator = ProxyLifecycleCoordinator(executor, object : ProxyLifecycleCoordinator.Backend {
            override fun start(config: NormalizedProxyConfig) = Unit
            override fun stop(): Unit = throw IllegalStateException("destroy stop failed")
        }, object : ProxyLifecycleCoordinator.Listener {
            override fun started(config: NormalizedProxyConfig) { events += "running" }
            override fun stopped() { events += "stopped" }
            override fun failed(error: Throwable) { events += error.message.orEmpty() }
        })

        coordinator.start(config)
        executor.drain()
        coordinator.destroy()
        executor.drain()

        assertEquals(listOf("running", "destroy stop failed"), events)
    }

    @Test
    fun failed_restart_stop_does_not_start_new_backend() {
        val executor = QueuedExecutor()
        val events = mutableListOf<String>()
        var starts = 0
        val coordinator = ProxyLifecycleCoordinator(executor, object : ProxyLifecycleCoordinator.Backend {
            override fun start(config: NormalizedProxyConfig) { starts++ }
            override fun stop(): Unit = throw IllegalStateException("restart stop failed")
        }, object : ProxyLifecycleCoordinator.Listener {
            override fun started(config: NormalizedProxyConfig) { events += "running" }
            override fun stopped() { events += "stopped" }
            override fun failed(error: Throwable) { events += error.message.orEmpty() }
        })

        coordinator.start(config)
        executor.drain()
        coordinator.start(config, restart = true)
        executor.drain()

        assertEquals(1, starts)
        assertEquals(listOf("running", "restart stop failed"), events)
    }

    @Test
    fun destroy_cannot_hide_an_in_flight_stop_failure() {
        val executor = QueuedExecutor()
        val events = mutableListOf<String>()
        var stops = 0
        lateinit var coordinator: ProxyLifecycleCoordinator
        coordinator = ProxyLifecycleCoordinator(executor, object : ProxyLifecycleCoordinator.Backend {
            override fun start(config: NormalizedProxyConfig) = Unit
            override fun stop() {
                stops++
                if (stops == 1) {
                    coordinator.destroy()
                    throw IllegalStateException("first stop failed")
                }
            }
        }, object : ProxyLifecycleCoordinator.Listener {
            override fun started(config: NormalizedProxyConfig) { events += "running" }
            override fun stopped() { events += "stopped" }
            override fun failed(error: Throwable) { events += error.message.orEmpty() }
        })

        coordinator.start(config)
        executor.drain()
        coordinator.stop()
        executor.drain()

        assertTrue(events.contains("first stop failed"))
    }

    @Test
    fun stop_cannot_hide_an_in_flight_restart_stop_failure() {
        val executor = QueuedExecutor()
        val events = mutableListOf<String>()
        var stops = 0
        lateinit var coordinator: ProxyLifecycleCoordinator
        coordinator = ProxyLifecycleCoordinator(executor, object : ProxyLifecycleCoordinator.Backend {
            override fun start(config: NormalizedProxyConfig) = Unit
            override fun stop() {
                stops++
                if (stops == 1) {
                    coordinator.stop()
                    throw IllegalStateException("restart stop failed")
                }
            }
        }, object : ProxyLifecycleCoordinator.Listener {
            override fun started(config: NormalizedProxyConfig) { events += "running" }
            override fun stopped() { events += "stopped" }
            override fun failed(error: Throwable) { events += error.message.orEmpty() }
        })

        coordinator.start(config)
        executor.drain()
        coordinator.start(config, restart = true)
        executor.drain()

        assertTrue(events.contains("restart stop failed"))
    }
}
