import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.kotlin.multiplatform)
    alias(libs.plugins.compose.multiplatform)
    alias(libs.plugins.compose.compiler)
}

kotlin {
    jvm {
        compilerOptions.jvmTarget.set(JvmTarget.JVM_17)
    }
    sourceSets {
        commonMain.dependencies {
            api(project(":shared:application"))
            implementation(compose.runtime)
            implementation(compose.foundation)
            implementation(compose.material3)
        }
        jvmTest.dependencies {
            implementation(libs.junit4)
        }
    }
}

val desktopGlyphs = tasks.register<Sync>("prepareDesktopGlyphs") {
    from(rootProject.layout.projectDirectory.dir("apps/android/src/main/res/drawable")) {
        include("ic_autplay_*.xml")
        into("autplay/glyphs")
    }
    into(layout.buildDirectory.dir("generated/desktopGlyphs"))
}

kotlin.sourceSets.named("jvmMain") {
    resources.srcDir(desktopGlyphs)
}
