plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

val syncPythonSources by tasks.registering(Copy::class) {
    description = "Copies the upstream proxy/ package and Android-specific overrides into the Chaquopy source dir"
    from("../../proxy") {
        into("proxy")
    }
    from("src/main/python-overrides") {
        into("proxy")
    }
    from("src/main/python-sources")
    into("src/main/python")
    exclude("**/__pycache__/**")
}

tasks.named("preBuild") {
    dependsOn(syncPythonSources)
}

android {
    namespace = "com.tgwsproxy"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.tgwsproxy"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

        ndk {
            abiFilters += listOf("arm64-v8a", "armeabi-v7a", "x86_64")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

chaquopy {
    defaultConfig {
        version = "3.11"
        pip {
            install("certifi")
            install("httpx[http2]==0.28.1")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
}
