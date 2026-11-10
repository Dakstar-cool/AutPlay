import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.kotlin.multiplatform)
}

kotlin {
    jvm {
        compilerOptions.jvmTarget.set(JvmTarget.JVM_17)
    }
    sourceSets {
        commonMain.dependencies {
            api(project(":shared:domain"))
            api(libs.kotlinx.coroutines.core)
        }
        jvmMain.dependencies {
            api(libs.kotlinx.serialization.json)
            implementation(libs.java.json.canonicalization)
        }
        jvmTest.dependencies {
            implementation(libs.junit4)
        }
    }
}
