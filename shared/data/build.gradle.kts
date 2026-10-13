import org.jetbrains.kotlin.gradle.dsl.JvmTarget

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
        }
        jvmTest.dependencies { implementation(libs.junit4) }
    }
}

dependencies { add("kspJvm", libs.androidx.room3.compiler) }
room3 { schemaDirectory("$projectDir/schemas") }
