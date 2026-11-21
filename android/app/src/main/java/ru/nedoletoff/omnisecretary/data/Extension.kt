package ru.nedoletoff.omnisecretary.data

/**
 * Безопасные голосовые настройки для сетевых вызовов.
 *
 * Session хранит сырые значения строками, где пустое поле означает «не задано».
 * Здесь пустая строка превращается в null, чтобы сервер применил свой default
 * и в запрос не уходил бессмысленный параметр с пустым значением.
 */
val Session.safeSttEngine: String? get() = voiceSttEngine.ifBlank { null }
val Session.safeSttModel: String? get() = voiceSttModel.ifBlank { null }
val Session.safeSttLanguage: String? get() = voiceSttLanguage.ifBlank { null }

val Session.safeTtsEngine: String? get() = voiceTtsEngine.ifBlank { null }
val Session.safeTtsModel: String? get() = voiceTtsModel.ifBlank { null }
val Session.safeTtsVoice: String? get() = voiceTtsVoice.ifBlank { null }
val Session.safeTtsLanguage: String? get() = voiceTtsLanguage.ifBlank { null }
val Session.safeTtsFormat: String? get() = voiceTtsFormat.ifBlank { null }

val Session.safeAssistantVoice: String? get() = voiceAssistantVoice.ifBlank { null }
val Session.safeAssistantModel: String? get() = voiceAssistantModel.ifBlank { null }
