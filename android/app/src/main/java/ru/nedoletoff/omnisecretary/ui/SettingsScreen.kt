package ru.nedoletoff.omnisecretary.ui

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
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import ru.nedoletoff.omnisecretary.data.Api
import ru.nedoletoff.omnisecretary.data.Session

@Composable
fun SettingsScreen(api: Api, onLogout: () -> Unit) {
    val context = LocalContext.current
    val prefs = remember {
        context.getSharedPreferences("omni_settings", android.content.Context.MODE_PRIVATE)
    }
    var models by remember { mutableStateOf<List<Pair<String, String>>>(emptyList()) }
    var status by remember { mutableStateOf("") }
    var appNotifications by remember {
        mutableStateOf(prefs.getBoolean("notify_app", true))
    }
    var telegramEcho by remember {
        mutableStateOf(prefs.getBoolean("notify_telegram", false))
    }

    LaunchedEffect(Unit) {
        runCatching { api.models() }.onSuccess { models = it }
        runCatching { api.status() }.onSuccess { body ->
            val temp = body.optJSONObject("temperature")
            val memory = body.optJSONObject("memory")
            status = buildString {
                if (temp != null) append("Температура: ${temp.opt("value")} °C\n")
                if (memory != null) append("Память: ${memory.opt("used_mb")} / "
                        + "${memory.opt("total_mb")} МБ\n")
                body.optString("uptime").takeIf { it.isNotBlank() }
                    ?.let { append("Uptime: $it\n") }
            }.ifBlank { "Метрики недоступны" }
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text("Настройки", style = MaterialTheme.typography.headlineSmall, color = GruvboxYellow)
        Card(colors = CardDefaults.cardColors(containerColor = GruvboxBg1)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("Аккаунт", color = GruvboxYellow)
                Text(Session.email, color = GruvboxFg)
                Text(Session.server, color = GruvboxGray,
                    style = MaterialTheme.typography.bodySmall)
                Text("Роль: ${Session.role}", color = GruvboxGray,
                    style = MaterialTheme.typography.bodySmall)
            }
        }

        Card(colors = CardDefaults.cardColors(containerColor = GruvboxBg1)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("Модель", color = GruvboxYellow)
                if (models.isEmpty()) {
                    Text("Список недоступен", color = GruvboxGray,
                        style = MaterialTheme.typography.bodySmall)
                } else {
                    models.forEach { (id, name) ->
                        Row(
                            Modifier
                                .fillMaxWidth()
                                .clickable { Session.setModel(id) }
                                .padding(vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                if (id == Session.model) "● " else "○ ",
                                color = if (id == Session.model) GruvboxGreen else GruvboxGray,
                            )
                            Text(name, color = GruvboxFg)
                        }
                    }
                }
            }
        }

        Card(colors = CardDefaults.cardColors(containerColor = GruvboxBg1)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Уведомления", color = GruvboxYellow)
                Text("Каналы независимы: приложение и Telegram настраиваются отдельно.",
                    color = GruvboxGray, style = MaterialTheme.typography.bodySmall)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("В приложении", Modifier.weight(1f), color = GruvboxFg)
                    Switch(checked = appNotifications, onCheckedChange = {
                        appNotifications = it
                        prefs.edit().putBoolean("notify_app", it).apply()
                    })
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Дублировать в Telegram", Modifier.weight(1f), color = GruvboxFg)
                    Switch(checked = telegramEcho, onCheckedChange = {
                        telegramEcho = it
                        prefs.edit().putBoolean("notify_telegram", it).apply()
                    })
                }
            }
        }

        Card(colors = CardDefaults.cardColors(containerColor = GruvboxBg1)) {
            Column(Modifier.padding(16.dp)) {
                Text("Устройство", color = GruvboxYellow)
                Text(status.ifBlank { "Только для владельца" }, color = GruvboxFg,
                    style = MaterialTheme.typography.bodySmall)
            }
        }

        HorizontalDivider(color = GruvboxBg2)
        Button(onClick = {
            Session.clear()
            onLogout()
        }, modifier = Modifier.fillMaxWidth()) {
            Text("Выйти")
        }
        Text("OmniSecretary · self-hosted, данные на вашем Pi",
            color = GruvboxGray, style = MaterialTheme.typography.bodySmall)
    }
}
