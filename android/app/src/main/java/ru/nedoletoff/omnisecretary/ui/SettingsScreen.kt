package ru.nedoletoff.omnisecretary.ui

import android.content.Context
import android.media.MediaPlayer
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import org.json.JSONObject
import ru.nedoletoff.omnisecretary.data.Api
import ru.nedoletoff.omnisecretary.data.ModelInfo
import ru.nedoletoff.omnisecretary.data.Session
import ru.nedoletoff.omnisecretary.data.VoiceSettings
import java.io.File

/** Голоса по умолчанию, если сервер не вернул справочник. */
private val DEFAULT_VOICES = listOf("aidar", "baya", "kseniya", "xenia", "eugene", "neural")

@Composable
fun SettingsScreen(api: Api, onLogout: () -> Unit, onThemeChanged: (String) -> Unit = {}) {
    val context = LocalContext.current
    val prefs = remember {
        context.getSharedPreferences("omni_settings", android.content.Context.MODE_PRIVATE)
    }
    var models by remember { mutableStateOf<List<ModelInfo>>(emptyList()) }
    var usage by remember { mutableStateOf<JSONObject?>(null) }
    var status by remember { mutableStateOf("") }
    var statusError by remember { mutableStateOf("") }
    var appNotifications by remember {
        mutableStateOf(prefs.getBoolean("notify_app", true))
    }
    var telegramEcho by remember {
        mutableStateOf(prefs.getBoolean("notify_telegram", false))
    }
    var biometric by remember {
        mutableStateOf(prefs.getBoolean("biometric_lock", false))
    }
    var ntfyServer by remember {
        mutableStateOf(prefs.getString("ntfy_server", "https://ntfy.sh") ?: "https://ntfy.sh")
    }
    var ntfyTopic by remember {
        mutableStateOf(prefs.getString("ntfy_topic", "") ?: "")
    }
    var themeMode by remember { mutableStateOf(Session.themeMode) }
    val scope = rememberCoroutineScope()

    // Голосовые настройки читаются один раз при открытии экрана.
    val initialVoice = remember { VoiceSettings.load(context) }
    var sttEngine by remember { mutableStateOf(initialVoice.sttEngine) }
    var sttModel by remember { mutableStateOf(initialVoice.sttModel) }
    var sttLanguage by remember { mutableStateOf(initialVoice.sttLanguage) }
    var ttsEngine by remember { mutableStateOf(initialVoice.ttsEngine) }
    var ttsModel by remember { mutableStateOf(initialVoice.ttsModel) }
    var ttsVoice by remember { mutableStateOf(initialVoice.ttsVoice) }
    var ttsLanguage by remember { mutableStateOf(initialVoice.ttsLanguage) }
    var ttsFormat by remember { mutableStateOf(initialVoice.ttsFormat) }
    var assistantVoice by remember { mutableStateOf(initialVoice.assistantVoice) }
    var assistantModel by remember { mutableStateOf(initialVoice.assistantModel) }
    var assistantStyle by remember { mutableStateOf(initialVoice.assistantStyle) }
    var availableVoices by remember { mutableStateOf<List<String>>(emptyList()) }
    var voiceMessage by remember { mutableStateOf("") }
    var voiceTesting by remember { mutableStateOf(false) }
    val c = OmniPalette.colors

    // Голоса берём с сервера; при недоступности показываем встроенный список.
    LaunchedEffect(Unit) {
        runCatching { api.voices() }
            .onSuccess { catalog ->
                availableVoices = catalog.voices.ifEmpty { DEFAULT_VOICES }
            }
            .onFailure { availableVoices = DEFAULT_VOICES }
    }

    LaunchedEffect(Unit) {
        runCatching { api.modelDetails() }.onSuccess { models = it }
            .onFailure { runCatching { api.models() }.onSuccess { list -> models = list.map { ModelInfo(it.first, it.second, "") } } }
        runCatching { api.usage() }.onSuccess { if (it.length() > 0) usage = it }
        runCatching { api.status() }
            .onSuccess { body ->
                val temp = body.optJSONObject("temperature") ?: body.optJSONObject("temp")
                val memory = body.optJSONObject("memory") ?: body.optJSONObject("mem")
                val cpu = body.optJSONObject("cpu")
                val load = body.opt("load")?.toString() ?: ""
                status = buildString {
                    temp?.opt("value")?.let { append("Температура: $it °C\n") }
                    if (memory != null) {
                        val used = (memory.opt("used_mb") ?: memory.opt("used") ?: "?").toString()
                        val total = (memory.opt("total_mb") ?: memory.opt("total") ?: "?").toString()
                        append("Память: $used / $total МБ\n")
                    }
                    cpu?.opt("percent")?.let { append("CPU: $it%\n") }
                    if (load.isNotBlank() && load != "null") append("Load: $load\n")
                    body.optString("uptime").takeIf { it.isNotBlank() }
                        ?.let { append("Uptime: $it\n") }
                    body.optJSONObject("disk")?.let { d ->
                        val u = (d.opt("used") ?: "").toString()
                        val t = (d.opt("total") ?: "").toString()
                        if (u.isNotBlank()) append("Диск: $u / $t\n")
                    }
                }.ifBlank { "Метрики недоступны" }
            }
            .onFailure { statusError = it.message ?: "Недоступно" }
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text("Настройки", style = MaterialTheme.typography.headlineSmall, color = c.accent)

        Card(colors = CardDefaults.cardColors(containerColor = c.surface)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("Аккаунт", color = c.accent)
                Text(Session.email.ifBlank { "—" }, color = c.text)
                Text(Session.server, color = c.textSecondary,
                    style = MaterialTheme.typography.bodySmall)
                Text("Роль: ${Session.role.ifBlank { "—" }}", color = c.textSecondary,
                    style = MaterialTheme.typography.bodySmall)
            }
        }

        Card(colors = CardDefaults.cardColors(containerColor = c.surface)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Модели", color = c.accent)
                Text("Выберите модель по умолчанию. Описание помогает понять, для чего она подходит.",
                    color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
                if (models.isEmpty()) {
                    Text("Список недоступен", color = c.textSecondary,
                        style = MaterialTheme.typography.bodySmall)
                } else {
                    models.forEach { model ->
                        Column(
                            Modifier
                                .fillMaxWidth()
                                .clickable { Session.setModel(model.id) }
                                .padding(vertical = 8.dp),
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    if (model.id == Session.model) "● " else "○ ",
                                    color = if (model.id == Session.model) c.success else c.textSecondary,
                                )
                                Column(Modifier.weight(1f)) {
                                    Text(model.name, color = c.text)
                                    if (model.name != model.id) {
                                        Text(model.id, color = c.textSecondary,
                                            style = MaterialTheme.typography.labelSmall)
                                    }
                                }
                            }
                            if (model.description.isNotBlank()) {
                                Text(model.description, color = c.textSecondary,
                                    style = MaterialTheme.typography.bodySmall,
                                    modifier = Modifier.padding(start = 18.dp, top = 2.dp))
                            }
                        }
                        HorizontalDivider(color = c.surfaceAlt)
                    }
                }
            }
        }

        usage?.optJSONObject("totals")?.let { totals ->
            Card(colors = CardDefaults.cardColors(containerColor = c.surface)) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("Использование и статистика", color = c.accent)
                    val lifetime = totals.optLong("lifetime_tokens", 0)
                    val input = totals.optLong("input_tokens", 0)
                    val output = totals.optLong("output_tokens", 0)
                    val chats = totals.optInt("total_chats", 0)
                    val messages = totals.optInt("messages", 0)
                    val modelsUsed = totals.optInt("models_used", 0)
                    val activeDays = totals.optInt("active_days", 0)
                    if (lifetime > 0 || messages > 0) {
                        if (lifetime > 0) Text("Токены всего: $lifetime (ввод: $input, ответ: $output)", color = c.text,
                            style = MaterialTheme.typography.bodySmall)
                        Text("Чатов: $chats · Сообщений: $messages", color = c.text,
                            style = MaterialTheme.typography.bodySmall)
                        Text("Моделей использовано: $modelsUsed · Активных дней: $activeDays", color = c.textSecondary,
                            style = MaterialTheme.typography.bodySmall)
                    } else {
                        Text("Статистика появится после первых диалогов.", color = c.textSecondary,
                            style = MaterialTheme.typography.bodySmall)
                    }
                    Text("Точные расходы зависят от модели и провайдера (см. биллинг провайдера).",
                        color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
                }
            }
        }

        Card(colors = CardDefaults.cardColors(containerColor = c.surface)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("Оформление", color = c.accent)
                Text("Gruvbox — фирменная тёмная тема. Material You — цвета из обоев/системы (Android 12+).",
                    color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
                listOf("gruvbox" to "Gruvbox", "material_you" to "Material You").forEach { (id, label) ->
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .clickable {
                                themeMode = id
                                Session.setThemeMode(id)
                                onThemeChanged(id)
                            }
                            .padding(vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(if (id == themeMode) "● " else "○ ",
                            color = if (id == themeMode) c.success else c.textSecondary)
                        Text(label, color = c.text)
                    }
                }
            }
        }

        Card(colors = CardDefaults.cardColors(containerColor = c.surface)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Безопасность", color = c.accent)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Блокировка биометрией", color = c.text)
                        Text("Запрашивать отпечаток/лицо при входе и при возврате в приложение",
                            color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
                    }
                    Switch(checked = biometric, onCheckedChange = {
                        biometric = it
                        prefs.edit().putBoolean("biometric_lock", it).apply()
                    })
                }
            }
        }

        Card(colors = CardDefaults.cardColors(containerColor = c.surface)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Уведомления", color = c.accent)
                Text("Каналы независимы: приложение, Telegram и ntfy настраиваются отдельно.",
                    color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("В приложении", Modifier.weight(1f), color = c.text)
                    Switch(checked = appNotifications, onCheckedChange = {
                        appNotifications = it
                        prefs.edit().putBoolean("notify_app", it).apply()
                    })
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Дублировать в Telegram", Modifier.weight(1f), color = c.text)
                    Switch(checked = telegramEcho, onCheckedChange = {
                        telegramEcho = it
                        prefs.edit().putBoolean("notify_telegram", it).apply()
                    })
                }
                OutlinedTextField(ntfyServer, { ntfyServer = it; prefs.edit().putString("ntfy_server", it).apply() },
                    label = { Text("ntfy сервер") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth())
                OutlinedTextField(ntfyTopic, { ntfyTopic = it; prefs.edit().putString("ntfy_topic", it).apply() },
                    label = { Text("ntfy topic (пусто — не использовать)") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth())
                Text("Для собственного ntfy укажите адрес сервера и topic.",
                    color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
            }
        }

        Card(colors = CardDefaults.cardColors(containerColor = c.surface)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Голос", color = c.accent)
                Text(
                    "Распознавание и озвучка. Локальный режим работает на Pi, " +
                        "облачный — через API, настроенный на сервере.",
                    color = c.textSecondary, style = MaterialTheme.typography.bodySmall)

                Text("Распознавание речи (STT)", color = c.text)
                EngineDropdown(
                    "Движок распознавания", sttEngine,
                    listOf("local" to "Локальный (Pi / Vosk)", "cloud" to "Облачный API"),
                    { sttEngine = it }, c)
                OutlinedTextField(sttModel, { sttModel = it },
                    label = { Text("Модель распознавания") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth())
                OutlinedTextField(sttLanguage, { sttLanguage = it },
                    label = { Text("Язык распознавания (например, ru)") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth())

                HorizontalDivider(color = c.surfaceAlt)
                Text("Озвучка (TTS)", color = c.text)
                EngineDropdown(
                    "Движок озвучки", ttsEngine,
                    listOf("local" to "Локальный Silero/Piper", "cloud" to "Облачный API"),
                    { ttsEngine = it }, c)
                VoiceField(ttsVoice, { ttsVoice = it }, availableVoices, c)
                OutlinedTextField(ttsModel, { ttsModel = it },
                    label = { Text("Модель озвучки") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth())
                OutlinedTextField(ttsLanguage, { ttsLanguage = it },
                    label = { Text("Язык озвучки (например, ru)") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth())
                EngineDropdown("Формат ответа", ttsFormat,
                    listOf("wav" to "WAV", "mp3" to "MP3"), { ttsFormat = it }, c)

                HorizontalDivider(color = c.surfaceAlt)
                Text("Голос нейронки", color = c.text)
                Text("TTS-персона ассистента: её голос и модель/стиль озвучки в чате. " +
                        "Пустые значения — использовать общие настройки TTS.",
                    color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
                VoiceField(assistantVoice, { assistantVoice = it }, availableVoices, c,
                    label = "Голос ассистента (пусто — как общий TTS)")
                OutlinedTextField(assistantModel, { assistantModel = it },
                    label = { Text("Модель озвучки ассистента (пусто — как общий TTS)") },
                    singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(assistantStyle, { assistantStyle = it },
                    label = { Text("Описание персоны (стиль)") }, minLines = 3,
                    modifier = Modifier.fillMaxWidth())

                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = {
                        VoiceSettings(
                            sttEngine = sttEngine,
                            sttModel = sttModel,
                            sttLanguage = sttLanguage,
                            ttsEngine = ttsEngine,
                            ttsModel = ttsModel,
                            ttsVoice = ttsVoice,
                            ttsLanguage = ttsLanguage,
                            ttsFormat = ttsFormat,
                            assistantVoice = assistantVoice,
                            assistantModel = assistantModel,
                            assistantStyle = assistantStyle,
                        ).save(context)
                        voiceMessage = "Голосовые настройки сохранены"
                    }, modifier = Modifier.weight(1f)) {
                        Text("Сохранить")
                    }
                    Button(onClick = {
                        voiceTesting = true
                        voiceMessage = "Запрашиваю озвучку…"
                        scope.launch {
                            try {
                                val bytes = api.speak(
                                    "Проверка голоса",
                                    voice = ttsVoice.ifBlank { null },
                                    engine = ttsEngine.ifBlank { null },
                                    model = ttsModel.ifBlank { null },
                                    language = ttsLanguage.ifBlank { null },
                                    responseFormat = ttsFormat.ifBlank { null },
                                )
                                playVoicePreview(
                                    context, bytes,
                                    if (ttsFormat.equals("mp3", true)) "mp3" else "wav",
                                    onDone = {
                                        voiceTesting = false
                                        voiceMessage = "Голос сгенерирован и воспроизведён"
                                    },
                                    onError = { message ->
                                        voiceTesting = false
                                        voiceMessage = message
                                    },
                                )
                            } catch (e: Exception) {
                                voiceTesting = false
                                voiceMessage = "Ошибка: ${e.message ?: "неизвестная"}"
                            }
                        }
                    }, modifier = Modifier.weight(1f), enabled = !voiceTesting) {
                        Text(if (voiceTesting) "Проверяю…" else "Проверить голос")
                    }
                }
                if (voiceMessage.isNotBlank()) {
                    Text(voiceMessage, color = c.textSecondary,
                        style = MaterialTheme.typography.bodySmall)
                }
            }
        }

        Card(colors = CardDefaults.cardColors(containerColor = c.surface)) {
            Column(Modifier.padding(16.dp)) {
                Text("Устройство", color = c.accent)
                when {
                    status.isNotBlank() -> Text(status, color = c.text,
                        style = MaterialTheme.typography.bodySmall)
                    statusError.isNotBlank() -> Text("Недоступно: $statusError", color = c.textSecondary,
                        style = MaterialTheme.typography.bodySmall)
                    else -> Text("Только для владельца", color = c.textSecondary,
                        style = MaterialTheme.typography.bodySmall)
                }
            }
        }

        HorizontalDivider(color = c.surfaceAlt)
        Button(onClick = {
            Session.clear()
            onLogout()
        }, modifier = Modifier.fillMaxWidth()) {
            Text("Выйти")
        }
        Text("OmniSecretary · self-hosted, данные на вашем Pi",
            color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
    }
}

/** Выпадающий список с фиксированными вариантами (движок, формат). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun EngineDropdown(
    label: String,
    value: String,
    options: List<Pair<String, String>>,
    onChange: (String) -> Unit,
    c: AppColors,
) {
    var expanded by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { expanded = it }) {
        OutlinedTextField(
            value = options.firstOrNull { it.first == value }?.second ?: value,
            onValueChange = {},
            readOnly = true,
            label = { Text(label) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().menuAnchor(),
        )
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            options.forEach { (id, title) ->
                DropdownMenuItem(
                    text = { Text(title, color = c.text) },
                    onClick = { onChange(id); expanded = false },
                )
            }
        }
    }
}

/** Голос: подсказки с сервера плюс произвольный ввод. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun VoiceField(
    value: String,
    onChange: (String) -> Unit,
    suggestions: List<String>,
    c: AppColors,
    label: String = "Голос (из списка или вручную)",
) {
    var expanded by remember { mutableStateOf(false) }
    val matches = suggestions.filter { it.contains(value, ignoreCase = true) }
    ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { expanded = it }) {
        OutlinedTextField(
            value = value,
            onValueChange = onChange,
            label = { Text(label) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().menuAnchor(),
        )
        if (matches.isNotEmpty()) {
            DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
                matches.forEach { name ->
                    DropdownMenuItem(
                        text = { Text(name, color = c.text) },
                        onClick = { onChange(name); expanded = false },
                    )
                }
            }
        }
    }
}

/** Небольшой плеер для проверки голоса: временный файл удаляется после игры. */
private fun playVoicePreview(
    context: Context,
    bytes: ByteArray,
    extension: String,
    onDone: () -> Unit,
    onError: (String) -> Unit,
) {
    if (bytes.isEmpty()) {
        onError("Сервер вернул пустой аудиофайл")
        return
    }
    val file = try {
        File.createTempFile("voice-test-", ".$extension", context.cacheDir)
    } catch (e: Exception) {
        onError("Не удалось создать временный файл: ${e.message ?: "ошибка"}")
        return
    }
    val player = MediaPlayer()
    fun cleanup() {
        runCatching { player.release() }
        runCatching { file.delete() }
    }
    player.setOnCompletionListener {
        cleanup()
        onDone()
    }
    player.setOnErrorListener { _, what, _ ->
        cleanup()
        onError("Не удалось воспроизвести звук (код $what)")
        true
    }
    runCatching {
        file.writeBytes(bytes)
        player.setDataSource(file.absolutePath)
        player.prepare()
        player.start()
    }.onFailure { failure ->
        cleanup()
        onError(failure.message ?: "Не удалось воспроизвести звук")
    }
}
