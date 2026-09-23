plugins {
    id("com.android.application")
}

android {
    namespace = "com.dwu.fomocontroller"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.dwu.fomocontroller"
        minSdk = 28
        targetSdk = 37
        versionCode = 1
        versionName = "0.1.0"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
}
