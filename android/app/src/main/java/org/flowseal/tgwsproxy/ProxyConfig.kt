package org.flowseal.tgwsproxy

data class ProxyConfig(
    val host: String = DEFAULT_HOST,
    val portText: String = DEFAULT_PORT.toString(),
    val dcIpText: String = DEFAULT_DC_IP_LINES.joinToString("\n"),
    val verbose: Boolean = false,
) {
    fun validate(): ValidationResult {
        val hostValue = host.trim()
        if (!isIpv4Address(hostValue)) {
            return ValidationResult(errorMessage = "IP-адрес прокси указан некорректно.")
        }

        val portValue = portText.trim().toIntOrNull()
            ?: return ValidationResult(errorMessage = "Порт должен быть числом.")
        if (portValue !in 1..65535) {
            return ValidationResult(errorMessage = "Порт должен быть в диапазоне 1-65535.")
        }

        val lines = dcIpText
            .lineSequence()
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .toList()

        if (lines.isEmpty()) {
            return ValidationResult(errorMessage = "Добавьте хотя бы один DC:IP маппинг.")
        }

        for (line in lines) {
            val parts = line.split(":", limit = 2)
            val dcValue = parts.firstOrNull()?.toIntOrNull()
            val ipValue = parts.getOrNull(1)?.trim().orEmpty()
            if (parts.size != 2 || dcValue == null || !isIpv4Address(ipValue)) {
                return ValidationResult(errorMessage = "Строка \"$line\" должна быть в формате DC:IP.")
            }
        }

        return ValidationResult(
            normalized = NormalizedProxyConfig(
                host = hostValue,
                port = portValue,
                dcIpList = lines,
                verbose = verbose,
            )
        )
    }

    companion object {
        const val DEFAULT_HOST = "127.0.0.1"
        const val DEFAULT_PORT = 1080
        val DEFAULT_DC_IP_LINES = listOf(
            "2:149.154.167.220",
            "4:149.154.167.220",
        )

        private fun isIpv4Address(value: String): Boolean {
            val octets = value.split(".")
            if (octets.size != 4) {
                return false
            }

            return octets.all { octet ->
                octet.isNotEmpty() &&
                    octet.length <= 3 &&
                    octet.all(Char::isDigit) &&
                    octet.toIntOrNull() in 0..255
            }
        }
    }
}

data class ValidationResult(
    val normalized: NormalizedProxyConfig? = null,
    val errorMessage: String? = null,
)

data class NormalizedProxyConfig(
    val host: String,
    val port: Int,
    val dcIpList: List<String>,
    val verbose: Boolean,
)
