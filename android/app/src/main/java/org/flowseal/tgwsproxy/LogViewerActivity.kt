package org.flowseal.tgwsproxy

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import org.flowseal.tgwsproxy.databinding.ActivityLogViewerBinding
import java.io.File

class LogViewerActivity : AppCompatActivity() {
    private lateinit var binding: ActivityLogViewerBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityLogViewerBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.refreshLogsButton.setOnClickListener { renderLog() }
        binding.closeLogsButton.setOnClickListener { finish() }

        renderLog()
    }

    override fun onResume() {
        super.onResume()
        renderLog()
    }

    private fun renderLog() {
        val logFile = File(filesDir, "tg-ws-proxy/proxy.log")
        binding.logPathValue.text = logFile.absolutePath
        binding.logContentValue.text = readLogTail(logFile)
    }

    private fun readLogTail(logFile: File, maxChars: Int = 40000): String {
        if (!logFile.isFile) {
            return getString(R.string.logs_empty)
        }

        val text = runCatching {
            logFile.readText(Charsets.UTF_8)
        }.getOrElse { error ->
            return getString(R.string.logs_read_failed, error.message ?: error.javaClass.simpleName)
        }

        if (text.isBlank()) {
            return getString(R.string.logs_empty)
        }
        if (text.length <= maxChars) {
            return text
        }

        return getString(R.string.logs_truncated_prefix) + "\n\n" + text.takeLast(maxChars)
    }
}
