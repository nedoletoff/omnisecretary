package ru.nedoletoff.omnisecretary.data

import android.content.Context

/**
 * Снимок голосовых настроек для экрана настроек.
 *
 * Единственный источник правды — [Session]: там же определены ключи и значения
 * по умолчанию. Класс остаётся только для формы и не дублирует ключи хранилища.
 */
data class VoiceSettings(
    val sttEngine: String = Session.DEFAULT_STT_ENGINE,
    val sttModel: String = Session.DEFAULT_STT_MODEL,
    val sttLanguage: String = "",
    val ttsEngine: String = Session.DEFAULT_TTS_ENGINE,
    val ttsModel: String = "",
    val ttsVoice: String = Session.DEFAULT_TTS_VOICE,
    val ttsLanguage: String = "",
    val ttsFormat: String = "",
    val assistantVoice: String = "",
    val assistantModel: String = "",
    val assistantStyle: String = "",
) {
    /** Сохраняет значения через Session, чтобы их сразу увидели все экраны. */
    fun save(context: Context) {
        Session.init(context)
        Session.setVoiceSttEngine(sttEngine)
        Session.setVoiceSttModel(sttModel)
        Session.setVoiceSttLanguage(sttLanguage)
        Session.setVoiceTtsEngine(ttsEngine)
        Session.setVoiceTtsModel(ttsModel)
        Session.setVoiceTtsVoice(ttsVoice)
        Session.setVoiceTtsLanguage(ttsLanguage)
        Session.setVoiceTtsFormat(ttsFormat)
        Session.setVoiceAssistantVoice(assistantVoice)
        Session.setVoiceAssistantModel(assistantModel)
        Session.setVoiceAssistantStyle(assistantStyle)
    }

    companion object {
        /** Читает те же поля, что использует Session при распознавании и озвучке. */
        fun load(context: Context): VoiceSettings {
            Session.init(context)
            return VoiceSettings(
                sttEngine = Session.voiceSttEngine,
                sttModel = Session.voiceSttModel,
                sttLanguage = Session.voiceSttLanguage,
                ttsEngine = Session.voiceTtsEngine,
                ttsModel = Session.voiceTtsModel,
                ttsVoice = Session.voiceTtsVoice,
                ttsLanguage = Session.voiceTtsLanguage,
                ttsFormat = Session.voiceTtsFormat,
                assistantVoice = Session.voiceAssistantVoice,
                assistantModel = Session.voiceAssistantModel,
                assistantStyle = Session.voiceAssistantStyle,
            )
        }
    }
}
