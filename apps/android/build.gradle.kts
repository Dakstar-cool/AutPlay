plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.compose.compiler)
    alias(libs.plugins.ksp)
    alias(libs.plugins.room3)
}

val qaSideBySide = providers.gradleProperty("autplay.qaSideBySide").orNull == "true"
val releaseVersionCodeOverride = providers.gradleProperty("autplay.versionCode").orNull?.toInt()
val releaseVersionNameOverride = providers.gradleProperty("autplay.versionName").orNull
val releaseKeystorePath = providers.environmentVariable("AUTPLAY_ANDROID_KEYSTORE_PATH").orNull
val releaseKeystorePassword = providers.environmentVariable("AUTPLAY_ANDROID_KEYSTORE_PASSWORD").orNull
val releaseKeyAlias = providers.environmentVariable("AUTPLAY_ANDROID_KEY_ALIAS").orNull
val releaseKeyPassword = providers.environmentVariable("AUTPLAY_ANDROID_KEY_PASSWORD").orNull
val releaseSigningInputs = listOf(
    releaseKeystorePath,
    releaseKeystorePassword,
    releaseKeyAlias,
    releaseKeyPassword,
)
val releaseSigningEnabled = releaseSigningInputs.all { !it.isNullOrBlank() }

if (!releaseSigningEnabled && releaseSigningInputs.any { !it.isNullOrBlank() }) {
    throw GradleException("Production signing requires all AUTPLAY_ANDROID_* inputs")
}
if (releaseSigningEnabled && gradle.startParameter.isConfigurationCacheRequested) {
    throw GradleException("Production signing requires --no-configuration-cache")
}

android {
    namespace = "app.autplay"
    compileSdk {
        version = release(36) {
            minorApiLevel = 1
        }
    }
    buildToolsVersion = "36.1.0"

    defaultConfig {
        applicationId = if (qaSideBySide) "app.autplay.qa" else "app.autplay"
        minSdk = 26
        targetSdk = 36
        versionCode = 3
        versionName = "0.3.0"
        releaseVersionCodeOverride?.let { versionCode = it }
        releaseVersionNameOverride?.let { versionName = it }
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        if (releaseSigningEnabled) {
            create("autplayProduction") {
                storeFile = file(requireNotNull(releaseKeystorePath))
                storePassword = releaseKeystorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            if (releaseSigningEnabled) {
                signingConfig = signingConfigs.getByName("autplayProduction")
            }
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
        create("trustedLan") {
            initWith(getByName("release"))
            // The packaged personal-server topology is deliberately limited to a trusted
            // RFC1918 LAN and plain HTTP. Keep that exception opt-in and machine-visible.
            applicationIdSuffix = ".lan"
            isDebuggable = true
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("debug")
            matchingFallbacks += listOf("release")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    bundle {
        language {
            // The in-app language picker must retain every bundled translation at runtime.
            enableSplit = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        abortOnError = true
        disable += setOf("AndroidGradlePluginVersion", "GradleDependency")
        warningsAsErrors = true
    }
}


room3 {
    schemaDirectory("$projectDir/schemas")
}

dependencies {
    val composeBom = platform(libs.androidx.compose.bom)

    implementation(composeBom)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.room3.runtime)
    implementation(libs.androidx.sqlite.bundled)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.java.json.canonicalization)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.androidx.work.runtime)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.media3.common)
    implementation(libs.androidx.media3.database)
    implementation(libs.androidx.media3.datasource)
    implementation(libs.androidx.media3.exoplayer)
    implementation(libs.androidx.media3.session)
    implementation(libs.squareup.okhttp)
    implementation(libs.zxing.core)

    ksp(libs.androidx.room3.compiler)

    debugImplementation(libs.androidx.compose.ui.tooling)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
    testImplementation(libs.junit4)
    testImplementation(libs.squareup.okhttp.mockwebserver)
    androidTestImplementation(composeBom)
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.test.core.ktx)
    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.androidx.test.runner)
    androidTestImplementation(libs.androidx.room3.testing)
}
