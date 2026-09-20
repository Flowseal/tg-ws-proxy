package org.flowseal.tgwsproxy

object UpstreamMode {
    const val DIRECT = "telegram_ws_direct"

    data class Option(
        val value: String,
        val labelResId: Int,
    )

    val options = listOf(Option(DIRECT, 0))

    @Suppress("UNUSED_PARAMETER")
    fun normalize(value: String?): String = DIRECT

    fun summary(): String = "Direct only"
}
