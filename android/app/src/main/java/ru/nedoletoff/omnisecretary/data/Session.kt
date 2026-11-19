package ru.nedoletoff.omnisecretary.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Сессия пользователя: адрес сервера и токен Open WebUI.
 *
 * Токен лежит только в EncryptedSharedPreferences — в открытом виде его
 * нет ни в файлах приложения, ни в резервных копиях (allowBackup=false).
 */
object Session {
    const val DEFAULT_SERVER = "https://nedoletoff.sknt.ru"
    const val DEFAULT_MODEL = "omnisecretary"

    private lateinit var prefs: SharedPreferences

    var server: String = DEFAULT_SERVER
        private set
    var token: String = ""
        private set
    var email: String = ""
        private set
    var role: String = ""
        private set
    var model: String = DEFAULT_MODEL
        private set
    var themeMode: String = "gruvbox" // gruvbox | material_you
        private set

    val isLoggedIn: Boolean get() = token.isNotBlank()

    fun init(context: Context) {
        if (::prefs.isInitialized) return
        val key = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        prefs = EncryptedSharedPreferences.create(
            context,
            "omni_session",
            key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
        server = prefs.getString("server", DEFAULT_SERVER) ?: DEFAULT_SERVER
        token = prefs.getString("token", "") ?: ""
        email = prefs.getString("email", "") ?: ""
        role = prefs.getString("role", "") ?: ""
        model = prefs.getString("model", DEFAULT_MODEL) ?: DEFAULT_MODEL
        themeMode = prefs.getString("theme_mode", "gruvbox") ?: "gruvbox"
    }

    fun save(server: String, token: String, email: String, role: String) {
        this.server = server.trimEnd('/')
        this.token = token
        this.email = email
        this.role = role
        prefs.edit()
            .putString("server", this.server)
            .putString("token", token)
            .putString("email", email)
            .putString("role", role)
            .apply()
    }

    fun setModel(value: String) {
        model = value
        prefs.edit().putString("model", value).apply()
    }

    fun setThemeMode(value: String) {
        themeMode = value
        prefs.edit().putString("theme_mode", value).apply()
    }

    fun clear() {
        token = ""
        email = ""
        role = ""
        prefs.edit().clear().apply()
    }
}
