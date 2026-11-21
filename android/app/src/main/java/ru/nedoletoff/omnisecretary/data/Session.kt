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

    // Значения по умолчанию для голоса: сохраняют поведение до появления настроек.
    const val DEFAULT_STT_ENGINE = "local"
    const val DEFAULT_STT_MODEL = "vosk"
    const val DEFAULT_TTS_ENGINE = "local"
    const val DEFAULT_TTS_VOICE = "baya"

    private lateinit var prefs: SharedPreferences
    private lateinit var appContext: Context
    private lateinit var voicePrefs: SharedPreferences

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
        appContext = context.applicationContext
        // Голосовые настройки — не секреты, поэтому достаточно обычных SharedPreferences.
        voicePrefs = appContext.getSharedPreferences("omni_settings", Context.MODE_PRIVATE)
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

    // ── Голосовые настройки (обычные SharedPreferences, не токены) ──

    private fun voiceValue(key: String, default: String): String? {
        if (!::voicePrefs.isInitialized) return default.ifBlank { null }
        return voicePrefs.getString(key, default)?.ifBlank { null }
    }

    private fun saveVoice(key: String, value: String) {
        if (::voicePrefs.isInitialized) voicePrefs.edit().putString(key, value).apply()
    }

    val voiceSttEngine: String? get() = voiceValue("voice_stt_engine", DEFAULT_STT_ENGINE)
    val voiceSttModel: String? get() = voiceValue("voice_stt_model", DEFAULT_STT_MODEL)
    val voiceSttLanguage: String? get() = voiceValue("voice_stt_language", "")

    val voiceTtsEngine: String? get() = voiceValue("voice_tts_engine", DEFAULT_TTS_ENGINE)
    val voiceTtsModel: String? get() = voiceValue("voice_tts_model", "")
    val voiceTtsVoice: String? get() = voiceValue("voice_tts_voice", DEFAULT_TTS_VOICE)
    val voiceTtsLanguage: String? get() = voiceValue("voice_tts_language", "")
    val voiceTtsFormat: String? get() = voiceValue("voice_tts_format", "")

    val voiceAssistantVoice: String? get() = voiceValue("voice_assistant_voice", "")
    val voiceAssistantModel: String? get() = voiceValue("voice_assistant_model", "")

    /** Голос и модель TTS-персоны ассистента в чате с fallback на общие настройки. */
    val chatVoice: String? get() = voiceAssistantVoice ?: voiceTtsVoice
    val chatVoiceModel: String? get() = voiceAssistantModel ?: voiceTtsModel

    fun setVoiceSttEngine(value: String) = saveVoice("voice_stt_engine", value)
    fun setVoiceSttModel(value: String) = saveVoice("voice_stt_model", value)
    fun setVoiceSttLanguage(value: String) = saveVoice("voice_stt_language", value)
    fun setVoiceTtsEngine(value: String) = saveVoice("voice_tts_engine", value)
    fun setVoiceTtsModel(value: String) = saveVoice("voice_tts_model", value)
    fun setVoiceTtsVoice(value: String) = saveVoice("voice_tts_voice", value)
    fun setVoiceTtsLanguage(value: String) = saveVoice("voice_tts_language", value)
    fun setVoiceTtsFormat(value: String) = saveVoice("voice_tts_format", value)
    fun setVoiceAssistantVoice(value: String) = saveVoice("voice_assistant_voice", value)
    fun setVoiceAssistantModel(value: String) = saveVoice("voice_assistant_model", value)

    fun clear() {
        token = ""
        email = ""
        role = ""
        prefs.edit().clear().apply()
    }
}
