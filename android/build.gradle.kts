plugins {
    id("com.android.application") version "9.0.0" apply false
    // Kotlin встроен в AGP 9; отдельный плагин kotlin-android не нужен.
    id("org.jetbrains.kotlin.plugin.compose") version "2.2.0" apply false
}
