pluginManagement {
    repositories {
        val localChaquopyRepo = file(System.getenv("LOCAL_CHAQUOPY_REPO") ?: ".m2-chaquopy")
        if (localChaquopyRepo.isDirectory) {
            maven(url = localChaquopyRepo.toURI())
        }
        maven("https://chaquo.com/maven")
        gradlePluginPortal()
        google()
        mavenCentral()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        val localChaquopyRepo = file(System.getenv("LOCAL_CHAQUOPY_REPO") ?: ".m2-chaquopy")
        if (localChaquopyRepo.isDirectory) {
            maven(url = localChaquopyRepo.toURI())
        }
        maven("https://chaquo.com/maven")
        google()
        mavenCentral()
    }
}

rootProject.name = "tg-ws-proxy-android"
include(":app")
