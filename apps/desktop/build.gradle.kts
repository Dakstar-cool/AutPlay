import org.jetbrains.compose.desktop.application.dsl.TargetFormat
import org.jetbrains.compose.desktop.application.tasks.AbstractJPackageTask
import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermissions
import java.time.Duration

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

val hostIsMacArm64 = System.getProperty("os.name").startsWith("Mac") &&
    System.getProperty("os.arch") in setOf("aarch64", "arm64")
val nativeResourcesRoot = layout.buildDirectory.dir("native-resources")
val audioHelper = nativeResourcesRoot.map { it.file("macos-arm64/native/AutPlayAudioHelper") }
val compileMacAudioHelper = tasks.register<Exec>("compileMacAudioHelper") {
    onlyIf { hostIsMacArm64 }
    val source = layout.projectDirectory.file("src/native/AutPlayAudioHelper.swift")
    val binary = audioHelper.get().asFile
    inputs.file(source)
    inputs.property("swiftMode", "5")
    inputs.property("deploymentTarget", "arm64-apple-macosx13.0")
    outputs.file(binary)
    timeout.set(Duration.ofMinutes(2))
    doFirst { binary.parentFile.mkdirs() }
    commandLine(
        "xcrun", "swiftc", "-swift-version", "5", "-O", "-target", "arm64-apple-macosx13.0",
        "-framework", "Foundation", "-framework", "AVFoundation",
        "-framework", "CoreMedia", source.asFile.absolutePath, "-o", binary.absolutePath,
    )
}

tasks.withType<Test>().configureEach {
    maxParallelForks = 1
    if (hostIsMacArm64) {
        dependsOn(compileMacAudioHelper)
        systemProperty(
            "autplay.nativeTestHelper",
            providers.gradleProperty("autplay.nativeTestHelper").orElse(audioHelper.map { it.asFile.absolutePath }).get(),
        )
        systemProperty("autplay.requireNativeTest", "true")
    }
}

tasks.withType<Sync>().configureEach {
    if (name == "prepareAppResources") dependsOn(compileMacAudioHelper)
}

// Compose's internal resource copy drops Unix modes. Restore the helper in the completed
// development app image before DMG creation; final release signing follows all bundle mutations.
tasks.withType<AbstractJPackageTask>().configureEach {
    if (hostIsMacArm64 && targetFormat == TargetFormat.AppImage) {
        doLast {
            val helper = destinationDir.get().asFile.resolve(
                "${packageName.get()}.app/Contents/app/resources/native/AutPlayAudioHelper",
            )
            check(helper.isFile) { "Native audio helper is missing" }
            Files.setPosixFilePermissions(helper.toPath(), PosixFilePermissions.fromString("rwxr-xr-x"))
            check(helper.canExecute()) { "Native audio helper is not executable" }
        }
    }
}

compose.desktop {
    application {
        mainClass = "app.autplay.desktop.MainKt"
        nativeDistributions {
            appResourcesRootDir.set(nativeResourcesRoot)
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
