import java.io.ByteArrayOutputStream

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Версия берётся из тега (v1.0.4-beta / android-v1.0.4-beta),
// чтобы разные сборки не получали одинаковый versionCode.
fun detectAppVersion(): Pair<String, Int> {
    val fromTag = System.getenv("GITHUB_REF_NAME")
        ?.takeIf { it.isNotBlank() && it != "main" && it != "master" }
        ?: try {
            val p = ProcessBuilder("git", "describe", "--tags", "--abbrev=0")
                .directory(rootDir).start()
            val out = ByteArrayOutputStream()
            p.inputStream.use { it.copyTo(out) }
            p.waitFor()
            out.toString().trim().takeIf { it.isNotBlank() }
        } catch (_: Exception) { null }

    val raw = fromTag.orEmpty()
    val normalized = raw.removePrefix("android-v").removePrefix("v")
    val m = Regex("""^(\d+)\.(\d+)\.(\d+)(?:[-.]beta)?$""").find(normalized)
    return if (m != null) {
        val major = m.groupValues[1].toInt()
        val minor = m.groupValues[2].toInt()
        val patch = m.groupValues[3].toInt()
        normalized to (major * 10000 + minor * 100 + patch)
    } else {
        // Резервная версия для сборок из ветки без тега.
        "1.0.4-beta" to 10004
    }
}

val (appVersionName, appVersionCode) = detectAppVersion()

android {
    namespace = "ru.nedoletoff.omnisecretary"
    compileSdk = 37

    defaultConfig {
        applicationId = "ru.nedoletoff.omnisecretary"
        minSdk = 26
        targetSdk = 37
        versionCode = appVersionCode
        versionName = appVersionName
    }

    signingConfigs {
        create("release") {
            // Для релизной подписи задайте в gradle.properties или переменных окружения:
            // RELEASE_STORE_FILE, RELEASE_STORE_PASSWORD, RELEASE_KEY_ALIAS, RELEASE_KEY_PASSWORD
            val storePath = (findProperty("RELEASE_STORE_FILE") as String?) ?: System.getenv("RELEASE_STORE_FILE")
            if (!storePath.isNullOrBlank()) {
                storeFile = file(storePath)
                storePassword = (findProperty("RELEASE_STORE_PASSWORD") as String?)
                    ?: System.getenv("RELEASE_STORE_PASSWORD")
                keyAlias = (findProperty("RELEASE_KEY_ALIAS") as String?)
                    ?: System.getenv("RELEASE_KEY_ALIAS")
                keyPassword = (findProperty("RELEASE_KEY_PASSWORD") as String?)
                    ?: System.getenv("RELEASE_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro")
            val storePath = (findProperty("RELEASE_STORE_FILE") as String?) ?: System.getenv("RELEASE_STORE_FILE")
            if (!storePath.isNullOrBlank()) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_21
        targetCompatibility = JavaVersion.VERSION_21
    }

    buildFeatures {
        compose = true
    }

    packaging {
        resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}")
    }
}

kotlin {
    jvmToolchain(21)
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2026.09.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.11.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.11.0")
    implementation("androidx.navigation:navigation-compose:2.10.2")
    implementation("androidx.core:core-ktx:1.19.1")
    implementation("androidx.security:security-crypto:1.1.0")
    implementation("androidx.biometric:biometric:1.1.0")
    implementation("androidx.fragment:fragment-ktx:1.9.1")
    implementation("com.squareup.okhttp3:okhttp:5.5.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")
    // Распознавание текста с изображений (OCR), офлайн-модель.
    implementation("com.google.mlkit:text-recognition:16.0.1")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
