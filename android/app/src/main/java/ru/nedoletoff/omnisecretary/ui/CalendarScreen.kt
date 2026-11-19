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
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
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
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import ru.nedoletoff.omnisecretary.data.Api
import ru.nedoletoff.omnisecretary.data.Session
import ru.nedoletoff.omnisecretary.data.TodayEvent
import ru.nedoletoff.omnisecretary.ui.theme.OmniPalette
import java.time.LocalDate
import java.time.YearMonth
import java.time.format.DateTimeFormatter

private val DATE_FMT: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd")

@Composable
fun CalendarScreen(session: Session) {
    val c = OmniPalette.colors
    val api = remember(session.token, session.baseUrl) { Api(session) }
    val scope = rememberCoroutineScope()

    var month by remember { mutableStateOf(YearMonth.now()) }
    var selected by remember { mutableStateOf(LocalDate.now()) }
    var events by remember { mutableStateOf<List<TodayEvent>>(emptyList()) }
    var status by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var showAdd by remember { mutableStateOf(false) }
    var editing by remember { mutableStateOf<TodayEvent?>(null) }

    fun load() {
        scope.launch {
            loading = true
            runCatching { events = api.events(31) }
                .onFailure { status = "Не удалось загрузить: ${it.message}" }
            loading = false
        }
    }
    LaunchedEffect(Unit) { load() }

    fun eventDate(e: TodayEvent): LocalDate? =
        runCatching { LocalDate.parse(e.time.take(10), DATE_FMT) }.getOrNull()

    val dayEvents = events.filter { eventDate(it) == selected }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .background(c.bg)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text("Календарь", color = c.text, fontSize = 22.sp, fontWeight = FontWeight.Bold,
                    modifier = Modifier.weight(1f))
                TextButton(onClick = { showAdd = true }) { Text("+ Событие", color = c.accent) }
                TextButton(onClick = { load() }) { Text("↻", color = c.info) }
            }
            Text("События сохраняются в вашем календаре Open WebUI (и в веб-интерфейсе).",
                color = c.textSecondary, fontSize = 12.sp)
            if (status.isNotBlank()) Text(status, color = c.warning, fontSize = 12.sp)
        }

        item {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(14.dp))
                    .background(c.surface)
                    .padding(12.dp),
            ) {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    TextButton(onClick = { month = month.minusMonths(1) }) { Text("‹", color = c.text) }
                    Text(
                        monthText(month),
                        color = c.text,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.weight(1f),
                        textAlign = TextAlign.Center,
                    )
                    TextButton(onClick = { month = month.plusMonths(1) }) { Text("›", color = c.text) }
                }
                val days = listOf("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
                Row(Modifier.fillMaxWidth()) {
                    days.forEach { d ->
                        Text(d, color = c.textSecondary, fontSize = 11.sp,
                            modifier = Modifier.weight(1f), textAlign = TextAlign.Center)
                    }
                }
                Spacer(Modifier.height(4.dp))
                val first = month.atDay(1)
                val offset = (first.dayOfWeek.value + 6) % 7
                val total = month.lengthOfMonth()
                val cells = (1..42).map { idx ->
                    val day = idx - offset
                    if (day in 1..total) month.atDay(day) else null
                }
                cells.chunked(7).forEach { week ->
                    Row(Modifier.fillMaxWidth()) {
                        week.forEach { date ->
                            val isSelected = date == selected
                            val has = date != null && events.any { eventDate(it) == date }
                            Box(
                                modifier = Modifier
                                    .weight(1f)
                                    .aspectRatio(1f)
                                    .padding(2.dp)
                                    .clip(RoundedCornerShape(10.dp))
                                    .background(if (isSelected) c.accent.copy(alpha = 0.25f) else c.surface)
                                    .clickable(enabled = date != null) { if (date != null) selected = date },
                                contentAlignment = Alignment.Center,
                            ) {
                                if (date != null) {
                                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                        Text(
                                            date.dayOfMonth.toString(),
                                            color = if (isSelected) c.accent else c.text,
                                            fontSize = 13.sp,
                                        )
                                        if (has) {
                                            Box(
                                                Modifier
                                                    .size(4.dp)
                                                    .clip(CircleShape)
                                                    .background(c.info)
                                            )
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        item {
            Text(
                "События: ${selected.format(DATE_FMT)}",
                color = c.text,
                fontWeight = FontWeight.SemiBold,
            )
        }

        if (loading) {
            item { Text("Загрузка…", color = c.textSecondary) }
        } else if (dayEvents.isEmpty()) {
            item {
                Text("Нет событий на этот день. Нажмите «+ Событие».",
                    color = c.textSecondary, fontSize = 13.sp)
            }
        } else {
            items(dayEvents) { event ->
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(12.dp))
                        .background(c.surfaceAlt)
                        .padding(12.dp),
                ) {
                    Text(event.title.ifBlank { "Без названия" }, color = c.text, fontWeight = FontWeight.Medium)
                    if (event.time.isNotBlank()) Text(event.time, color = c.textSecondary, fontSize = 12.sp)
                    if (event.description.isNotBlank()) Text(event.description, color = c.textSecondary, fontSize = 12.sp)
                    Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TextButton(onClick = { editing = event }) { Text("Изменить", color = c.action) }
                        TextButton(onClick = {
                            scope.launch {
                                runCatching { api.deleteEvent(event.id) }
                                    .onSuccess { status = "Удалено"; load() }
                                    .onFailure { status = "Ошибка: ${it.message}" }
                            }
                        }) { Text("Удалить", color = c.danger) }
                    }
                }
            }
        }

        item { Spacer(Modifier.height(24.dp)) }
    }

    if (showAdd) {
        EventDialog(
            title = "Новое событие",
            initialDate = selected,
            initialTitle = "",
            initialDesc = "",
            onDismiss = { showAdd = false },
            onSave = { t, startIso, desc ->
                scope.launch {
                    runCatching { api.createEvent(t, startIso, 60, desc) }
                        .onSuccess { status = "Событие создано"; showAdd = false; load() }
                        .onFailure { status = "Ошибка: ${it.message}" }
                }
            },
        )
    }

    editing?.let { ev ->
        EventDialog(
            title = "Изменить событие",
            initialDate = eventDate(ev) ?: selected,
            initialTitle = ev.title,
            initialDesc = ev.description,
            onDismiss = { editing = null },
            onSave = { t, startIso, desc ->
                scope.launch {
                    runCatching { api.updateEvent(ev.id, title = t, start = startIso, description = desc) }
                        .onSuccess { status = "Обновлено"; editing = null; load() }
                        .onFailure { status = "Ошибка: ${it.message}" }
                }
            },
        )
    }
}

@Composable
private fun EventDialog(
    title: String,
    initialDate: LocalDate,
    initialTitle: String,
    initialDesc: String,
    onDismiss: () -> Unit,
    onSave: (String, String, String) -> Unit,
) {
    val c = OmniPalette.colors
    var name by remember { mutableStateOf(initialTitle) }
    var date by remember { mutableStateOf(initialDate.format(DATE_FMT)) }
    var time by remember { mutableStateOf("10:00") }
    var desc by remember { mutableStateOf(initialDesc) }
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = c.surface,
        title = { Text(title, color = c.text) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("Название") }, singleLine = true)
                OutlinedTextField(value = date, onValueChange = { date = it }, label = { Text("Дата (ГГГГ-ММ-ДД)") }, singleLine = true)
                OutlinedTextField(value = time, onValueChange = { time = it }, label = { Text("Время (ЧЧ:ММ)") }, singleLine = true)
                OutlinedTextField(value = desc, onValueChange = { desc = it }, label = { Text("Описание") })
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    val startIso = "${date.trim()}T${time.trim().ifBlank { "10:00" }}:00"
                    if (name.trim().isNotBlank()) onSave(name.trim(), startIso, desc.trim())
                },
                colors = ButtonDefaults.buttonColors(containerColor = c.accent, contentColor = c.onAccent),
            ) { Text("Сохранить") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена", color = c.textSecondary) } },
    )
}

private fun monthText(month: YearMonth): String {
    val names = listOf(
        "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
    )
    return "${names[month.monthValue - 1]} ${month.year}"
}
