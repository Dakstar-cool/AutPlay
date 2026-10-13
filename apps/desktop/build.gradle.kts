import org.jetbrains.compose.desktop.application.dsl.TargetFormat
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.kotlin.jvm)
    alias(libs.plugins.compose.multiplatform)
    alias(libs.plugins.compose.compiler)
}

kotlin {
    compilerOptions.jvmTarget.set(JvmTarget.JVM_17)
}

dependencies {
    implementation(project(":shared:ui"))
    implementation(project(":shared:data"))
    implementation(libs.kotlinx.serialization.json)
    testImplementation(libs.junit4)
    val desktopRuntime = providers.gradleProperty("autplay.desktopRuntime").orNull
    implementation(
        when (desktopRuntime) {
            null -> compose.desktop.currentOs
            "macos-arm64" -> compose.desktop.macos_arm64
            else -> throw GradleException("Unsupported autplay.desktopRuntime: $desktopRuntime")
        },
    )
    implementation(compose.material3)
}

// Resolves the full native runtime for checksum review on a different development host.
tasks.register<Sync>("prepareDesktopRuntime") {
    from(configurations.runtimeClasspath) {
        // Transitional Compose coordinates can publish distinct JARs with the same name.
        // Keep their cache identities instead of flattening and losing one artifact.
        eachFile { path = "${file.parentFile.name}/$name" }
    }
    into(layout.buildDirectory.dir("resolved-runtime"))
}

compose.desktop {
    application {
        mainClass = "app.autplay.desktop.MainKt"
        nativeDistributions {
            targetFormats(TargetFormat.Dmg)
            packageName = "AutPlay"
            // JDK 17's macOS bundler requires a positive first version component.
            // This installer remains an unsigned development foundation.
            packageVersion = "1.0.0"
            description = "AutPlay macOS development prototype"
            vendor = "AutPlay"
            includeAllModules = true
            macOS {
                bundleID = "app.autplay.desktop"
                dockName = "AutPlay"
                minimumSystemVersion = "13.0"
            }
        }
    }
}
