package ru.nedoletoff.omnisecretary.ui

import android.Manifest
import android.content.Context
import android.media.MediaPlayer
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.DocumentScanner
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.NoteAdd
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.AssistChip
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconToggleButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
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
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    val recorder = remember { Recorder(context) }

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
        if (chatId.isNotBlank() && messages.isEmpty()) {
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

    fun launchCamera() {
        val dir = File(context.cacheDir, "camera").apply { mkdirs() }
        val file = File(dir, "photo-${System.currentTimeMillis()}.jpg")
        val uri = FileProvider.getUriForFile(
            context, "${context.packageName}.fileprovider", file)
        cameraUri = uri
        takePicture.launch(uri)
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
            ?.content ?: return
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
                            color = if (temporary) GruvboxAqua else GruvboxGray,
                            maxLines = 1)
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Назад")
                    }
                },
                actions = {
                    IconToggleButton(checked = handsFree,
                        onCheckedChange = { handsFree = it }) {
                        Icon(Icons.Filled.Mic,
                            contentDescription = "Диалог без рук",
                            tint = if (handsFree) GruvboxGreen else GruvboxGray)
                    }
                    IconButton(onClick = { speakLast() }) {
                        Icon(Icons.Filled.VolumeUp, contentDescription = "Озвучить")
                    }
                    IconButton(onClick = { saveLastToNote() }) {
                        Icon(Icons.Filled.NoteAdd, contentDescription = "В заметку")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = GruvboxBg1, titleContentColor = GruvboxFg),
            )
        },
        containerColor = GruvboxBg,
        bottomBar = {
            Column(Modifier.background(GruvboxBg1).padding(8.dp)) {
                if (notice.isNotBlank()) {
                    Text(notice, color = GruvboxGray,
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
                            tint = if (recording) GruvboxRed else GruvboxYellow,
                            modifier = Modifier.size(24.dp),
                        )
                    }
                    IconButton(onClick = { pickFile.launch(arrayOf("*/*")) }, enabled = !uploading) {
                        Icon(Icons.Filled.AttachFile, contentDescription = "Вложить файл",
                            tint = GruvboxBlue)
                    }
                    IconButton(onClick = { launchCamera() }, enabled = !uploading && !ocrBusy) {
                        Icon(Icons.Filled.CameraAlt, contentDescription = "Камера",
                            tint = GruvboxBlue)
                    }
                    IconButton(onClick = { pickForOcr.launch("image/*") }, enabled = !ocrBusy && !busy) {
                        if (ocrBusy) CircularProgressIndicator(Modifier.size(18.dp))
                        else Icon(Icons.Filled.DocumentScanner, contentDescription = "Распознать текст с фото",
                            tint = GruvboxAqua)
                    }
                    OutlinedTextField(
                        value = input,
                        onValueChange = { input = it },
                        modifier = Modifier
                            .weight(1f)
                            .heightIn(min = 52.dp)
                            .imePadding(),
                        placeholder = { Text("Спросите что-нибудь") },
                        maxLines = 4,
                    )
                    IconButton(
                        onClick = { send(input) },
                        enabled = !busy && !uploading && !ocrBusy && (input.isNotBlank() || attachments.isNotEmpty()),
                    ) {
                        if (busy) CircularProgressIndicator(Modifier.size(20.dp))
                        else Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Отправить",
                            tint = GruvboxGreen)
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
                Box(Modifier.fillMaxWidth()) {
                    Text(
                        text = message.content.ifBlank { "…" },
                        color = if (isUser) GruvboxBg else GruvboxFg,
                        style = MaterialTheme.typography.bodyMedium,
                        textAlign = if (isUser) TextAlign.End else TextAlign.Start,
                        modifier = Modifier
                            .align(if (isUser) Alignment.CenterEnd else Alignment.CenterStart)
                            .background(
                                if (isUser) GruvboxYellow else GruvboxBg1,
                                RoundedCornerShape(12.dp))
                            .padding(12.dp)
                            .fillMaxWidth(0.9f),
                    )
                }
            }
        }
    }
}
