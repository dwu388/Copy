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
        versionCode = 3
        versionName = "0.2.1"
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

    sourceSets {
        getByName("test").resources.srcDir("src/main/assets")
    }
}


dependencies {
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20250517")
}
