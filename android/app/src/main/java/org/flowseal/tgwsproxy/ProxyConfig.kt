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
    val cfproxyUserDomainEnabled: Boolean = cfproxyUserDomainText.isNotBlank(),
    val cfproxyWorkerDomainText: String = "",
    val cfproxyWorkerEnabled: Boolean = false,
    val noSecure: Boolean = false,
    val fakeTlsDomain: String = "",
    val forceTestDc: Boolean = false,
    val proxyProtocol: Boolean = false,
    val language: String = "ru",
    val checkUpdates: Boolean = false,
    val verbose: Boolean = false,
) {
    fun validate(): ValidationResult {
        fun error(ru: String, en: String): ValidationResult =
            ValidationResult(errorMessage = if (language == "en") en else ru)

        val hostValue = host.trim()
        if (!isIpv4Address(hostValue)) {
            return error("IP-адрес прокси указан некорректно.", "Proxy IP address is invalid.")
        }

        val portValue = portText.trim().toIntOrNull()
            ?: return error("Порт должен быть числом.", "Port must be a number.")
        if (portValue !in 1..65535) {
            return error("Порт должен быть в диапазоне 1-65535.", "Port must be in the range 1-65535.")
        }

        val secretValue = secretText.trim().lowercase()
        if (secretValue.length != 32 || !secretValue.all { it in "0123456789abcdef" }) {
            return error("MTProto secret должен содержать ровно 32 hex-символа.",
                "MTProto secret must contain exactly 32 hex characters.")
        }

        val lines = dcIpText
            .lineSequence()
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .toList()

        if (lines.isEmpty()) {
            return error("Добавьте хотя бы один DC:IP маппинг.", "Add at least one DC:IP mapping.")
        }

        for (line in lines) {
            val parts = line.split(":", limit = 2)
            val dcValue = parts.firstOrNull()?.toIntOrNull()
            val ipValue = parts.getOrNull(1)?.trim().orEmpty()
            if (parts.size != 2 || dcValue == null || !isIpv4Address(ipValue)) {
                return error("Строка \"$line\" должна быть в формате DC:IP.",
                    "Line \"$line\" must use the DC:IP format.")
            }
        }

        val appearanceValue = normalizeAppearance(appearance)

        val logMaxMbValue = logMaxMbText.trim().toDoubleOrNull()
            ?: return error("Размер лог-файла должен быть числом.", "Log size must be a number.")
        if (logMaxMbValue <= 0.0) {
            return error("Размер лог-файла должен быть больше нуля.", "Log size must be greater than zero.")
        }

        val bufferKbValue = bufferKbText.trim().toIntOrNull()
            ?: return error("Буфер сокета должен быть целым числом.", "Socket buffer must be an integer.")
        if (bufferKbValue < 4) {
            return error("Буфер сокета должен быть не меньше 4 KB.", "Socket buffer must be at least 4 KB.")
        }

        val poolSizeValue = poolSizeText.trim().toIntOrNull()
            ?: return error("Размер WS pool должен быть целым числом.", "WS pool size must be an integer.")
        if (poolSizeValue < 0) {
            return error("Размер WS pool не может быть отрицательным.", "WS pool size cannot be negative.")
        }

        val cfproxyValue = cfproxy
        val cfproxyPriorityValue = cfproxyPriority
        val cfproxyUserDomainValue = cfproxyUserDomainText.trim()
        val userDomains = splitDomains(cfproxyUserDomainValue)
        val workerDomains = splitDomains(cfproxyWorkerDomainText)
        val activeUserDomains = if (cfproxy && cfproxyUserDomainEnabled) userDomains else emptyList()
        val activeWorkerDomains = if (cfproxyWorkerEnabled) workerDomains else emptyList()
        if ((activeUserDomains + activeWorkerDomains).any { !isHostname(it) }) {
            return error("CfProxy domain должен быть доменным именем без схемы и пути.",
                "CfProxy domain must be a hostname without scheme or path.")
        }
        if (fakeTlsDomain.isNotBlank() && !isHostname(fakeTlsDomain.trim())) {
            return error("Fake TLS domain должен быть доменным именем без схемы и пути.",
                "Fake TLS domain must be a hostname without scheme or path.")
        }
        if (fakeTlsDomain.any { it.code > 127 }) {
            return error("Fake TLS domain должен содержать только ASCII-символы.",
                "Fake TLS domain must contain only ASCII characters.")
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
                cfproxyUserDomains = userDomains,
                cfproxyUserDomainEnabled = cfproxyUserDomainEnabled,
                cfproxyWorkerDomains = workerDomains,
                cfproxyWorkerEnabled = cfproxyWorkerEnabled,
                noSecure = noSecure,
                fakeTlsDomain = fakeTlsDomain.trim(),
                forceTestDc = forceTestDc,
                proxyProtocol = proxyProtocol,
                language = if (language == "en") "en" else "ru",
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

        private fun splitDomains(value: String): List<String> = value
            .split(Regex("[,;\\r\\n]+"))
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .distinct()

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
    val cfproxyUserDomains: List<String> = emptyList(),
    val cfproxyUserDomainEnabled: Boolean = false,
    val cfproxyWorkerDomains: List<String> = emptyList(),
    val cfproxyWorkerEnabled: Boolean = false,
    val noSecure: Boolean = false,
    val fakeTlsDomain: String = "",
    val forceTestDc: Boolean = false,
    val proxyProtocol: Boolean = false,
    val language: String = "ru",
    val checkUpdates: Boolean,
    val verbose: Boolean,
)
