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
) {
    var chats by remember { mutableStateOf<List<ChatSummary>>(emptyList()) }
    var busy by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()

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
        when {
            busy && chats.isEmpty() -> CircularProgressIndicator(
                Modifier.align(Alignment.Center))
            error.isNotBlank() && chats.isEmpty() -> Text(
                error, color = GruvboxRed, modifier = Modifier.align(Alignment.Center))
            chats.isEmpty() -> Text(
                "Чатов пока нет. Начните новый.",
                color = GruvboxGray, modifier = Modifier.align(Alignment.Center))
            else -> LazyColumn(Modifier.fillMaxSize()) {
                items(chats, key = { it.id }) { chat ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { onOpen(chat) }
                            .padding(horizontal = 16.dp, vertical = 14.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(chat.title, style = MaterialTheme.typography.bodyLarge)
                            Text("нажмите, чтобы открыть",
                                style = MaterialTheme.typography.bodySmall,
                                color = GruvboxGray)
                        }
                        IconButton(onClick = {
                            scope.launch {
                                runCatching { api.deleteChat(chat.id) }
                                reload()
                            }
                        }) {
                            Icon(Icons.Filled.Delete, contentDescription = "Удалить",
                                tint = GruvboxGray)
                        }
                    }
                    HorizontalDivider(color = GruvboxBg2)
                }
            }
        }
        ExtendedFloatingActionButton(
            onClick = onNewChat,
            icon = { Icon(Icons.Filled.Add, contentDescription = null) },
            text = { Text("Новый чат") },
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(20.dp),
        )
    }
}
