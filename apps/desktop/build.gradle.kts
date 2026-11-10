import org.jetbrains.compose.desktop.application.dsl.TargetFormat
import org.jetbrains.compose.desktop.application.tasks.AbstractJPackageTask
import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermissions
import java.time.Duration
import java.util.Properties
import java.util.UUID

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
    implementation(libs.java.json.canonicalization)
    implementation(libs.zxing.core)
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
val systemMediaLibrary = nativeResourcesRoot.map { it.file("macos-arm64/native/libAutPlaySystemMedia.dylib") }
val remoteMediaHelper = nativeResourcesRoot.map { it.file("macos-arm64/native/AutPlayRemoteMediaHelper") }
val secureStateLibrary = nativeResourcesRoot.map { it.file("macos-arm64/native/libAutPlaySecureState.dylib") }
val sourceAccessLibrary = nativeResourcesRoot.map { it.file("macos-arm64/native/libAutPlaySourceAccess.dylib") }
val signedIdentityRecord = nativeResourcesRoot.map { it.file("macos-arm64/native/AutPlaySignedIdentity") }
// Public reviewed build inputs. Supplying these does not sign code or grant Keychain access.
val signedIdentityTeam = providers.gradleProperty("autplay.macDeveloperTeam").orElse("")
val signedIdentityProfile = providers.gradleProperty("autplay.macProvisioningProfileUuid").orElse("")
val prepareMacSignedIdentity = tasks.register("prepareMacSignedIdentity") {
    val supportedHost = hostIsMacArm64
    val developerTeam = signedIdentityTeam
    val provisioningProfile = signedIdentityProfile
    val identityRecord = signedIdentityRecord
    onlyIf { supportedHost }
    inputs.property("developerTeam", developerTeam)
    inputs.property("provisioningProfileUuid", provisioningProfile)
    outputs.file(identityRecord)
    doLast {
        val team = developerTeam.get()
        val profile = provisioningProfile.get()
        val content = if (team.isEmpty() && profile.isEmpty()) {
            "autplay-signed-identity-unavailable-1\n"
        } else {
            check(team.matches(Regex("[A-Z0-9]{10}")) && (profile.isEmpty() || profile.length == 36 &&
                runCatching { UUID.fromString(profile).toString().equals(profile, ignoreCase = true) }.getOrDefault(false))) {
                "Reviewed macOS team is required; an optional provisioning profile UUID must be canonical"
            }
            "autplay-signed-identity-1\n$team\n${profile.ifEmpty { "-" }}\n"
        }
        val output = identityRecord.get().asFile
        output.parentFile.mkdirs()
        output.writeText(content, Charsets.UTF_8)
    }
}
// Use exactly the JDK running the canonical desktop gate, not a discovered/downloaded header kit.
val systemMediaJdk = file(System.getProperty("java.home"))
val compileMacSystemMedia = tasks.register<Exec>("compileMacSystemMedia") {
    val supportedHost = hostIsMacArm64
    val jdk = systemMediaJdk
    onlyIf { supportedHost }
    val source = layout.projectDirectory.file("src/native/AutPlaySystemMedia.mm")
    val binary = systemMediaLibrary.get().asFile
    val headers = jdk.resolve("include")
    val moduleCache = layout.buildDirectory.dir("native-module-cache/system-media").get().asFile
    inputs.file(source)
    inputs.files(jdk.resolve("release"), headers.resolve("jni.h"), headers.resolve("darwin/jni_md.h"))
    inputs.property("jniHeaderRoot", headers.absolutePath)
    inputs.property("languageMode", "c++17-objc-arc")
    inputs.property("deploymentTarget", "arm64-apple-macosx13.0")
    inputs.property("systemMediaAbi", 1)
    inputs.property("sdk", "macosx")
    outputs.file(binary)
    localState.register(moduleCache)
    environment("CLANG_MODULE_CACHE_PATH", moduleCache.absolutePath)
    // Xcode/SDK selection is host-owned and can change in place; never reuse a stale dylib.
    outputs.upToDateWhen { false }
    timeout.set(Duration.ofMinutes(2))
    doFirst {
        val release = Properties().apply {
            jdk.resolve("release").inputStream().use { load(it) }
        }
        fun jdkValue(key: String): String = release.getProperty(key, "").trim('"')
        check(jdkValue("JAVA_VERSION") == "17.0.20" &&
            jdkValue("JAVA_RUNTIME_VERSION") == "17.0.20+8-LTS" &&
            jdkValue("IMPLEMENTOR") == "Microsoft") {
            "System media JNI requires the pinned Microsoft OpenJDK 17.0.20+8-LTS headers"
        }
        check(headers.resolve("jni.h").isFile && headers.resolve("darwin/jni_md.h").isFile) {
            "System media JNI requires the pinned JDK's macOS headers"
        }
        binary.parentFile.mkdirs()
        check(moduleCache.isDirectory || moduleCache.mkdirs()) { "Cannot create owned native module cache" }
    }
    // xcrun selects Xcode's macOS SDK and system frameworks. This task never fetches libraries.
    commandLine(
        "xcrun", "--sdk", "macosx", "clang++", "-std=c++17", "-O2", "-dynamiclib", "-fobjc-arc", "-fblocks",
        "-target", "arm64-apple-macosx13.0", "-fmodules-cache-path=${moduleCache.absolutePath}",
        "-I${headers.absolutePath}", "-I${headers.resolve("darwin").absolutePath}",
        "-framework", "Foundation", "-framework", "AppKit", "-framework", "MediaPlayer",
        "-Wl,-install_name,@rpath/libAutPlaySystemMedia.dylib", source.asFile.absolutePath, "-o", binary.absolutePath,
    )
}
val compileMacAudioHelper = tasks.register<Exec>("compileMacAudioHelper") {
    val supportedHost = hostIsMacArm64
    onlyIf { supportedHost }
    val source = layout.projectDirectory.file("src/native/AutPlayAudioHelper.swift")
    val binary = audioHelper.get().asFile
    val moduleCache = layout.buildDirectory.dir("native-module-cache/audio").get().asFile
    inputs.file(source)
    inputs.property("swiftMode", "5")
    inputs.property("deploymentTarget", "arm64-apple-macosx13.0")
    outputs.file(binary)
    localState.register(moduleCache)
    environment("CLANG_MODULE_CACHE_PATH", moduleCache.absolutePath)
    environment("SWIFT_MODULECACHE_PATH", moduleCache.absolutePath)
    timeout.set(Duration.ofMinutes(2))
    doFirst {
        binary.parentFile.mkdirs()
        check(moduleCache.isDirectory || moduleCache.mkdirs()) { "Cannot create owned native module cache" }
    }
    commandLine(
        "xcrun", "swiftc", "-swift-version", "5", "-O", "-target", "arm64-apple-macosx13.0",
        "-module-cache-path", moduleCache.absolutePath, "-Xcc", "-fmodules-cache-path=${moduleCache.absolutePath}",
        "-framework", "Foundation", "-framework", "AVFoundation",
        "-framework", "CoreMedia", source.asFile.absolutePath, "-o", binary.absolutePath,
    )
}

val compileMacRemoteMediaHelper = tasks.register<Exec>("compileMacRemoteMediaHelper") {
    val supportedHost = hostIsMacArm64
    onlyIf { supportedHost }
    val source = layout.projectDirectory.file("src/native/AutPlayRemoteMediaHelper.swift")
    val binary = remoteMediaHelper.get().asFile
    val moduleCache = layout.buildDirectory.dir("native-module-cache/remote-media").get().asFile
    inputs.file(source)
    inputs.property("swiftMode", "5")
    inputs.property("deploymentTarget", "arm64-apple-macosx13.0")
    inputs.property("remoteMediaProtocol", "autplay.remote-media:1")
    outputs.file(binary)
    localState.register(moduleCache)
    environment("CLANG_MODULE_CACHE_PATH", moduleCache.absolutePath)
    environment("SWIFT_MODULECACHE_PATH", moduleCache.absolutePath)
    outputs.upToDateWhen { false }
    timeout.set(Duration.ofMinutes(2))
    doFirst {
        binary.parentFile.mkdirs()
        check(moduleCache.isDirectory || moduleCache.mkdirs()) { "Cannot create owned native module cache" }
    }
    commandLine(
        "xcrun", "--sdk", "macosx", "swiftc", "-swift-version", "5", "-O",
        "-module-cache-path", moduleCache.absolutePath, "-Xcc", "-fmodules-cache-path=${moduleCache.absolutePath}",
        "-target", "arm64-apple-macosx13.0", "-framework", "Foundation",
        "-framework", "AVFoundation", "-framework", "CoreMedia", "-framework", "AppKit",
        "-framework", "Security", "-framework", "CryptoKit", "-framework", "Network",
        source.asFile.absolutePath, "-o", binary.absolutePath,
    )
}

val compileMacSecureState = tasks.register<Exec>("compileMacSecureState") {
    val supportedHost = hostIsMacArm64
    val jdk = systemMediaJdk
    onlyIf { supportedHost }
    val source = layout.projectDirectory.file("src/native/AutPlaySecureState.mm")
    val publicMediaIdentitySource = layout.projectDirectory.file("src/native/AutPlayRemoteMediaIdentity.mm")
    val binary = secureStateLibrary.get().asFile
    val headers = jdk.resolve("include")
    val moduleCache = layout.buildDirectory.dir("native-module-cache/secure-state").get().asFile
    inputs.files(source, publicMediaIdentitySource)
    inputs.files(jdk.resolve("release"), headers.resolve("jni.h"), headers.resolve("darwin/jni_md.h"))
    inputs.property("jniHeaderRoot", headers.absolutePath)
    inputs.property("languageMode", "c++17-objc-arc")
    inputs.property("deploymentTarget", "arm64-apple-macosx13.0")
    inputs.property("secureStateAbi", 1)
    inputs.property("remoteMediaIdentityAbi", 1)
    outputs.file(binary)
    localState.register(moduleCache)
    environment("CLANG_MODULE_CACHE_PATH", moduleCache.absolutePath)
    outputs.upToDateWhen { false }
    timeout.set(Duration.ofMinutes(2))
    doFirst {
        val release = Properties().apply {
            jdk.resolve("release").inputStream().use { load(it) }
        }
        fun jdkValue(key: String): String = release.getProperty(key, "").trim('"')
        check(jdkValue("JAVA_VERSION") == "17.0.20" &&
            jdkValue("JAVA_RUNTIME_VERSION") == "17.0.20+8-LTS" &&
            jdkValue("IMPLEMENTOR") == "Microsoft") {
            "Secure state JNI requires the pinned Microsoft OpenJDK 17.0.20+8-LTS headers"
        }
        check(headers.resolve("jni.h").isFile && headers.resolve("darwin/jni_md.h").isFile) {
            "Secure state JNI requires the pinned JDK's macOS headers"
        }
        binary.parentFile.mkdirs()
        check(moduleCache.isDirectory || moduleCache.mkdirs()) { "Cannot create owned native module cache" }
    }
    commandLine(
        "xcrun", "--sdk", "macosx", "clang++", "-std=c++17", "-O2", "-dynamiclib", "-fobjc-arc", "-fblocks",
        "-target", "arm64-apple-macosx13.0", "-fmodules-cache-path=${moduleCache.absolutePath}",
        "-I${headers.absolutePath}", "-I${headers.resolve("darwin").absolutePath}",
        "-framework", "Foundation", "-framework", "Security",
        "-Wl,-install_name,@rpath/libAutPlaySecureState.dylib", source.asFile.absolutePath,
        publicMediaIdentitySource.asFile.absolutePath, "-o", binary.absolutePath,
    )
}

val compileMacSourceAccess = tasks.register<Exec>("compileMacSourceAccess") {
    val supportedHost = hostIsMacArm64
    val jdk = systemMediaJdk
    onlyIf { supportedHost }
    val source = layout.projectDirectory.file("src/native/AutPlaySourceAccess.mm")
    val binary = sourceAccessLibrary.get().asFile
    val headers = jdk.resolve("include")
    val moduleCache = layout.buildDirectory.dir("native-module-cache/source-access").get().asFile
    inputs.file(source)
    inputs.files(jdk.resolve("release"), headers.resolve("jni.h"), headers.resolve("darwin/jni_md.h"))
    inputs.property("jniHeaderRoot", headers.absolutePath)
    inputs.property("languageMode", "c++17-objc-arc")
    inputs.property("deploymentTarget", "arm64-apple-macosx13.0")
    inputs.property("sourceAccessAbi", 1)
    outputs.file(binary)
    localState.register(moduleCache)
    environment("CLANG_MODULE_CACHE_PATH", moduleCache.absolutePath)
    outputs.upToDateWhen { false }
    timeout.set(Duration.ofMinutes(2))
    doFirst {
        val release = Properties().apply {
            jdk.resolve("release").inputStream().use { load(it) }
        }
        fun jdkValue(key: String): String = release.getProperty(key, "").trim('"')
        check(jdkValue("JAVA_VERSION") == "17.0.20" &&
            jdkValue("JAVA_RUNTIME_VERSION") == "17.0.20+8-LTS" &&
            jdkValue("IMPLEMENTOR") == "Microsoft") {
            "Source access JNI requires the pinned Microsoft OpenJDK 17.0.20+8-LTS headers"
        }
        check(headers.resolve("jni.h").isFile && headers.resolve("darwin/jni_md.h").isFile) {
            "Source access JNI requires the pinned JDK's macOS headers"
        }
        binary.parentFile.mkdirs()
        check(moduleCache.isDirectory || moduleCache.mkdirs()) { "Cannot create owned native module cache" }
    }
    commandLine(
        "xcrun", "--sdk", "macosx", "clang++", "-std=c++17", "-O2", "-dynamiclib", "-fobjc-arc", "-fblocks",
        "-target", "arm64-apple-macosx13.0", "-fmodules-cache-path=${moduleCache.absolutePath}",
        "-I${headers.absolutePath}", "-I${headers.resolve("darwin").absolutePath}",
        "-framework", "Foundation", "-framework", "Security", "-framework", "AppKit",
        "-Wl,-install_name,@rpath/libAutPlaySourceAccess.dylib", source.asFile.absolutePath, "-o", binary.absolutePath,
    )
}

tasks.withType<Test>().configureEach {
    maxParallelForks = 1
    // JVM artwork/policy tests are offscreen; packaged GUI startup is a separate process.
    systemProperty("java.awt.headless", "true")
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
    if (name == "prepareAppResources") {
        dependsOn(compileMacAudioHelper, compileMacSystemMedia, compileMacRemoteMediaHelper,
            compileMacSecureState, compileMacSourceAccess, prepareMacSignedIdentity)
    }
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
            val systemMedia = destinationDir.get().asFile.resolve(
                "${packageName.get()}.app/Contents/app/resources/native/libAutPlaySystemMedia.dylib",
            )
            check(systemMedia.isFile) { "Native system media library is missing" }
            val remoteHelper = destinationDir.get().asFile.resolve(
                "${packageName.get()}.app/Contents/app/resources/native/AutPlayRemoteMediaHelper",
            )
            check(remoteHelper.isFile) { "Native remote media helper is missing" }
            Files.setPosixFilePermissions(remoteHelper.toPath(), PosixFilePermissions.fromString("rwxr-xr-x"))
            check(remoteHelper.canExecute()) { "Native remote media helper is not executable" }
            val secureState = destinationDir.get().asFile.resolve(
                "${packageName.get()}.app/Contents/app/resources/native/libAutPlaySecureState.dylib",
            )
            check(secureState.isFile) { "Native secure state library is missing" }
            val identityRecord = destinationDir.get().asFile.resolve(
                "${packageName.get()}.app/Contents/app/resources/native/AutPlaySignedIdentity",
            )
            check(identityRecord.isFile) { "Public macOS signed identity record is missing" }
            val sourceAccess = destinationDir.get().asFile.resolve(
                "${packageName.get()}.app/Contents/app/resources/native/libAutPlaySourceAccess.dylib",
            )
            check(sourceAccess.isFile) { "Native source access library is missing" }
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
