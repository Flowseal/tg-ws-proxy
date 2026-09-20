package org.flowseal.tgwsproxy

object NotificationSummary {
    const val FALLBACK_CFPROXY = "Fallback: CfProxy"
    const val FALLBACK_CFPROXY_PRIO = "Fallback: CfProxy (prio)"
    const val FALLBACK_CFPROXY_CUSTOM = "Fallback: CfProxy custom"
    const val FALLBACK_TCP = "Fallback: TCP"

    fun formatFallbackSummary(config: NormalizedProxyConfig): String {
        if (!config.cfproxy) {
            return FALLBACK_TCP
        }
        if (config.cfproxyUserDomain.isNotBlank()) {
            return FALLBACK_CFPROXY_CUSTOM
        }
        if (config.cfproxyPriority) {
            return FALLBACK_CFPROXY_PRIO
        }
        return FALLBACK_CFPROXY
    }
}
