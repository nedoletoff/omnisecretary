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

    LaunchedEffect(Unit) {
        runCatching { api.models() }.onSuccess { models = it }
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
        Text("Настройки", style = MaterialTheme.typography.headlineSmall, color = GruvboxYellow)
        Card(colors = CardDefaults.cardColors(containerColor = GruvboxBg1)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("Аккаунт", color = GruvboxYellow)
                Text(Session.email.ifBlank { "—" }, color = GruvboxFg)
                Text(Session.server, color = GruvboxGray,
                    style = MaterialTheme.typography.bodySmall)
                Text("Роль: ${Session.role.ifBlank { "—" }}", color = GruvboxGray,
                    style = MaterialTheme.typography.bodySmall)
            }
        }

        Card(colors = CardDefaults.cardColors(containerColor = GruvboxBg1)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("Модель по умолчанию", color = GruvboxYellow)
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
                            Column {
                                Text(name, color = GruvboxFg)
                                if (name != id) Text(id, color = GruvboxGray,
                                    style = MaterialTheme.typography.labelSmall)
                            }
                        }
                    }
                }
            }
        }

        Card(colors = CardDefaults.cardColors(containerColor = GruvboxBg1)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Безопасность", color = GruvboxYellow)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Блокировка биометрией", color = GruvboxFg)
                        Text("Запрашивать отпечаток/лицо при входе в приложение",
                            color = GruvboxGray, style = MaterialTheme.typography.bodySmall)
                    }
                    Switch(checked = biometric, onCheckedChange = {
                        biometric = it
                        prefs.edit().putBoolean("biometric_lock", it).apply()
                    })
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
                when {
                    status.isNotBlank() -> Text(status, color = GruvboxFg,
                        style = MaterialTheme.typography.bodySmall)
                    statusError.isNotBlank() -> Text("Недоступно: $statusError", color = GruvboxGray,
                        style = MaterialTheme.typography.bodySmall)
                    else -> Text("Только для владельца", color = GruvboxGray,
                        style = MaterialTheme.typography.bodySmall)
                }
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
