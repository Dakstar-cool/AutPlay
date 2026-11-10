import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import org.gradle.api.tasks.testing.Test

plugins {
    alias(libs.plugins.kotlin.multiplatform)
    alias(libs.plugins.ksp)
    alias(libs.plugins.room3)
}

kotlin {
    jvm { compilerOptions.jvmTarget.set(JvmTarget.JVM_17) }
    sourceSets {
        commonMain.dependencies { api(project(":shared:application")) }
        jvmMain.dependencies {
            implementation(libs.androidx.room3.runtime)
            implementation(libs.androidx.sqlite.bundled)
            implementation(libs.kotlinx.serialization.json)
            implementation(libs.java.json.canonicalization)
        }
        jvmTest.dependencies { implementation(libs.junit4) }
    }
}

dependencies { add("kspJvm", libs.androidx.room3.compiler) }
room3 { schemaDirectory("$projectDir/schemas") }

// Artwork fixtures render offscreen and must not register a macOS GUI application.
tasks.withType<Test>().configureEach {
    systemProperty("java.awt.headless", "true")
}
