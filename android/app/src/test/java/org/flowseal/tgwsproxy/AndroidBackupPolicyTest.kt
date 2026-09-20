package org.flowseal.tgwsproxy

import java.nio.file.Files
import java.nio.file.Paths
import org.junit.Assert.assertTrue
import org.junit.Test
import javax.xml.parsers.DocumentBuilderFactory

class AndroidBackupPolicyTest {
    @Test
    fun appDisablesBackupOfStoredSecrets() {
        val manifest = String(Files.readAllBytes(Paths.get("src/main/AndroidManifest.xml")))
        assertTrue(manifest.contains("android:allowBackup=\"false\""))
        assertTrue(manifest.contains("android:fullBackupContent=\"false\""))
        assertTrue(manifest.contains("android:dataExtractionRules=\"@xml/data_extraction_rules\""))
    }

    @Test
    fun extractionRulesExcludeSecretPreferencesFromCloudAndDeviceTransfer() {
        val path = Paths.get("src/main/res/xml/data_extraction_rules.xml")
        val document = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(path.toFile())
        for (section in listOf("cloud-backup", "device-transfer")) {
            val rules = document.getElementsByTagName(section).item(0)
            assertTrue("Missing $section", rules != null)
            val excludes = (rules as org.w3c.dom.Element).getElementsByTagName("exclude")
            assertTrue((0 until excludes.length).any { index ->
                val exclude = excludes.item(index) as org.w3c.dom.Element
                exclude.getAttribute("domain") == "sharedpref" &&
                    exclude.getAttribute("path") == "proxy_settings.xml"
            })
        }
    }
}
