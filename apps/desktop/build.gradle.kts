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
    implementation(compose.desktop.currentOs)
    implementation(compose.material3)
}

compose.desktop {
    application {
        mainClass = "app.autplay.desktop.MainKt"
        nativeDistributions {
            targetFormats(TargetFormat.Dmg)
            packageName = "AutPlay"
            packageVersion = "0.1.0"
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
