pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "AutPlay"

if (providers.gradleProperty("autplay.desktopOnly").orNull != "true") {
    include(":apps:android")
}
include(":shared:domain", ":shared:application", ":shared:ui", ":apps:desktop")
