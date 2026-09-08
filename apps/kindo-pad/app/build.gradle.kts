plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
}

android {
    namespace = "org.kindo.pad"
    compileSdk = 35

    defaultConfig {
        applicationId = "org.kindo.pad"
        // Pad 端底线 Android 8（平板实机分布 2026 年均在 12+；26 起自适应图标免 PNG 兜底）
        minSdk = 26
        targetSdk = 34
        versionCode = 2
        versionName = "0.1.1"
    }

    // 自发布签名（升级链连续性；见 deploy/signing/README.md）
    signingConfigs {
        create("release") {
            storeFile = rootProject.file("../../deploy/signing/kindo-release.keystore")
            storePassword = "kindo-release-key"
            keyAlias = "kindo"
            keyPassword = "kindo-release-key"
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }
    // lintVital 在部分环境因 AGP lint 工具缺陷崩溃（IncompatibleClassChangeError，
    // 2026-09-07 CI 实测）；发布质量以编译+单测门禁为准，lint 可手动执行
    lint {
        checkReleaseBuilds = false
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.10.01")
    implementation(composeBom)
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")

    // 媒体播放：Media3 ExoPlayer + 自定义 Header 的 HTTP DataSource（技术方案 §1）
    implementation("androidx.media3:media3-exoplayer:1.4.1")
    implementation("androidx.media3:media3-ui:1.4.1")
    implementation("androidx.media3:media3-datasource-okhttp:1.4.1")

    // 网络：OkHttp（REST / Realtime WS / Voice WS）
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    // 单元测试（WavParser 等纯 JVM 逻辑）
    testImplementation("junit:junit:4.13.2")
}
