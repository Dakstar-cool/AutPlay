pluginManagement {
    resolutionStrategy {
        eachPlugin {
            val implementationModule = when (requested.id.id) {
                "org.jetbrains.kotlin.multiplatform", "org.jetbrains.kotlin.jvm" ->
                    "org.jetbrains.kotlin:kotlin-gradle-plugin"
                "org.jetbrains.kotlin.plugin.compose" -> "org.jetbrains.kotlin:compose-compiler-gradle-plugin"
                "org.jetbrains.compose" -> "org.jetbrains.compose:compose-gradle-plugin"
                "com.android.application" -> "com.android.tools.build:gradle"
                "com.google.devtools.ksp" -> "com.google.devtools.ksp:symbol-processing-gradle-plugin"
                "androidx.room3" -> "androidx.room3:room3-gradle-plugin"
                else -> null
            }
            if (implementationModule != null) {
                useModule("$implementationModule:${requested.version}")
            }
        }
    }
    repositories {
        google {
            content {
                includeGroupByRegex("androidx(\\..*)?")
                includeGroupByRegex("com\\.android(\\..*)?")
                includeGroupByRegex("com\\.google\\.android(\\..*)?")
                includeGroupByRegex("com\\.google\\.firebase(\\..*)?")
                includeGroupByRegex("com\\.google\\.testing(\\..*)?")
                includeGroup("com.google.gms")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google {
            content {
                includeGroupByRegex("androidx(\\..*)?")
                includeGroupByRegex("com\\.android(\\..*)?")
                includeGroupByRegex("com\\.google\\.android(\\..*)?")
                includeGroupByRegex("com\\.google\\.firebase(\\..*)?")
                includeGroupByRegex("com\\.google\\.testing(\\..*)?")
                includeGroup("com.google.gms")
            }
        }
        mavenCentral()
    }
}

rootProject.name = "AutPlay"

if (providers.gradleProperty("autplay.desktopOnly").orNull != "true") {
    include(":apps:android")
}
include(":shared:domain", ":shared:application", ":shared:data", ":shared:ui", ":apps:desktop")
