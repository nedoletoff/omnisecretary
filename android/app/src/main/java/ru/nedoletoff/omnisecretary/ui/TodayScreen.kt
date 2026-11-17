package ru.nedoletoff.omnisecretary.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import ru.nedoletoff.omnisecretary.data.Api
import ru.nedoletoff.omnisecretary.data.TodayData

@Composable
fun TodayScreen(api: Api, reloadKey: Int) {
    var data by remember { mutableStateOf<TodayData?>(null) }
    var error by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(true) }
    val scope = rememberCoroutineScope()

    suspend fun load() {
        busy = true
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

        Section("События", today.events.map { "${it.first}  ${it.second}" })
        Section("Задачи", today.tasks)
        Section("Заметки", today.notes)
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
