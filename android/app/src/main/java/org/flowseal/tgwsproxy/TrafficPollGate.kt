package org.flowseal.tgwsproxy

internal class TrafficPollGate {
    private val lock = Any()
    private var generation = 0L

    fun activate(action: (Long) -> Unit) = synchronized(lock) {
        action(++generation)
    }

    fun invalidate(action: () -> Unit = {}) = synchronized(lock) {
        ++generation
        action()
    }

    fun <T, R> poll(
        ticket: Long,
        isActive: () -> Boolean,
        read: () -> T,
        apply: (Result<T>) -> R,
    ): R? {
        val result = runCatching(read)
        return synchronized(lock) {
            if (generation == ticket && isActive()) apply(result) else null
        }
    }
}
