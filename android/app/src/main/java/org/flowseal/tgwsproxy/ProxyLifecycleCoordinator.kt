package org.flowseal.tgwsproxy

import java.util.concurrent.Executor

internal class ProxyLifecycleCoordinator(
    private val executor: Executor,
    private val backend: Backend,
    private val listener: Listener,
) {
    interface Backend {
        fun start(config: NormalizedProxyConfig)
        fun stop()
    }

    interface Listener {
        fun started(config: NormalizedProxyConfig)
        fun stopped()
        fun failed(error: Throwable)
    }

    private val lock = Any()
    private var generation = 0L
    private var destroyed = false

    fun start(config: NormalizedProxyConfig, restart: Boolean = false) {
        val ticket = synchronized(lock) {
            if (destroyed) return
            ++generation
        }
        executor.execute {
            if (!isCurrentStart(ticket)) return@execute
            if (restart) {
                val stopError = runCatching { backend.stop() }.exceptionOrNull()
                if (stopError != null) {
                    synchronized(lock) { listener.failed(stopError) }
                    return@execute
                }
            }
            if (!isCurrentStart(ticket)) return@execute
            val startError = runCatching { backend.start(config) }.exceptionOrNull()
            if (startError == null) {
                publishStart(ticket) { started(config) }
            } else {
                publishStart(ticket) { failed(startError) }
            }
        }
    }

    fun stop() {
        val ticket = synchronized(lock) {
            if (destroyed) return
            ++generation
        }
        executor.execute { stopAndPublish(ticket) }
    }

    fun destroy() {
        val ticket = synchronized(lock) {
            if (destroyed) return
            destroyed = true
            ++generation
        }
        executor.execute { stopAndPublish(ticket) }
    }

    private fun stopAndPublish(ticket: Long) {
        val error = runCatching { backend.stop() }.exceptionOrNull()
        synchronized(lock) {
            if (error != null) {
                listener.failed(error)
            } else if (generation == ticket) {
                listener.stopped()
            }
        }
    }

    private fun isCurrentStart(ticket: Long) = synchronized(lock) {
        !destroyed && generation == ticket
    }

    private fun publishStart(ticket: Long, event: Listener.() -> Unit) = synchronized(lock) {
        if (!destroyed && generation == ticket) listener.event()
    }

}
