package org.flowseal.tgwsproxy

import java.security.SecureRandom

data class ProxyConfig(
    val host: String = DEFAULT_HOST,
    val portText: String = DEFAULT_PORT.toString(),
    val secretText: String = DEFAULT_SECRET,
    val dcIpText: String = DEFAULT_DC_IP_LINES.joinToString("\n"),
    val appearance: String = DEFAULT_APPEARANCE,
    val logMaxMbText: String = formatDecimal(DEFAULT_LOG_MAX_MB),
    val bufferKbText: String = DEFAULT_BUFFER_KB.toString(),
    val poolSizeText: String = DEFAULT_POOL_SIZE.toString(),
    val cfproxy: Boolean = DEFAULT_CFPROXY,
    val cfproxyPriority: Boolean = DEFAULT_CFPROXY_PRIORITY,
    val cfproxyUserDomainText: String = DEFAULT_CFPROXY_USER_DOMAIN,
    val checkUpdates: Boolean = false,
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

        val secretValue = secretText.trim().lowercase()
        if (secretValue.length != 32 || !secretValue.all { it in "0123456789abcdef" }) {
            return ValidationResult(
                errorMessage = "MTProto secret должен содержать ровно 32 hex-символа."
            )
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

        val appearanceValue = normalizeAppearance(appearance)

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

        val cfproxyValue = cfproxy
        val cfproxyPriorityValue = cfproxyPriority
        val cfproxyUserDomainValue = cfproxyUserDomainText.trim()
        if (
            cfproxyValue &&
            cfproxyUserDomainValue.isNotEmpty() &&
            !isHostname(cfproxyUserDomainValue)
        ) {
            return ValidationResult(
                errorMessage = "CfProxy domain должен быть доменным именем без схемы и пути."
            )
        }

        return ValidationResult(
            normalized = NormalizedProxyConfig(
                host = hostValue,
                port = portValue,
                secret = secretValue,
                dcIpList = lines,
                appearance = appearanceValue,
                logMaxMb = logMaxMbValue,
                bufferKb = bufferKbValue,
                poolSize = poolSizeValue,
                cfproxy = cfproxyValue,
                cfproxyPriority = cfproxyPriorityValue,
                cfproxyUserDomain = cfproxyUserDomainValue,
                checkUpdates = checkUpdates,
                verbose = verbose,
            )
        )
    }

    companion object {
        const val DEFAULT_HOST = "127.0.0.1"
        const val DEFAULT_PORT = 1443
        const val DEFAULT_APPEARANCE = "auto"
        const val DEFAULT_LOG_MAX_MB = 5.0
        const val DEFAULT_BUFFER_KB = 256
        const val DEFAULT_POOL_SIZE = 4
        const val DEFAULT_CFPROXY = true
        const val DEFAULT_CFPROXY_PRIORITY = true
        const val DEFAULT_CFPROXY_USER_DOMAIN = ""
        val DEFAULT_SECRET = generateSecret()
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

        fun normalizeAppearance(value: String?): String {
            return when (value?.trim()?.lowercase()) {
                "light" -> "light"
                "dark" -> "dark"
                else -> "auto"
            }
        }

        fun generateSecretForUi(): String {
            return generateSecret()
        }

        private fun generateSecret(): String {
            val bytes = ByteArray(16)
            SecureRandom().nextBytes(bytes)
            return bytes.joinToString(separator = "") { "%02x".format(it) }
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

        private fun isHostname(value: String): Boolean {
            if (
                value.contains("://") ||
                value.contains("/") ||
                value.contains("\\") ||
                value.any(Char::isWhitespace)
            ) {
                return false
            }
            val labels = value.split(".")
            if (labels.isEmpty()) {
                return false
            }

            return labels.all { label ->
                label.isNotEmpty() &&
                    label.length <= 63 &&
                    label.first().isLetterOrDigit() &&
                    label.last().isLetterOrDigit() &&
                    label.all { it.isLetterOrDigit() || it == '-' }
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
    val secret: String,
    val dcIpList: List<String>,
    val appearance: String,
    val logMaxMb: Double,
    val bufferKb: Int,
    val poolSize: Int,
    val cfproxy: Boolean,
    val cfproxyPriority: Boolean,
    val cfproxyUserDomain: String,
    val checkUpdates: Boolean,
    val verbose: Boolean,
)
