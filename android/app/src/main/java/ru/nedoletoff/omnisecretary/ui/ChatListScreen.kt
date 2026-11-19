package ru.nedoletoff.omnisecretary.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.HorizontalDivider
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
import ru.nedoletoff.omnisecretary.data.ChatSummary

@Composable
fun ChatListScreen(
    api: Api,
    reloadKey: Int,
    onOpen: (ChatSummary) -> Unit,
    onNewChat: () -> Unit,
    onPrivateChat: () -> Unit = onNewChat,
) {
    var chats by remember { mutableStateOf<List<ChatSummary>>(emptyList()) }
    var busy by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    val c = OmniPalette.colors

    suspend fun reload() {
        busy = true
        error = ""
        try {
            chats = api.chats()
        } catch (failure: Exception) {
            error = failure.message ?: "Не удалось загрузить чаты"
        } finally {
            busy = false
        }
    }

    LaunchedEffect(reloadKey) { reload() }

    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize()) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Чаты", style = MaterialTheme.typography.headlineSmall,
                    color = c.accent, modifier = Modifier.weight(1f))
                androidx.compose.material3.TextButton(onClick = onPrivateChat) {
                    Text("Приватный", color = c.info)
                }
            }
            when {
                busy && chats.isEmpty() -> Box(Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center) { CircularProgressIndicator() }
                error.isNotBlank() && chats.isEmpty() -> Box(Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(error, color = c.danger)
                        androidx.compose.material3.TextButton(onClick = { scope.launch { reload() } }) {
                            Text("Повторить")
                        }
                    }
                }
                chats.isEmpty() -> Box(Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center) {
                    Text("Чатов пока нет. Начните новый.", color = c.textSecondary)
                }
                else -> LazyColumn(Modifier.weight(1f)) {
                    items(chats, key = { it.id }) { chat ->
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { onOpen(chat) }
                                .padding(horizontal = 16.dp, vertical = 14.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(chat.title, style = MaterialTheme.typography.bodyLarge,
                                    color = c.text)
                                Text("нажмите, чтобы открыть",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = c.textSecondary)
                            }
                            IconButton(onClick = {
                                scope.launch {
                                    runCatching { api.deleteChat(chat.id) }
                                    reload()
                                }
                            }) {
                                Icon(Icons.Filled.Delete, contentDescription = "Удалить",
                                    tint = c.textSecondary)
                            }
                        }
                        HorizontalDivider(color = c.surfaceAlt)
                    }
                }
            }
        }
        ExtendedFloatingActionButton(
            onClick = onNewChat,
            icon = { Icon(Icons.Filled.Add, contentDescription = "Новый чат") },
            text = { Text("Новый чат") },
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(20.dp),
        )
    }
}
