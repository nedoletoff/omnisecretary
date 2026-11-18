package ru.nedoletoff.omnisecretary.ui

import android.Manifest
import android.content.Context
import android.media.MediaPlayer
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.NoteAdd
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.VolumeUp
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import ru.nedoletoff.omnisecretary.data.Api
import ru.nedoletoff.omnisecretary.data.ChatMessage
import ru.nedoletoff.omnisecretary.data.Session
import ru.nedoletoff.omnisecretary.voice.Recorder
import java.io.File

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

    fun send(prompt: String) {
        val text = prompt.trim()
        if (text.isEmpty() || busy) return
        val history = messages
        messages = history + ChatMessage("user", text, System.currentTimeMillis()) +
            ChatMessage("assistant", "", System.currentTimeMillis() + 1)
        input = ""
        busy = true
        notice = ""
        scope.launch {
            try {
                if (activeChatId.isBlank()) activeChatId = api.newChat(temporary)
                var answer = ""
                api.stream(activeChatId, Session.model, history, text) { delta ->
                    answer += delta
                    val current = messages.toMutableList()
                    val last = current.lastOrNull() ?: return@stream
                    current[current.lastIndex] = last.copy(content = last.content + delta)
                    messages = current
                }
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
                Row(verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = { toggleRecording() }) {
                        Icon(
                            if (recording) Icons.Filled.Stop else Icons.Filled.Mic,
                            contentDescription = "Голос",
                            tint = if (recording) GruvboxRed else GruvboxYellow,
                            modifier = Modifier.size(26.dp),
                        )
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
                        enabled = !busy && input.isNotBlank(),
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
