plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "app.auren.phone"
    compileSdk = 37

    defaultConfig {
        applicationId = "app.auren.phone"
        minSdk = 29
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { compose = true }
    testOptions { unitTests.isReturnDefaultValues = true }
}

// The phone's own voice needs Phonon's library, built by native/build-phonon.sh and not committed. Without it
// the app still builds and reads replies with Android's own voice.
if (!file("src/main/jniLibs/arm64-v8a/libptts_ffi.so").exists()) {
    logger.warn("auren: no libptts_ffi.so; run android/native/build-phonon.sh for the phone's own voice")
}

dependencies {
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    implementation(libs.compose.icons)
    implementation(libs.activity.compose)
    implementation(libs.lifecycle.runtime.compose)
    implementation(libs.camerax.core)
    implementation(libs.camerax.camera2)
    implementation(libs.camerax.lifecycle)
    implementation(libs.camerax.view)
    implementation(libs.mlkit.barcode)
    implementation(libs.okhttp)
    implementation(libs.coroutines.android)
    // Phonon's library is a plain C API (libptts_ffi.so); JNA calls it without a JNI shim.
    implementation(libs.jna) { artifact { type = "aar" } }

    testImplementation(libs.junit)
    testImplementation(libs.coroutines.test)
    // Android's own org.json is a stub in local unit tests: the protocol tests use the real one.
    testImplementation(libs.json)
}
