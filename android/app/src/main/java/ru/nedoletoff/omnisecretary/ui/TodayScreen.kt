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
import ru.nedoletoff.omnisecretary.data.ReminderItem
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
    val c = OmniPalette.colors
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<TodayData?>(null) }
    var reminders by remember { mutableStateOf<List<ReminderItem>>(emptyList()) }
    var error by remember { mutableStateOf("") }
    var notice by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(true) }
    var dialog by remember { mutableStateOf(DialogKind.NONE) }
    var editingTask by remember { mutableStateOf<ru.nedoletoff.omnisecretary.data.TodayTask?>(null) }
    var editingEvent by remember { mutableStateOf<ru.nedoletoff.omnisecretary.data.TodayEvent?>(null) }

    val pendingPrefs = remember { context.getSharedPreferences("omni_pending", Context.MODE_PRIVATE) }
    var pendingNotes by remember {
        mutableStateOf(runCatching {
            org.json.JSONArray(pendingPrefs.getString("notes", "[]")).length()
        }.getOrDefault(0))
    }
    var pendingReminders by remember {
        mutableStateOf(runCatching {
            org.json.JSONArray(pendingPrefs.getString("reminders", "[]")).length()
        }.getOrDefault(0))
    }
    fun enqueuePendingNote(title: String, text: String, folder: String = "AI/Inbox") {
        val arr = runCatching { org.json.JSONArray(pendingPrefs.getString("notes", "[]")) }
            .getOrDefault(org.json.JSONArray())
        arr.put(org.json.JSONObject().put("title", title).put("text", text).put("folder", folder))
        pendingPrefs.edit().putString("notes", arr.toString()).apply()
        pendingNotes = arr.length()
    }
    suspend fun flushPendingNotes(): Int {
        val arr = runCatching { org.json.JSONArray(pendingPrefs.getString("notes", "[]")) }
            .getOrDefault(org.json.JSONArray())
        if (arr.length() == 0) return 0
        val remaining = org.json.JSONArray()
        var sent = 0
        for (i in 0 until arr.length()) {
            val item = arr.optJSONObject(i) ?: continue
            val ok = runCatching {
                api.saveNote(item.optString("title", "Заметка"), item.optString("text", ""),
                    item.optString("folder", "AI/Inbox"))
            }.isSuccess
            if (ok) sent++ else remaining.put(item)
        }
        pendingPrefs.edit().putString("notes", remaining.toString()).apply()
        pendingNotes = remaining.length()
        return sent
    }
    fun enqueuePendingReminder(text: String, dueAtIso: String, telegram: Boolean) {
        val arr = runCatching { org.json.JSONArray(pendingPrefs.getString("reminders", "[]")) }
            .getOrDefault(org.json.JSONArray())
        arr.put(org.json.JSONObject().put("text", text).put("due_at", dueAtIso).put("telegram", telegram))
        pendingPrefs.edit().putString("reminders", arr.toString()).apply()
        pendingReminders = arr.length()
    }
    suspend fun flushPendingReminders(): Int {
        val arr = runCatching { org.json.JSONArray(pendingPrefs.getString("reminders", "[]")) }
            .getOrDefault(org.json.JSONArray())
        if (arr.length() == 0) return 0
        val remaining = org.json.JSONArray()
        var sent = 0
        for (i in 0 until arr.length()) {
            val item = arr.optJSONObject(i) ?: continue
            val ok = runCatching {
                api.reminder(item.optString("text"), item.optString("due_at"), item.optBoolean("telegram", false))
            }.isSuccess
            if (ok) sent++ else remaining.put(item)
        }
        pendingPrefs.edit().putString("reminders", arr.toString()).apply()
        pendingReminders = remaining.length()
        return sent
    }

    suspend fun load(showSpinner: Boolean = true) {
        if (showSpinner) busy = true
        error = ""
        try {
            data = api.today()
            reminders = runCatching { api.reminders() }.getOrDefault(emptyList())
            var sentNotes = 0
            var sentReminders = 0
            if (pendingNotes > 0) sentNotes = runCatching { flushPendingNotes() }.getOrDefault(0)
            if (pendingReminders > 0) sentReminders = runCatching { flushPendingReminders() }.getOrDefault(0)
            if (sentNotes > 0 || sentReminders > 0) {
                notice = buildList {
                    if (sentNotes > 0) add("заметок: $sentNotes")
                    if (sentReminders > 0) add("напоминаний: $sentReminders")
                }.joinToString(", ", prefix = "Отправлено отложенных — ")
            }
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
                color = c.accent, modifier = Modifier.weight(1f))
            IconButton(onClick = { scope.launch { load() } }) {
                Icon(Icons.Filled.Refresh, contentDescription = "Обновить", tint = c.action)
            }
        }

        if (notice.isNotBlank()) {
            Text(notice, color = c.info, style = MaterialTheme.typography.bodySmall)
        }
        if (pendingNotes > 0 || pendingReminders > 0) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                val parts = buildList {
                    if (pendingNotes > 0) add("заметок: $pendingNotes")
                    if (pendingReminders > 0) add("напоминаний: $pendingReminders")
                }.joinToString(", ")
                Text("В очереди — $parts", color = c.warning,
                    style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                TextButton(onClick = {
                    scope.launch {
                        val sentN = runCatching { flushPendingNotes() }.getOrDefault(0)
                        val sentR = runCatching { flushPendingReminders() }.getOrDefault(0)
                        val total = sentN + sentR
                        notice = if (total > 0) "Отправлено: $total" else "Не удалось отправить, попробуйте позже"
                        if (total > 0) load(showSpinner = false)
                    }
                }) { Text("Отправить") }
            }
        }
        if (busy && data == null) {
            CircularProgressIndicator()
            return@Column
        }
        if (error.isNotBlank()) {
            Text(error, color = c.danger)
            return@Column
        }
        val today = data ?: return@Column

        Card(colors = CardDefaults.cardColors(containerColor = c.surface)) {
            Column(Modifier.padding(16.dp)) {
                Text(today.place, color = c.textSecondary,
                    style = MaterialTheme.typography.bodySmall)
                if (today.temperature != null) {
                    Text("${today.temperature}°C, ветер ${today.wind ?: 0} м/с",
                        style = MaterialTheme.typography.headlineMedium, color = c.text)
                } else {
                    Text("Погода недоступна", color = c.textSecondary)
                }
            }
        }

        // Быстрые действия — создание без переключения в веб.
        Card(colors = CardDefaults.cardColors(containerColor = c.surface),
            modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Быстрые действия", color = c.accent,
                    style = MaterialTheme.typography.titleMedium)
                Text("Создавайте записи прямо отсюда. Подробный календарь — во вкладке «Календарь», заметки — во вкладке «Заметки».",
                    color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    AssistChip(onClick = { dialog = DialogKind.NOTE },
                        label = { Text("Заметка в Obsidian") },
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

        // События с удалением
        Card(colors = CardDefaults.cardColors(containerColor = c.surface),
            modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("События сегодня", color = c.accent, style = MaterialTheme.typography.titleMedium)
                if (today.events.isEmpty()) {
                    Text("На сегодня событий нет. Добавьте событие кнопкой выше или во вкладке «Календарь».",
                        color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
                } else {
                    today.events.forEach { event ->
                        EventRow(event,
                            onDelete = {
                                runAction {
                                    api.deleteEvent(event.id)
                                    "Событие удалено"
                                }
                            },
                            onEdit = { editingEvent = event })
                    }
                }
            }
        }

        // Задачи с выполнением и удалением
        Card(colors = CardDefaults.cardColors(containerColor = c.surface),
            modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("Задачи", color = c.accent, style = MaterialTheme.typography.titleMedium)
                Text("Задачи из Vikunja. Можно отметить выполненной, изменить или удалить.",
                    color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
                if (today.tasks.isEmpty()) {
                    Text("Активных задач нет. Нажмите «Задача», чтобы создать.",
                        color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
                } else {
                    today.tasks.forEach { task ->
                        TaskRow(task,
                            onDone = { runAction { api.completeTask(task.id, true); "Задача выполнена" } },
                            onDelete = { runAction { api.deleteTask(task.id); "Задача удалена" } },
                            onEdit = { editingTask = task })
                    }
                }
            }
        }

        Section("Недавние заметки", today.notes.map { it.title },
            "Короткий список заметок. Создание, редактирование и поиск — во вкладке «Заметки».")

        // Напоминания
        Card(colors = CardDefaults.cardColors(containerColor = c.surface),
            modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("Напоминания", color = c.accent,
                    style = MaterialTheme.typography.titleMedium)
                Text("Локальные напоминания и (при включении) уведомления в Telegram.",
                    color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
                if (reminders.isEmpty()) {
                    Text("Нет запланированных напоминаний.",
                        color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
                } else {
                    reminders.forEach { r ->
                        Row(Modifier.fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(r.text, color = c.text)
                                Text(r.dueAt.replace('T', ' ').take(16), color = c.textSecondary,
                                    style = MaterialTheme.typography.bodySmall)
                            }
                            TextButton(onClick = {
                                runAction { api.deleteReminder(r.id); "Напоминание удалено" }
                            }) { Text("Удалить", color = c.danger) }
                        }
                    }
                }
            }
        }
    }

    when (dialog) {
        DialogKind.NOTE -> NoteDialog(
            onDismiss = { dialog = DialogKind.NONE },
            onSave = { title, text ->
                scope.launch {
                    val t = title.ifBlank { "Заметка" }
                    try {
                        val path = api.saveNote(t, text)
                        notice = "Сохранено: $path"
                        dialog = DialogKind.NONE
                        load(showSpinner = false)
                    } catch (failure: Exception) {
                        enqueuePendingNote(t, text)
                        notice = "Нет связи: заметка сохранена в очередь (${pendingNotes})"
                        dialog = DialogKind.NONE
                    }
                }
            })
        DialogKind.REMINDER -> ReminderDialog(
            context = context,
            onDismiss = { dialog = DialogKind.NONE },
            onSave = { text, at, telegram ->
                scope.launch {
                    val prefs = context.getSharedPreferences("omni_settings", Context.MODE_PRIVATE)
                    val appNotify = prefs.getBoolean("notify_app", true)
                    if (appNotify) {
                        Reminders.schedule(context, text,
                            at.atZone(ZoneId.systemDefault()).toInstant().toEpochMilli())
                    }
                    val dueIso = at.atZone(ZoneId.systemDefault()).toOffsetDateTime().toString()
                    val apiOk = runCatching { api.reminder(text, dueIso, telegram) }.isSuccess
                    if (apiOk) {
                        notice = if (telegram) "Напоминание создано (приложение + Telegram)"
                            else "Напоминание создано в приложении"
                    } else {
                        enqueuePendingReminder(text, dueIso, telegram)
                        notice = "Напоминание сохранено локально, синхронизация в очереди (${pendingReminders})"
                    }
                    dialog = DialogKind.NONE
                    load(showSpinner = false)
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

    editingTask?.let { task ->
        TaskEditDialog(task = task,
            onDismiss = { editingTask = null },
            onSave = { title, due ->
                runAction {
                    api.updateTask(task.id, title = title, due = due)
                    editingTask = null
                    "Задача обновлена"
                }
            })
    }

    editingEvent?.let { event ->
        EventEditDialog(event = event,
            onDismiss = { editingEvent = null },
            onSave = { title, description ->
                runAction {
                    api.updateEvent(event.id, title = title, description = description)
                    editingEvent = null
                    "Событие обновлено"
                }
            })
    }
}

@Composable
private fun Section(title: String, lines: List<String>, hint: String = "") {
    val c = OmniPalette.colors
    Card(colors = CardDefaults.cardColors(containerColor = c.surface),
        modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, color = c.accent, style = MaterialTheme.typography.titleMedium)
            if (hint.isNotBlank()) {
                Text(hint, color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
            }
            if (lines.isEmpty()) {
                Text("Пока пусто", color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
            } else {
                lines.forEach { Text("• $it", color = c.text) }
            }
        }
    }
}

@Composable
private fun EventRow(event: ru.nedoletoff.omnisecretary.data.TodayEvent,
                     onDelete: () -> Unit, onEdit: () -> Unit) {
    val c = OmniPalette.colors
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(if (event.time.isBlank()) event.title else "${event.time}  ${event.title}",
                color = c.text)
        }
        TextButton(onClick = onEdit) { Text("✎", color = c.action) }
        TextButton(onClick = onDelete) { Text("×", color = c.danger) }
    }
}

@Composable
private fun TaskRow(task: ru.nedoletoff.omnisecretary.data.TodayTask,
                    onDone: () -> Unit, onDelete: () -> Unit, onEdit: () -> Unit) {
    val c = OmniPalette.colors
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            val due = if (task.due.isBlank()) "" else " · ${task.due}"
            Text("${task.title}$due", color = c.text)
        }
        TextButton(onClick = onEdit) { Text("✎", color = c.action) }
        TextButton(onClick = onDone) { Text("✓", color = c.success) }
        TextButton(onClick = onDelete) { Text("×", color = c.danger) }
    }
}

@Composable
private fun NoteDialog(onDismiss: () -> Unit, onSave: (String, String) -> Unit) {
    val c = OmniPalette.colors
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
                    color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
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
    val c = OmniPalette.colors
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
    val c = OmniPalette.colors
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
    val c = OmniPalette.colors
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

@Composable
private fun TaskEditDialog(
    task: ru.nedoletoff.omnisecretary.data.TodayTask,
    onDismiss: () -> Unit,
    onSave: (String, String) -> Unit,
) {
    val c = OmniPalette.colors
    var title by remember { mutableStateOf(task.title) }
    var due by remember { mutableStateOf(task.due) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Редактировать задачу") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(title, { title = it }, label = { Text("Название") },
                    singleLine = true)
                OutlinedTextField(due, { due = it },
                    label = { Text("Срок (ГГГГ-ММ-ДД)") }, singleLine = true)
            }
        },
        confirmButton = {
            TextButton(onClick = { if (title.isNotBlank()) onSave(title.trim(), due.trim()) },
                enabled = title.isNotBlank()) { Text("Сохранить") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } },
    )
}

@Composable
private fun EventEditDialog(
    event: ru.nedoletoff.omnisecretary.data.TodayEvent,
    onDismiss: () -> Unit,
    onSave: (String, String) -> Unit,
) {
    val c = OmniPalette.colors
    var title by remember { mutableStateOf(event.title) }
    var description by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Редактировать событие") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(title, { title = it }, label = { Text("Название") },
                    singleLine = true)
                OutlinedTextField(description, { description = it },
                    label = { Text("Описание (необязательно)") }, minLines = 2)
                Text("Время: ${event.time.ifBlank { "не указано" }}. Для изменения времени удалите и создайте событие заново.",
                    color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
            }
        },
        confirmButton = {
            TextButton(onClick = { if (title.isNotBlank()) onSave(title.trim(), description.trim()) },
                enabled = title.isNotBlank()) { Text("Сохранить") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } },
    )
}
