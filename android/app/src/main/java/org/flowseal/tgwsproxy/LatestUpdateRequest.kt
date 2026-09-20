package org.flowseal.tgwsproxy

internal class LatestUpdateRequest {
    private var generation = 0L

    fun begin(): Long = ++generation

    fun isCurrent(request: Long): Boolean = request == generation

    fun invalidate() {
        ++generation
    }
}
