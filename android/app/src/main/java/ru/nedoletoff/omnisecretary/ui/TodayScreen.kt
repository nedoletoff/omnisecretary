package ru.nedoletoff.omnisecretary.ui

import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AddTask
import androidx.compose.material.icons.filled.Event
import androidx.compose.material.icons.filled.NoteAdd
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import ru.nedoletoff.omnisecretary.data.Api
import ru.nedoletoff.omnisecretary.data.TodayData
import ru.nedoletoff.omnisecretary.notify.Reminders
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private enum class DialogKind { NONE, NOTE, REMINDER, EVENT, TASK }

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun TodayScreen(api: Api, reloadKey: Int) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<TodayData?>(null) }
    var error by remember { mutableStateOf("") }
    var notice by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(true) }
    var dialog by remember { mutableStateOf(DialogKind.NONE) }

    suspend fun load(showSpinner: Boolean = true) {
        if (showSpinner) busy = true
        error = ""
        try {
            data = api.today()
        } catch (failure: Exception) {
            error = failure.message ?: "Не удалось загрузить сводку"
        } finally {
            busy = false
        }
    }

    LaunchedEffect(reloadKey) { load() }

    fun runAction(block: suspend () -> String) {
        scope.launch {
            try {
                notice = block()
                dialog = DialogKind.NONE
                load(showSpinner = false)
            } catch (failure: Exception) {
                notice = failure.message ?: "Не удалось выполнить действие"
            }
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Сегодня", style = MaterialTheme.typography.headlineSmall,
                color = GruvboxYellow, modifier = Modifier.weight(1f))
            IconButton(onClick = { scope.launch { load() } }) {
                Icon(Icons.Filled.Refresh, contentDescription = "Обновить", tint = GruvboxBlue)
            }
        }

        if (notice.isNotBlank()) {
            Text(notice, color = GruvboxAqua, style = MaterialTheme.typography.bodySmall)
        }
        if (busy && data == null) {
            CircularProgressIndicator()
            return@Column
        }
        if (error.isNotBlank()) {
            Text(error, color = GruvboxRed)
            return@Column
        }
        val today = data ?: return@Column

        Card(colors = CardDefaults.cardColors(containerColor = GruvboxBg1)) {
            Column(Modifier.padding(16.dp)) {
                Text(today.place, color = GruvboxGray,
                    style = MaterialTheme.typography.bodySmall)
                if (today.temperature != null) {
                    Text("${today.temperature}°C, ветер ${today.wind ?: 0} м/с",
                        style = MaterialTheme.typography.headlineMedium, color = GruvboxFg)
                } else {
                    Text("Погода недоступна", color = GruvboxGray)
                }
            }
        }

        // Быстрые действия — создание без переключения в веб.
        Card(colors = CardDefaults.cardColors(containerColor = GruvboxBg1),
            modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Быстро добавить", color = GruvboxYellow,
                    style = MaterialTheme.typography.titleMedium)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    AssistChip(onClick = { dialog = DialogKind.NOTE },
                        label = { Text("Заметка") },
                        leadingIcon = { Icon(Icons.Filled.NoteAdd, null) })
                    AssistChip(onClick = { dialog = DialogKind.REMINDER },
                        label = { Text("Напоминание") },
                        leadingIcon = { Icon(Icons.Filled.Notifications, null) })
                    AssistChip(onClick = { dialog = DialogKind.EVENT },
                        label = { Text("Событие") },
                        leadingIcon = { Icon(Icons.Filled.Event, null) })
                    AssistChip(onClick = { dialog = DialogKind.TASK },
                        label = { Text("Задача") },
                        leadingIcon = { Icon(Icons.Filled.AddTask, null) })
                }
            }
        }

        Section("События", today.events.map {
            if (it.first.isBlank()) it.second else "${it.first}  ${it.second}"
        })
        Section("Задачи", today.tasks)
        Section("Заметки", today.notes)
    }

    when (dialog) {
        DialogKind.NOTE -> NoteDialog(
            onDismiss = { dialog = DialogKind.NONE },
            onSave = { title, text ->
                runAction {
                    val path = api.saveNote(title.ifBlank { "Заметка" }, text)
                    "Сохранено: $path"
                }
            })
        DialogKind.REMINDER -> ReminderDialog(
            context = context,
            onDismiss = { dialog = DialogKind.NONE },
            onSave = { text, at, telegram ->
                runAction {
                    val prefs = context.getSharedPreferences("omni_settings", Context.MODE_PRIVATE)
                    val appNotify = prefs.getBoolean("notify_app", true)
                    if (appNotify) {
                        Reminders.schedule(context, text,
                            at.atZone(ZoneId.systemDefault()).toInstant().toEpochMilli())
                    }
                    api.reminder(text, at.atZone(ZoneId.systemDefault()).toOffsetDateTime()
                        .toString(), telegram)
                    if (telegram) "Напоминание создано (приложение + Telegram)"
                    else "Напоминание создано в приложении"
                }
            })
        DialogKind.EVENT -> EventDialog(
            onDismiss = { dialog = DialogKind.NONE },
            onSave = { title, start, duration ->
                runAction {
                    api.createEvent(title, start.toString(), duration)
                    "Событие добавлено"
                }
            })
        DialogKind.TASK -> TaskDialog(
            onDismiss = { dialog = DialogKind.NONE },
            onSave = { title, due ->
                runAction {
                    api.createTask(title, due)
                    "Задача добавлена"
                }
            })
        DialogKind.NONE -> Unit
    }
}

@Composable
private fun Section(title: String, lines: List<String>) {
    Card(colors = CardDefaults.cardColors(containerColor = GruvboxBg1),
        modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, color = GruvboxYellow, style = MaterialTheme.typography.titleMedium)
            if (lines.isEmpty()) {
                Text("Пусто", color = GruvboxGray, style = MaterialTheme.typography.bodySmall)
            } else {
                lines.forEach { Text(it, color = GruvboxFg) }
            }
        }
    }
}

@Composable
private fun NoteDialog(onDismiss: () -> Unit, onSave: (String, String) -> Unit) {
    var title by remember { mutableStateOf("") }
    var text by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Заметка в Obsidian") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(title, { title = it }, label = { Text("Заголовок") },
                    singleLine = true)
                OutlinedTextField(text, { text = it }, label = { Text("Текст") },
                    minLines = 3)
                Text("Сохранится в AI/Inbox и уйдёт коммитом в вольт.",
                    color = GruvboxGray, style = MaterialTheme.typography.bodySmall)
            }
        },
        confirmButton = {
            TextButton(onClick = { if (text.isNotBlank()) onSave(title, text) },
                enabled = text.isNotBlank()) { Text("Сохранить") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } },
    )
}

@Composable
private fun ReminderDialog(
    context: Context,
    onDismiss: () -> Unit,
    onSave: (String, LocalDateTime, Boolean) -> Unit,
) {
    val now = LocalDateTime.now().plusHours(1).withMinute(0).withSecond(0).withNano(0)
    var text by remember { mutableStateOf("") }
    var date by remember { mutableStateOf(now.toLocalDate().toString()) }
    var time by remember { mutableStateOf(now.toLocalTime().format(DateTimeFormatter.ofPattern("HH:mm"))) }
    val prefs = remember { context.getSharedPreferences("omni_settings", Context.MODE_PRIVATE) }
    var telegram by remember { mutableStateOf(prefs.getBoolean("notify_telegram", false)) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Напоминание") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(text, { text = it }, label = { Text("О чём напомнить") },
                    singleLine = true)
                OutlinedTextField(date, { date = it }, label = { Text("Дата (ГГГГ-ММ-ДД)") },
                    singleLine = true)
                OutlinedTextField(time, { time = it }, label = { Text("Время (ЧЧ:ММ)") },
                    singleLine = true)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Дублировать в Telegram", Modifier.weight(1f),
                        style = MaterialTheme.typography.bodySmall)
                    androidx.compose.material3.Switch(checked = telegram,
                        onCheckedChange = { telegram = it })
                }
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val at = runCatching {
                    LocalDateTime.of(LocalDate.parse(date), LocalTime.parse(time))
                }.getOrNull() ?: return@TextButton
                if (text.isNotBlank()) onSave(text, at, telegram)
            }, enabled = text.isNotBlank()) { Text("Создать") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } },
    )
}

@Composable
private fun EventDialog(
    onDismiss: () -> Unit,
    onSave: (String, LocalDateTime, Int) -> Unit,
) {
    val now = LocalDateTime.now().plusHours(1).withMinute(0).withSecond(0).withNano(0)
    var title by remember { mutableStateOf("") }
    var date by remember { mutableStateOf(now.toLocalDate().toString()) }
    var time by remember { mutableStateOf(now.toLocalTime().format(DateTimeFormatter.ofPattern("HH:mm"))) }
    var duration by remember { mutableStateOf("60") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Событие") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(title, { title = it }, label = { Text("Название") },
                    singleLine = true)
                OutlinedTextField(date, { date = it }, label = { Text("Дата (ГГГГ-ММ-ДД)") },
                    singleLine = true)
                OutlinedTextField(time, { time = it }, label = { Text("Время (ЧЧ:ММ)") },
                    singleLine = true)
                OutlinedTextField(duration, { duration = it },
                    label = { Text("Длительность, мин") }, singleLine = true)
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val at = runCatching {
                    LocalDateTime.of(LocalDate.parse(date), LocalTime.parse(time))
                }.getOrNull() ?: return@TextButton
                val minutes = duration.toIntOrNull() ?: 60
                if (title.isNotBlank()) onSave(title, at, minutes)
            }, enabled = title.isNotBlank()) { Text("Добавить") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } },
    )
}

@Composable
private fun TaskDialog(onDismiss: () -> Unit, onSave: (String, String) -> Unit) {
    var title by remember { mutableStateOf("") }
    var due by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Задача в Vikunja") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(title, { title = it }, label = { Text("Название") },
                    singleLine = true)
                OutlinedTextField(due, { due = it },
                    label = { Text("Срок (ГГГГ-ММ-ДД), необязательно") },
                    singleLine = true)
            }
        },
        confirmButton = {
            TextButton(onClick = { if (title.isNotBlank()) onSave(title, due.trim()) },
                enabled = title.isNotBlank()) { Text("Добавить") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } },
    )
}
