plugins {
    id("com.android.application")
}

val serverUrl: String = providers.gradleProperty("serverUrl").get().trimEnd('/')

// A release is signed with the key in these environment variables (CI's
// secrets), so every build installs over the last. Without them it is
// signed with the debug key, which is fine for trying it once.
val keystorePath: String? = System.getenv("TRAINTRACKR_KEYSTORE")

android {
    namespace = "live.traintrackr.tv"
    compileSdk = 35

    defaultConfig {
        applicationId = "live.traintrackr.tv"
        minSdk = 26
        targetSdk = 35
        versionCode = (System.getenv("TRAINTRACKR_VERSION_CODE") ?: "1").toInt()
        versionName = System.getenv("TRAINTRACKR_VERSION_NAME") ?: "0.1.0"
        buildConfigField("String", "SERVER_URL", "\"$serverUrl\"")
    }

    buildFeatures {
        buildConfig = true
    }

    signingConfigs {
        if (keystorePath != null) {
            create("release") {
                storeFile = file(keystorePath)
                storePassword = System.getenv("TRAINTRACKR_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("TRAINTRACKR_KEY_ALIAS")
                keyPassword = System.getenv("TRAINTRACKR_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        abortOnError = true
        checkReleaseBuilds = false
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    // Draws the pairing QR code; no network or Play services needed.
    implementation("com.google.zxing:core:3.5.4")

    testImplementation("junit:junit:4.13.2")
    // Android's org.json is a stub in unit tests; this is the real thing.
    testImplementation("org.json:json:20260814")
}
