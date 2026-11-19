plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

// The app has Auren's version (pyproject.toml), so the APK of a release is that release. versionCode grows
// with it: 0.1.7 is 107, 1.2.3 is 10203.
val aurenVersion: String = Regex("""(?m)^version = "(\d+\.\d+\.\d+)"$""")
    .find(rootProject.file("../pyproject.toml").readText())?.groupValues?.get(1)
    ?: error("no version = \"X.Y.Z\" in pyproject.toml")
val aurenVersionCode: Int = aurenVersion.split(".").map(String::toInt).let { (a, b, c) -> a * 10000 + b * 100 + c }

// Release signing, from the environment (the release workflow's secrets): AUREN_KEYSTORE is the keystore's
// path. Without it a release build is unsigned (and the release workflow stops).
val keystore: String? = System.getenv("AUREN_KEYSTORE")?.takeIf { it.isNotBlank() }

android {
    namespace = "app.auren.phone"
    compileSdk = 37

    defaultConfig {
        applicationId = "app.auren.phone"
        minSdk = 29
        targetSdk = 36
        versionCode = aurenVersionCode
        versionName = aurenVersion
    }

    signingConfigs {
        if (keystore != null) {
            create("release") {
                storeFile = file(keystore)
                storePassword = System.getenv("AUREN_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("AUREN_KEY_ALIAS")
                keyPassword = System.getenv("AUREN_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystore != null) signingConfig = signingConfigs.getByName("release")
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
