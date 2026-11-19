package ru.nedoletoff.omnisecretary.ui

import android.Manifest
import android.content.Context
import android.content.Intent
import android.media.MediaPlayer
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.ClickableText
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material.icons.filled.Apps
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.DocumentScanner
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.NoteAdd
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import ru.nedoletoff.omnisecretary.data.Api
import ru.nedoletoff.omnisecretary.data.ChatMessage
import ru.nedoletoff.omnisecretary.data.Session
import ru.nedoletoff.omnisecretary.data.UploadedFile
import ru.nedoletoff.omnisecretary.device.DeviceActions
import ru.nedoletoff.omnisecretary.voice.Recorder
import java.io.File
import java.util.UUID
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

private fun playWav(context: Context, bytes: ByteArray, onDone: () -> Unit = {}) {
    if (bytes.isEmpty()) { onDone(); return }
    val file = File.createTempFile("tts-", ".wav", context.cacheDir)
    file.writeBytes(bytes)
    val player = MediaPlayer()
    player.setDataSource(file.absolutePath)
    player.setOnCompletionListener {
        it.release()
        file.delete()
        onDone()
    }
    player.setOnErrorListener { _, _, _ ->
        runCatching { player.release() }
        file.delete()
        onDone()
        true
    }
    runCatching { player.prepare(); player.start() }
        .onFailure { runCatching { player.release() }; file.delete(); onDone() }
}

private val MD_LINK = Regex("\\[([^\\]]+)\\]\\(([^)]+)\\)")

private fun formatTime(timestamp: Long): String = try {
    java.time.Instant.ofEpochMilli(timestamp)
        .atZone(java.time.ZoneId.systemDefault())
        .toLocalTime()
        .format(java.time.format.DateTimeFormatter.ofPattern("HH:mm"))
} catch (e: Exception) {
    ""
}

private fun AnnotatedString.Builder.appendInlineLinks(text: String, c: AppColors) {
    var cursor = 0
    MD_LINK.findAll(text).forEach { match ->
        append(text.substring(cursor, match.range.first))
        pushStringAnnotation("URL", match.groupValues[2])
        withStyle(SpanStyle(color = c.info, textDecoration = TextDecoration.Underline)) {
            append(match.groupValues[1])
        }
        pop()
        cursor = match.range.last + 1
    }
    append(text.substring(cursor))
}

@Composable
private fun RichMessage(text: String, c: AppColors) {
    val context = LocalContext.current
    val parts = text.split("```")
    parts.forEachIndexed { index, part ->
        if (index % 2 == 1) {
            Surface(
                shape = RoundedCornerShape(8.dp),
                color = c.surface,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.padding(8.dp)) {
                    Text(part.trim().ifBlank { "…" }, fontFamily = CodeFont,
                        fontSize = 12.sp, color = c.text)
                }
            }
        } else if (part.isNotBlank()) {
            val annotated = buildAnnotatedString {
                part.lineSequence().forEach { raw ->
                    val line = raw.trimEnd()
                    when {
                        line.startsWith("#### ") -> withStyle(
                            SpanStyle(color = c.text, fontWeight = FontWeight.Bold, fontSize = 14.sp)
                        ) { append(line.removePrefix("#### ") + "\n") }
                        line.startsWith("### ") -> withStyle(
                            SpanStyle(color = c.accent, fontWeight = FontWeight.Bold, fontSize = 15.sp)
                        ) { append(line.removePrefix("### ") + "\n") }
                        line.startsWith("## ") -> withStyle(
                            SpanStyle(color = c.accent, fontWeight = FontWeight.Bold, fontSize = 16.sp)
                        ) { append(line.removePrefix("## ") + "\n") }
                        line.startsWith("# ") -> withStyle(
                            SpanStyle(color = c.accent, fontWeight = FontWeight.Bold, fontSize = 18.sp)
                        ) { append(line.removePrefix("# ") + "\n") }
                        line.startsWith("- ") || line.startsWith("* ") -> {
                            withStyle(SpanStyle(color = c.accent)) { append("• ") }
                            appendInlineLinks(line.removePrefix("- ").removePrefix("* "), c)
                            append("\n")
                        }
                        line.startsWith("> ") -> withStyle(SpanStyle(color = c.textSecondary)) {
                            append("▌ " + line.removePrefix("> ") + "\n")
                        }
                        else -> {
                            appendInlineLinks(line, c)
                            append("\n")
                        }
                    }
                }
            }
            ClickableText(text = annotated,
                style = MaterialTheme.typography.bodyMedium.copy(color = c.text),
                onClick = { offset ->
                    annotated.getStringAnnotations("URL", offset, offset).firstOrNull()?.let { annotation ->
                        runCatching {
                            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(annotation.item)))
                        }
                    }
                })
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    api: Api,
    chatId: String,
    title: String,
    draft: String,
    autoRecord: Boolean = false,
    temporary: Boolean = false,
    initialAttachment: Uri? = null,
    onBack: () -> Unit,
) {
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    val recorder = remember { Recorder(context) }
    val pulse = rememberInfiniteTransition(label = "recording").animateFloat(
        initialValue = 0.35f, targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(650), RepeatMode.Reverse),
        label = "pulse",
    )

    var activeChatId by remember { mutableStateOf(chatId) }
    var messages by remember { mutableStateOf<List<ChatMessage>>(emptyList()) }
    var input by remember { mutableStateOf(draft) }
    var busy by remember { mutableStateOf(false) }
    var recording by remember { mutableStateOf(false) }
    var handsFree by remember { mutableStateOf(autoRecord) }
    var notice by remember { mutableStateOf("") }
    val attachments = remember { mutableStateListOf<UploadedFile>() }
    var uploading by remember { mutableStateOf(false) }
    var cameraUri by remember { mutableStateOf<Uri?>(null) }
    var ocrBusy by remember { mutableStateOf(false) }
    var showDeviceActions by remember { mutableStateOf(false) }
    var deviceActionPrompt by remember { mutableStateOf<Pair<String, String>?>(null) } // title to hint
    val c = OmniPalette.colors

    var permissionGranted by remember { mutableStateOf(false) }
    fun startRecording() {
        if (busy || recording) return
        runCatching { recorder.start() }
            .onSuccess { recording = true; notice = "Говорите…" }
            .onFailure { notice = "Микрофон недоступен" }
    }

    fun speakAndMaybeListen(text: String) {
        if (text.isBlank()) return
        scope.launch {
            try {
                val bytes = api.speak(text)
                withContext(Dispatchers.Main) {
                    playWav(context, bytes) {
                        if (handsFree && !busy && !recording) startRecording()
                    }
                }
            } catch (failure: Exception) {
                notice = failure.message ?: "Озвучка недоступна"
                if (handsFree) startRecording()
            }
        }
    }

    val permission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()) { granted ->
        permissionGranted = granted
        if (!granted) notice = "Без доступа к микрофону голос не работает"
        else if (autoRecord || handsFree) startRecording()
    }

    LaunchedEffect(chatId) {
        messages = emptyList()
        if (chatId.isNotBlank()) {
            runCatching { api.messages(chatId) }.onSuccess { messages = it }
        }
    }

    // Запуск «в одно действие»: виджет, плитка или ярлык открывают чат
    // сразу в режиме прослушивания.
    LaunchedEffect(autoRecord) {
        if (autoRecord && !recording) {
            if (permissionGranted) startRecording()
            else permission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) listState.animateScrollToItem(messages.lastIndex)
    }

    fun readUri(uri: Uri): Pair<String, ByteArray>? {
        return runCatching {
            val name = context.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
                val idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (idx >= 0 && cursor.moveToFirst()) cursor.getString(idx) else null
            } ?: "file-${UUID.randomUUID()}"
            val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                ?: return null
            name to bytes
        }.getOrNull()
    }

    fun uploadAndAttach(uri: Uri, fallbackMime: String) {
        val data = readUri(uri) ?: run { notice = "Не удалось прочитать файл"; return }
        val mime = context.contentResolver.getType(uri) ?: fallbackMime
        scope.launch {
            uploading = true
            try {
                val file = api.uploadFile(data.first, data.second, mime)
                attachments.add(file)
                notice = "Вложение добавлено: ${file.name}"
            } catch (e: Exception) {
                notice = e.message ?: "Не удалось загрузить файл"
            } finally {
                uploading = false
            }
        }
    }

    val pickImage = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) uploadAndAttach(uri, "image/*")
    }
    val pickFile = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) uploadAndAttach(uri, "application/octet-stream")
    }
    val takePicture = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        val uri = cameraUri
        if (ok && uri != null) uploadAndAttach(uri, "image/jpeg")
    }
    suspend fun recognizeText(uri: Uri): String = withContext(Dispatchers.IO) {
        val image = InputImage.fromFilePath(context, uri)
        suspendCancellableCoroutine { cont ->
            val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
            recognizer.process(image)
                .addOnSuccessListener { result -> if (cont.isActive) cont.resume(result.text) }
                .addOnFailureListener { e -> if (cont.isActive) cont.resumeWithException(e) }
                .addOnCompleteListener { runCatching { recognizer.close() } }
        }
    }

    val pickForOcr = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            ocrBusy = true
            notice = "Распознавание текста…"
            try {
                val text = recognizeText(uri)
                if (text.isBlank()) {
                    notice = "Текст на изображении не найден"
                } else {
                    input = if (input.isBlank()) text.trim() else input.trimEnd() + "\n\n" + text.trim()
                    notice = "Текст распознан и добавлен в поле ввода"
                }
            } catch (e: Exception) {
                notice = e.message ?: "Не удалось распознать текст"
            } finally {
                ocrBusy = false
            }
        }
    }

    fun takePictureForReal() {
        try {
            val dir = File(context.cacheDir, "camera").apply { mkdirs() }
            val file = File(dir, "photo-${System.currentTimeMillis()}.jpg")
            val uri = FileProvider.getUriForFile(
                context, "${context.packageName}.fileprovider", file)
            cameraUri = uri
            takePicture.launch(uri)
        } catch (e: Exception) {
            notice = "Камера недоступна: ${e.message ?: "ошибка"}"
        }
    }

    val cameraPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) takePictureForReal()
        else notice = "Разрешите доступ к камере в настройках приложения"
    }

    fun launchCamera() {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED) {
            takePictureForReal()
        } else {
            cameraPermission.launch(Manifest.permission.CAMERA)
        }
    }

    // Вложение, переданное через «Поделиться» из другого приложения.
    LaunchedEffect(initialAttachment) {
        if (initialAttachment != null) {
            uploadAndAttach(initialAttachment, "application/octet-stream")
        }
    }

    // Приватный чат: запрещаем скриншоты и предпросмотр в недавних.
    androidx.compose.runtime.DisposableEffect(temporary) {
        val window = (context as? android.app.Activity)?.window
        if (temporary) {
            window?.addFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE)
        }
        onDispose {
            window?.clearFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE)
        }
    }

    fun send(prompt: String) {
        val text = prompt.trim()
        val files = attachments.toList()
        if ((text.isEmpty() && files.isEmpty()) || busy || uploading) return

        // Команды устройству (таймер, будильник, открыть приложение и т.д.)
        if (files.isEmpty()) {
            DeviceActions.tryExecute(context, text)?.let { result ->
                messages = messages +
                    ChatMessage("user", text, System.currentTimeMillis()) +
                    ChatMessage("assistant",
                        if (result.ok) "✓ ${result.message}" else "Не удалось: ${result.message}",
                        System.currentTimeMillis() + 1)
                input = ""
                notice = if (result.ok) result.message else result.message
                return
            }
        }

        val history = messages
        val display = if (text.isNotBlank()) text else "[вложение]"
        messages = history + ChatMessage("user", display, System.currentTimeMillis()) +
            ChatMessage("assistant", "", System.currentTimeMillis() + 1)
        input = ""
        attachments.clear()
        busy = true
        notice = ""
        scope.launch {
            try {
                if (activeChatId.isBlank()) activeChatId = api.newChat(temporary)
                var answer = ""
                val sendText = if (text.isNotBlank()) text else "Опиши вложение."
                api.stream(activeChatId, Session.model, history, sendText, onDelta = delta@{ delta ->
                    answer += delta
                    val current = messages.toMutableList()
                    val last = current.lastOrNull() ?: return@delta
                    current[current.lastIndex] = last.copy(content = last.content + delta)
                    messages = current
                }, files = files)
                // Перечитаем историю, чтобы у локальных сообщений появились
                // настоящие id Open WebUI — иначе следующий запрос даст 409.
                runCatching { api.messages(activeChatId) }
                    .onSuccess { stored -> if (stored.size >= messages.size) messages = stored }
                if (handsFree && answer.isNotBlank()) speakAndMaybeListen(answer)
            } catch (failure: Exception) {
                notice = failure.message ?: "Ошибка ответа"
                messages = messages.dropLast(1)
            } finally {
                busy = false
            }
        }
    }

    fun toggleRecording() {
        if (recording) {
            recording = false
            val bytes = recorder.stop()
            if (bytes == null || bytes.isEmpty()) {
                notice = "Запись не удалась"
                return
            }
            scope.launch {
                busy = true
                try {
                    val text = api.transcribe(bytes)
                    if (text.isBlank()) notice = "Речь не распознана"
                    else if (handsFree) send(text) else input = text
                } catch (failure: Exception) {
                    notice = failure.message ?: "Ошибка распознавания"
                } finally {
                    busy = false
                }
            }
        } else {
            if (!permissionGranted) {
                permission.launch(Manifest.permission.RECORD_AUDIO)
                return
            }
            startRecording()
        }
    }

    fun speakLast() {
        val text = messages.lastOrNull { it.role == "assistant" && it.content.isNotBlank() }
            ?.content
        if (text == null) {
            notice = "Нет ответа для озвучки"
            return
        }
        speakAndMaybeListen(text)
    }

    fun saveLastToNote() {
        val text = messages.lastOrNull { it.role == "assistant" && it.content.isNotBlank() }
            ?.content ?: return
        scope.launch {
            try {
                val path = api.saveNote(title.ifBlank { "Заметка из чата" }, text)
                notice = "Сохранено: $path"
            } catch (failure: Exception) {
                notice = failure.message ?: "Не удалось сохранить"
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(
                            if (temporary) "Приватный чат" else title.ifBlank { "Чат" },
                            maxLines = 1)
                        Text(
                            if (temporary) "не сохраняется" else Session.model,
                            style = MaterialTheme.typography.labelSmall,
                            color = if (temporary) c.info else c.textSecondary,
                            maxLines = 1)
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Назад")
                    }
                },
                actions = {
                    IconButton(onClick = { showDeviceActions = true }) {
                        Icon(Icons.Filled.Apps, contentDescription = "Действия на устройстве",
                            tint = c.action)
                    }
                    IconButton(onClick = { speakLast() }) {
                        Icon(Icons.Filled.VolumeUp, contentDescription = "Озвучить")
                    }
                    IconButton(onClick = { saveLastToNote() }) {
                        Icon(Icons.Filled.NoteAdd, contentDescription = "В заметку")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = c.surface, titleContentColor = c.text),
            )
        },
        containerColor = c.background,
        bottomBar = {
            Column(
                Modifier.background(c.surface).imePadding().padding(horizontal = 8.dp, vertical = 6.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                if (recording) {
                    Row(
                        Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Box(
                            Modifier.size(10.dp).graphicsLayer(alpha = pulse.value)
                                .background(c.danger, CircleShape)
                        )
                        Text("Слушаю… запись идёт локально, отправлю после остановки",
                            color = c.danger, style = MaterialTheme.typography.bodySmall)
                    }
                }
                FilterChip(
                    selected = handsFree,
                    onClick = { handsFree = !handsFree },
                    label = {
                        Text(if (handsFree) "Живой диалог: вкл" else "Живой диалог: выкл",
                            style = MaterialTheme.typography.labelSmall)
                    },
                    leadingIcon = {
                        Icon(Icons.Filled.Mic, contentDescription = null,
                            Modifier.size(14.dp),
                            tint = if (handsFree) c.success else c.textSecondary)
                    },
                )
                if (notice.isNotBlank()) {
                    Text(notice, color = c.textSecondary,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp))
                }
                if (attachments.isNotEmpty() || uploading) {
                    Row(
                        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
                            .padding(horizontal = 4.dp, vertical = 4.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        attachments.forEach { file ->
                            AssistChip(
                                onClick = { attachments.remove(file) },
                                label = { Text(file.name.take(18), maxLines = 1) },
                                trailingIcon = { Icon(Icons.Filled.Close, null, Modifier.size(14.dp)) },
                            )
                        }
                        if (uploading) CircularProgressIndicator(Modifier.size(18.dp))
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = { toggleRecording() }) {
                        Icon(
                            if (recording) Icons.Filled.Stop else Icons.Filled.Mic,
                            contentDescription = "Голос",
                            tint = if (recording) c.danger else c.accent,
                            modifier = Modifier.size(24.dp),
                        )
                    }
                    IconButton(onClick = {
                        notice = "Выберите файл в системном диалоге"
                        pickFile.launch(arrayOf("*/*"))
                    }, enabled = !uploading) {
                        Icon(Icons.Filled.AttachFile, contentDescription = "Вложить файл",
                            tint = c.action)
                    }
                    IconButton(onClick = { launchCamera() }, enabled = !uploading && !ocrBusy) {
                        Icon(Icons.Filled.CameraAlt, contentDescription = "Камера",
                            tint = c.action)
                    }
                    IconButton(onClick = { pickForOcr.launch("image/*") }, enabled = !ocrBusy && !busy) {
                        if (ocrBusy) CircularProgressIndicator(Modifier.size(18.dp))
                        else Icon(Icons.Filled.DocumentScanner, contentDescription = "Распознать текст с фото",
                            tint = c.info)
                    }
                    OutlinedTextField(
                        value = input,
                        onValueChange = { input = it },
                        modifier = Modifier
                            .weight(1f)
                            .heightIn(min = 52.dp),
                        placeholder = { Text("Спросите что-нибудь") },
                        maxLines = 4,
                    )
                    IconButton(
                        onClick = { send(input) },
                        enabled = !busy && !uploading && !ocrBusy && (input.isNotBlank() || attachments.isNotEmpty()),
                    ) {
                        if (busy) CircularProgressIndicator(Modifier.size(20.dp))
                        else Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Отправить",
                            tint = c.success)
                    }
                }
            }
        },
    ) { padding ->
        LazyColumn(
            state = listState,
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            itemsIndexed(messages) { _, message ->
                val isUser = message.role == "user"
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
                    verticalAlignment = Alignment.Top,
                ) {
                    if (!isUser) {
                        Box(
                            Modifier.size(28.dp).padding(end = 4.dp)
                                .background(c.accent, CircleShape),
                            contentAlignment = Alignment.Center,
                        ) {
                            Text("O", color = c.onAccent,
                                style = MaterialTheme.typography.labelMedium)
                        }
                    }
                    Column(
                        Modifier.widthIn(max = 340.dp).fillMaxWidth(0.88f),
                        horizontalAlignment = if (isUser) Alignment.End else Alignment.Start,
                    ) {
                        Row(
                            Modifier.fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
                        ) {
                            Text(if (isUser) "Вы" else "OmniSecretary",
                                style = MaterialTheme.typography.labelSmall,
                                color = c.textSecondary)
                            Text(" · ${formatTime(message.timestamp)}",
                                style = MaterialTheme.typography.labelSmall,
                                color = c.textSecondary)
                            IconButton(
                                onClick = {
                                    clipboard.setText(AnnotatedString(message.content))
                                    notice = "Сообщение скопировано"
                                },
                                modifier = Modifier.size(20.dp),
                            ) {
                                Icon(Icons.Filled.ContentCopy, "Копировать сообщение",
                                    Modifier.size(13.dp), tint = c.textSecondary)
                            }
                        }
                        Surface(
                            color = if (isUser) c.accent else c.surface,
                            shape = RoundedCornerShape(
                                topStart = 14.dp, topEnd = 14.dp,
                                bottomStart = if (isUser) 14.dp else 4.dp,
                                bottomEnd = if (isUser) 4.dp else 14.dp,
                            ),
                            tonalElevation = if (isUser) 0.dp else 1.dp,
                        ) {
                            SelectionContainer(Modifier.padding(12.dp)) {
                                RichMessage(message.content.ifBlank { "…" }, c)
                            }
                        }
                    }
                    if (isUser) {
                        Box(
                            Modifier.size(28.dp).padding(start = 4.dp)
                                .background(c.info, CircleShape),
                            contentAlignment = Alignment.Center,
                        ) {
                            Text("Я", color = c.onAccent,
                                style = MaterialTheme.typography.labelMedium)
                        }
                    }
                }
            }
        }
    }

    if (showDeviceActions) {
        DeviceActionsDialog(
            onDismiss = { showDeviceActions = false },
            onAction = { result ->
                showDeviceActions = false
                messages = messages + ChatMessage("assistant",
                    if (result.ok) "✓ ${result.message}" else "Не удалось: ${result.message}",
                    System.currentTimeMillis())
                notice = result.message
            },
            onPrefill = { template ->
                showDeviceActions = false
                input = template
            },
        )
    }
}

@Composable
private fun DeviceActionsDialog(
    onDismiss: () -> Unit,
    onAction: (DeviceActions.Result) -> Unit,
    onPrefill: (String) -> Unit,
) {
    val context = LocalContext.current
    val c = OmniPalette.colors
    var customTimer by remember { mutableStateOf("") }
    var customAlarm by remember { mutableStateOf("") }
    var customApp by remember { mutableStateOf("") }
    var customSearch by remember { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = c.surface,
        title = { Text("Действия на устройстве", color = c.accent) },
        text = {
            Column(
                Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text("Как у ассистента: таймеры, будильники, приложения и поиск.",
                    color = c.textSecondary, style = MaterialTheme.typography.bodySmall)

                Text("Таймер (минуты)", color = c.text)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(customTimer, { customTimer = it.filter { c -> c.isDigit() } },
                        modifier = Modifier.weight(1f), singleLine = true,
                        placeholder = { Text("5") })
                    TextButton(onClick = {
                        val min = customTimer.toIntOrNull() ?: 5
                        onAction(DeviceActions.setTimer(context, min * 60))
                    }) { Text("Старт") }
                }

                Text("Будильник (ЧЧ:ММ)", color = c.text)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(customAlarm, { customAlarm = it },
                        modifier = Modifier.weight(1f), singleLine = true,
                        placeholder = { Text("07:30") })
                    TextButton(onClick = {
                        val parts = customAlarm.split(":", ".", " ", limit = 2)
                        val h = parts.getOrNull(0)?.toIntOrNull() ?: 7
                        val m = parts.getOrNull(1)?.toIntOrNull() ?: 30
                        onAction(DeviceActions.setAlarm(context, h, m))
                    }) { Text("Поставить") }
                }

                Text("Открыть приложение", color = c.text)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(customApp, { customApp = it },
                        modifier = Modifier.weight(1f), singleLine = true,
                        placeholder = { Text("YouTube, Карты…") })
                    TextButton(onClick = {
                        if (customApp.isBlank()) onPrefill("Открой ")
                        else onAction(DeviceActions.openApp(context, customApp))
                    }) { Text("Открыть") }
                }

                Text("Поиск в интернете", color = c.text)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(customSearch, { customSearch = it },
                        modifier = Modifier.weight(1f), singleLine = true,
                        placeholder = { Text("запрос") })
                    TextButton(onClick = {
                        if (customSearch.isBlank()) onPrefill("Найди в интернете ")
                        else onAction(DeviceActions.searchWeb(context, customSearch))
                    }) { Text("Найти") }
                }

                TextButton(onClick = { onAction(DeviceActions.showAlarms(context)) }) {
                    Text("Показать будильники/часы")
                }
                TextButton(onClick = { onPrefill("Маршрут до ") }) {
                    Text("Маршрут (ввести адрес)")
                }
            }
        },
        confirmButton = {},
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Закрыть") }
        },
    )
}
