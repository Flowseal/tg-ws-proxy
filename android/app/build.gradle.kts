import org.gradle.api.tasks.Sync
import org.gradle.api.GradleException
import java.io.File

plugins {
    id("com.android.application")
    id("com.chaquo.python")
    id("org.jetbrains.kotlin.android")
}

data class ReleaseSigningEnv(
    val keystoreFile: File,
    val storePassword: String,
    val keyAlias: String,
    val keyPassword: String,
)

fun requiredEnv(name: String): String {
    return System.getenv(name)?.takeIf { it.isNotBlank() }
        ?: throw GradleException("Missing required environment variable: $name")
}

fun loadReleaseSigningEnv(releaseSigningRequested: Boolean): ReleaseSigningEnv? {
    val keystorePath = System.getenv("ANDROID_KEYSTORE_FILE")?.takeIf { it.isNotBlank() }
    val anySigningEnvProvided = listOf(
        keystorePath,
        System.getenv("ANDROID_KEYSTORE_PASSWORD"),
        System.getenv("ANDROID_KEY_ALIAS"),
        System.getenv("ANDROID_KEY_PASSWORD"),
    ).any { !it.isNullOrBlank() }

    if (!releaseSigningRequested && !anySigningEnvProvided) {
        return null
    }

    val keystoreFile = File(requiredEnv("ANDROID_KEYSTORE_FILE"))
    if (!keystoreFile.isFile) {
        throw GradleException("ANDROID_KEYSTORE_FILE does not exist: ${keystoreFile.absolutePath}")
    }

    return ReleaseSigningEnv(
        keystoreFile = keystoreFile,
        storePassword = requiredEnv("ANDROID_KEYSTORE_PASSWORD"),
        keyAlias = requiredEnv("ANDROID_KEY_ALIAS"),
        keyPassword = requiredEnv("ANDROID_KEY_PASSWORD"),
    )
}

val stagedPythonSourcesDir = layout.buildDirectory.dir("generated/chaquopy/python")
val stagePythonSources by tasks.registering(Sync::class) {
    from(rootProject.projectDir.resolve("../proxy")) {
        into("proxy")
    }
    into(stagedPythonSourcesDir)
}
val releaseSigningRequested = gradle.startParameter.taskNames.any {
    it.contains("release", ignoreCase = true)
}
val releaseSigningEnv = loadReleaseSigningEnv(releaseSigningRequested)

android {
    namespace = "org.flowseal.tgwsproxy"
    compileSdk = 34

    defaultConfig {
        applicationId = "org.flowseal.tgwsproxy"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        if (releaseSigningEnv != null) {
            create("release") {
                storeFile = releaseSigningEnv.keystoreFile
                storePassword = releaseSigningEnv.storePassword
                keyAlias = releaseSigningEnv.keyAlias
                keyPassword = releaseSigningEnv.keyPassword
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            if (releaseSigningEnv != null) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        viewBinding = true
    }
}

chaquopy {
    defaultConfig {
        version = "3.12"
    }
    sourceSets {
        getByName("main") {
            srcDir("src/main/python")
            srcDir(stagePythonSources)
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.lifecycle:lifecycle-service:2.8.6")
    implementation("com.google.android.material:material:1.12.0")

    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
}
