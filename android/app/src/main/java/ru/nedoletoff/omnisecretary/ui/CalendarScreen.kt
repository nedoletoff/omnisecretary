package ru.nedoletoff.omnisecretary.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import ru.nedoletoff.omnisecretary.data.Api
import ru.nedoletoff.omnisecretary.data.TodayEvent
import java.time.Instant
import java.time.LocalDate
import java.time.YearMonth
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.TextStyle
import java.util.Locale

@Composable
fun CalendarScreen(api: Api) {
    val c = OmniPalette.colors
    val scope = rememberCoroutineScope()
    val zone = remember { ZoneId.systemDefault() }
    var month by remember { mutableStateOf(YearMonth.now()) }
    var selected by remember { mutableStateOf(LocalDate.now()) }
    var events by remember { mutableStateOf<List<TodayEvent>>(emptyList()) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    var showAdd by remember { mutableStateOf(false) }
    var editing by remember { mutableStateOf<TodayEvent?>(null) }

    fun load() {
        scope.launch {
            loading = true
            error = ""
            try {
                events = api.events(31)
            } catch (e: Exception) {
                error = e.message ?: "Не удалось загрузить события"
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(Unit) { load() }

    fun eventDate(event: TodayEvent): LocalDate? {
        val raw = event.start
        if (raw.isBlank()) return null
        return try {
            if (raw.contains("T")) {
                if (raw.endsWith("Z") || raw.contains("+"))
                    Instant.parse(raw).atZone(zone).toLocalDate()
                else java.time.LocalDateTime.parse(raw.take(19)).toLocalDate()
            } else LocalDate.parse(raw.take(10))
        } catch (_: Exception) { null }
    }

    val eventsByDate = remember(events) { events.groupBy { eventDate(it) } }
    val selectedEvents = eventsByDate[selected].orEmpty()
    val days = remember(month) {
        val first = month.atDay(1)
        val offset = (first.dayOfWeek.value - 1) // Monday=0
        val total = month.lengthOfMonth()
        val cells = mutableListOf<LocalDate?>()
        repeat(offset) { cells.add(null) }
        for (d in 1..total) cells.add(month.atDay(d))
        while (cells.size % 7 != 0) cells.add(null)
        cells
    }
    val weekDays = listOf("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    val monthName = month.month.getDisplayName(TextStyle.FULL_STANDALONE, Locale("ru"))
        .replaceFirstChar { it.uppercase() }

    Column(Modifier.fillMaxSize().padding(horizontal = 12.dp, vertical = 8.dp)) {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Календарь", style = MaterialTheme.typography.headlineSmall, color = c.accent)
            Row {
                IconButton(onClick = { showAdd = true }) {
                    Icon(Icons.Filled.Add, contentDescription = "Новое событие", tint = c.success)
                }
                IconButton(onClick = { load() }, enabled = !loading) {
                    Icon(Icons.Filled.Refresh, contentDescription = "Обновить", tint = c.textSecondary)
                }
            }
        }

        if (loading && events.isEmpty()) {
            CircularProgressIndicator(Modifier.padding(16.dp).align(Alignment.CenterHorizontally))
        }
        if (error.isNotBlank()) {
            Text(error, color = c.danger, style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(bottom = 4.dp))
        }

        Card(colors = CardDefaults.cardColors(containerColor = c.surface),
            modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(8.dp)) {
                Row(Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = { month = month.minusMonths(1) }) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, "Предыдущий месяц", tint = c.text)
                    }
                    Text("$monthName ${month.year}", color = c.text,
                        style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    IconButton(onClick = { month = month.plusMonths(1) }) {
                        Icon(Icons.AutoMirrored.Filled.ArrowForward, "Следующий месяц", tint = c.text)
                    }
                }
                Row(Modifier.fillMaxWidth()) {
                    weekDays.forEach { day ->
                        Text(day, modifier = Modifier.weight(1f), textAlign = TextAlign.Center,
                            color = c.textSecondary, style = MaterialTheme.typography.labelSmall)
                    }
                }
                Spacer(Modifier.height(4.dp))
                LazyVerticalGrid(columns = GridCells.Fixed(7),
                    modifier = Modifier.height(240.dp), userScrollEnabled = false) {
                    items(days) { date ->
                        if (date == null) {
                            Box(Modifier.aspectRatio(1f))
                        } else {
                            val isSelected = date == selected
                            val isToday = date == LocalDate.now()
                            val hasEvents = !eventsByDate[date].isNullOrEmpty()
                            Box(
                                Modifier.aspectRatio(1f).padding(2.dp)
                                    .clip(CircleShape)
                                    .background(
                                        when {
                                            isSelected -> c.accent
                                            isToday -> c.surfaceAlt
                                            else -> c.surface
                                        })
                                    .clickable { selected = date },
                                contentAlignment = Alignment.Center,
                            ) {
                                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                    Text(
                                        date.dayOfMonth.toString(),
                                        color = if (isSelected) c.onAccent else c.text,
                                        style = MaterialTheme.typography.bodySmall,
                                    )
                                    if (hasEvents) {
                                        Box(Modifier.size(4.dp).clip(CircleShape)
                                            .background(if (isSelected) c.onAccent else c.success))
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        Spacer(Modifier.height(10.dp))
        Text(
            "События: ${selected.format(DateTimeFormatter.ofPattern("d MMMM yyyy", Locale("ru")))}",
            color = c.text, style = MaterialTheme.typography.titleSmall,
        )
        Spacer(Modifier.height(6.dp))

        if (selectedEvents.isEmpty()) {
            Text("Нет событий на этот день. Нажмите + чтобы добавить.",
                color = c.textSecondary, style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(vertical = 8.dp))
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.weight(1f, fill = false)) {
                items(selectedEvents, key = { it.id }) { event ->
                    Card(colors = CardDefaults.cardColors(containerColor = c.surfaceAlt),
                        modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp)) {
                            Row(Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically) {
                                Text(event.title.ifBlank { "Без названия" }, color = c.text,
                                    fontWeight = FontWeight.Medium,
                                    modifier = Modifier.weight(1f))
                                Row {
                                    IconButton(onClick = { editing = event }, modifier = Modifier.size(32.dp)) {
                                        Icon(Icons.Filled.Edit, "Изменить", tint = c.textSecondary,
                                            modifier = Modifier.size(18.dp))
                                    }
                                    IconButton(onClick = {
                                        scope.launch {
                                            runCatching { api.deleteEvent(event.id) }
                                            load()
                                        }
                                    }, modifier = Modifier.size(32.dp)) {
                                        Icon(Icons.Filled.Delete, "Удалить", tint = c.danger,
                                            modifier = Modifier.size(18.dp))
                                    }
                                }
                            }
                            val whenText = buildString {
                                if (event.start.isNotBlank()) append(formatEventTime(event.start))
                                if (event.end.isNotBlank()) append(" — ${formatEventTime(event.end)}")
                            }
                            if (whenText.isNotBlank()) Text(whenText, color = c.info,
                                style = MaterialTheme.typography.bodySmall)
                            if (event.description.isNotBlank()) Text(event.description,
                                color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }
    }

    if (showAdd) {
        CalendarEventDialog(
            title = "Новое событие",
            initialTitle = "",
            initialStart = "${selected}T09:00",
            initialEnd = "${selected}T10:00",
            initialDesc = "",
            onDismiss = { showAdd = false },
            onSave = { t, s, e, d ->
                scope.launch {
                    runCatching { api.createEvent(t, s, e, d) }
                    showAdd = false
                    load()
                }
            },
        )
    }
    editing?.let { event ->
        CalendarEventDialog(
            title = "Изменить событие",
            initialTitle = event.title,
            initialStart = event.start.take(16).ifBlank { "${selected}T09:00" },
            initialEnd = event.end.take(16).ifBlank { "${selected}T10:00" },
            initialDesc = event.description,
            onDismiss = { editing = null },
            onSave = { t, s, e, d ->
                scope.launch {
                    runCatching { api.updateEvent(event.id, t, s, e, d) }
                    editing = null
                    load()
                }
            },
        )
    }
}

private fun formatEventTime(raw: String): String {
    return try {
        if (raw.contains("T")) {
            val t = raw.substringAfter("T").take(5)
            if (t.contains(":")) t else raw
        } else raw
    } catch (_: Exception) { raw }
}

@Composable
private fun CalendarEventDialog(
    title: String,
    initialTitle: String,
    initialStart: String,
    initialEnd: String,
    initialDesc: String,
    onDismiss: () -> Unit,
    onSave: (String, String, String, String) -> Unit,
) {
    val c = OmniPalette.colors
    var eventTitle by remember { mutableStateOf(initialTitle) }
    var start by remember { mutableStateOf(initialStart) }
    var end by remember { mutableStateOf(initialEnd) }
    var desc by remember { mutableStateOf(initialDesc) }

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = c.surface,
        title = { Text(title, color = c.accent) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(eventTitle, { eventTitle = it }, label = { Text("Название") },
                    singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(start, { start = it },
                    label = { Text("Начало (ГГГГ-ММ-ДДTЧЧ:ММ)") },
                    singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(end, { end = it },
                    label = { Text("Конец (ГГГГ-ММ-ДДTЧЧ:ММ)") },
                    singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(desc, { desc = it }, label = { Text("Описание") },
                    modifier = Modifier.fillMaxWidth(), minLines = 2, maxLines = 4)
            }
        },
        confirmButton = {
            TextButton(onClick = {
                if (eventTitle.isNotBlank() && start.isNotBlank())
                    onSave(eventTitle, start, end, desc)
            }) { Text("Сохранить") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } },
    )
}
