package org.flowseal.tgwsproxy

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class PackagedPythonSmokeTest {
    @Test
    fun bridge_import_and_python_aes_vector() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(context))
        }
        val python = Python.getInstance()
        python.getModule("android_proxy_bridge")
        val bytes = python.getModule("builtins").get("bytes")!!
        val key = bytes.callAttr("fromhex", "2b7e151628aed2a6abf7158809cf4f3c")
        val iv = bytes.callAttr("fromhex", "f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff")
        val plain = bytes.callAttr("fromhex", "6bc1bee22e409f96e93d7e117393172a")
        val aes = python.getModule("proxy._aes")
        val algorithm = aes.get("algorithms")!!.get("AES")!!.call(key)
        val mode = aes.get("modes")!!.get("CTR")!!.call(iv)
        val cipher = aes.get("Cipher")!!.call(algorithm, mode)
        assertEquals("proxy._aes", aes.get("Cipher")!!.get("__module__").toString())
        val encryptor = cipher.callAttr("encryptor")
        assertEquals("proxy.crypto_backend", encryptor.get("__class__")!!.get("__module__").toString())
        assertEquals(
            "874d6191b620e3261bef6864990db6ce",
            encryptor.callAttr("update", plain).callAttr("hex").toString(),
        )
    }
}
