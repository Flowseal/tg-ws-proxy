plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.amurcanov.tgwsproxy"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.amurcanov.tgwsproxy"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.4"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables {
            useSupportLibrary = true
        }
        
        ndk {
            abiFilters.add("arm64-v8a")
        }
    }

    signingConfigs {
        val keystoreFile = file("amurcanov.jks")
        if (keystoreFile.exists()) {
            create("release") {
                storeFile = keystoreFile
                // Берем пароли из локальных переменных среды или файла
                storePassword = System.getenv("KEYSTORE_PASSWORD") ?: "flowseal-fork"
                keyAlias = "amurcanov"
                keyPassword = System.getenv("KEY_PASSWORD") ?: "flowseal-fork"
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.findByName("release")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }
    kotlinOptions {
        jvmTarget = "1.8"
    }
    buildFeatures {
        compose = true
    }
    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.8"
    }
    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
    sourceSets {
        getByName("main") {
            jniLibs.srcDir("src/main/jniLibs")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.7.0")
    implementation("androidx.activity:activity-compose:1.8.2")
    implementation(platform("androidx.compose:compose-bom:2024.01.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")

    // JNA for easy C-shared library calls
    implementation("net.java.dev.jna:jna:5.14.0@aar")
    debugImplementation("com.squareup.leakcanary:leakcanary-android:2.14")
    implementation("androidx.compose.material:material-icons-extended")
}
