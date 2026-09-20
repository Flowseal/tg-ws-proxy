package org.flowseal.tgwsproxy

import java.nio.file.Files
import java.nio.file.Paths
import org.junit.Assert.assertTrue
import org.junit.Test

class AndroidBackupPolicyTest {
    @Test
    fun appDisablesBackupOfStoredSecrets() {
        val manifest = String(Files.readAllBytes(Paths.get("src/main/AndroidManifest.xml")))
        assertTrue(manifest.contains("android:allowBackup=\"false\""))
    }
}
