package org.flowseal.tgwsproxy

data class ProxyConfig(
    val host: String = DEFAULT_HOST,
    val portText: String = DEFAULT_PORT.toString(),
    val dcIpText: String = DEFAULT_DC_IP_LINES.joinToString("\n"),
    val logMaxMbText: String = formatDecimal(DEFAULT_LOG_MAX_MB),
    val bufferKbText: String = DEFAULT_BUFFER_KB.toString(),
    val poolSizeText: String = DEFAULT_POOL_SIZE.toString(),
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

        val logMaxMbValue = logMaxMbText.trim().toDoubleOrNull()
            ?: return ValidationResult(
                errorMessage = "Размер лог-файла должен быть числом."
            )
        if (logMaxMbValue <= 0.0) {
            return ValidationResult(
                errorMessage = "Размер лог-файла должен быть больше нуля."
            )
        }

        val bufferKbValue = bufferKbText.trim().toIntOrNull()
            ?: return ValidationResult(
                errorMessage = "Буфер сокета должен быть целым числом."
            )
        if (bufferKbValue < 4) {
            return ValidationResult(
                errorMessage = "Буфер сокета должен быть не меньше 4 KB."
            )
        }

        val poolSizeValue = poolSizeText.trim().toIntOrNull()
            ?: return ValidationResult(
                errorMessage = "Размер WS pool должен быть целым числом."
            )
        if (poolSizeValue < 0) {
            return ValidationResult(
                errorMessage = "Размер WS pool не может быть отрицательным."
            )
        }

        return ValidationResult(
            normalized = NormalizedProxyConfig(
                host = hostValue,
                port = portValue,
                dcIpList = lines,
                logMaxMb = logMaxMbValue,
                bufferKb = bufferKbValue,
                poolSize = poolSizeValue,
                verbose = verbose,
            )
        )
    }

    companion object {
        const val DEFAULT_HOST = "127.0.0.1"
        const val DEFAULT_PORT = 1080
        const val DEFAULT_LOG_MAX_MB = 5.0
        const val DEFAULT_BUFFER_KB = 256
        const val DEFAULT_POOL_SIZE = 4
        val DEFAULT_DC_IP_LINES = listOf(
            "2:149.154.167.220",
            "4:149.154.167.220",
        )

        fun formatDecimal(value: Double): String {
            return if (value % 1.0 == 0.0) {
                value.toInt().toString()
            } else {
                value.toString()
            }
        }

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
    val logMaxMb: Double,
    val bufferKb: Int,
    val poolSize: Int,
    val verbose: Boolean,
)
