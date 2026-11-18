package ru.nedoletoff.omnisecretary

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Today
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import ru.nedoletoff.omnisecretary.data.Api
import ru.nedoletoff.omnisecretary.data.Session
import ru.nedoletoff.omnisecretary.notify.Reminders
import ru.nedoletoff.omnisecretary.ui.ChatListScreen
import ru.nedoletoff.omnisecretary.ui.ChatScreen
import ru.nedoletoff.omnisecretary.ui.LoginScreen
import ru.nedoletoff.omnisecretary.ui.OmniTheme
import ru.nedoletoff.omnisecretary.ui.SettingsScreen
import ru.nedoletoff.omnisecretary.ui.TodayScreen

private data class LaunchAction(
    val openVoice: Boolean = false,
    val openToday: Boolean = false,
    val sharedText: String = "",
)

class MainActivity : ComponentActivity() {
    private val actionState = mutableStateOf(LaunchAction())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Session.init(applicationContext)
        Reminders.ensureChannel(applicationContext)
        enableEdgeToEdge()
        actionState.value = parseIntent(intent)
        setContent {
            OmniTheme {
                AppRoot(
                    action = actionState.value,
                    onActionConsumed = { actionState.value = LaunchAction() },
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        actionState.value = parseIntent(intent)
    }

    private fun parseIntent(intent: Intent?): LaunchAction {
        if (intent == null) return LaunchAction()
        val shared = when (intent.action) {
            Intent.ACTION_SEND -> intent.getStringExtra(Intent.EXTRA_TEXT).orEmpty()
            else -> ""
        }
        val data = intent.data
        val path = data?.host ?: data?.path?.trim('/') ?: ""
        val voice = intent.action == ACTION_VOICE || path == "voice"
        val today = intent.action == ACTION_TODAY || path == "today"
        return LaunchAction(openVoice = voice, openToday = today, sharedText = shared)
    }

    companion object {
        const val ACTION_VOICE = "ru.nedoletoff.omnisecretary.VOICE"
        const val ACTION_TODAY = "ru.nedoletoff.omnisecretary.TODAY"
    }
}

private data class Tab(val label: String, val icon: androidx.compose.ui.graphics.vector.ImageVector)

@Composable
private fun AppRoot(action: LaunchAction, onActionConsumed: () -> Unit) {
    var loggedIn by remember { mutableStateOf(Session.isLoggedIn) }
    if (!loggedIn) {
        LoginScreen(onLoggedIn = { loggedIn = true })
        return
    }

    val api = remember(Session.server, Session.token) { Api(Session.server, Session.token) }
    var tab by remember { mutableIntStateOf(0) }
    var openChat by remember { mutableStateOf<Pair<String, String>?>(null) }
    var draft by remember { mutableStateOf("") }
    var autoRecord by remember { mutableStateOf(false) }
    var reloadChats by remember { mutableIntStateOf(0) }
    var reloadToday by remember { mutableIntStateOf(0) }

    // Внешние действия: виджет/плитка/ярлык/шаринг/deep link.
    LaunchedEffect(action) {
        if (action == LaunchAction()) return@LaunchedEffect
        if (action.openToday) {
            tab = 1
            reloadToday++
        }
        if (action.sharedText.isNotBlank()) {
            draft = action.sharedText
            autoRecord = false
            tab = 0
            openChat = "" to "Новый чат"
        } else if (action.openVoice) {
            draft = ""
            autoRecord = true
            tab = 0
            openChat = "" to "Голосовой запрос"
        }
        onActionConsumed()
    }

    val tabs = listOf(
        Tab("Чаты", Icons.AutoMirrored.Filled.Chat),
        Tab("Сегодня", Icons.Filled.Today),
        Tab("Настройки", Icons.Filled.Settings),
    )

    val chat = openChat
    if (chat != null) {
        ChatScreen(
            api = api,
            chatId = chat.first,
            title = chat.second,
            draft = draft,
            autoRecord = autoRecord,
            onBack = {
                openChat = null
                draft = ""
                autoRecord = false
                reloadChats++
            },
        )
        return
    }

    Scaffold(
        bottomBar = {
            NavigationBar {
                tabs.forEachIndexed { index, item ->
                    NavigationBarItem(
                        selected = tab == index,
                        onClick = {
                            tab = index
                            if (index == 1) reloadToday++
                        },
                        icon = { Icon(item.icon, contentDescription = item.label) },
                        label = { Text(item.label) },
                    )
                }
            }
        },
    ) { padding ->
        when (tab) {
            0 -> Box(Modifier.padding(padding)) {
                ChatListScreen(
                    api = api,
                    reloadKey = reloadChats,
                    onOpen = { summary ->
                        draft = ""
                        autoRecord = false
                        openChat = summary.id to summary.title
                    },
                    onNewChat = {
                        draft = ""
                        autoRecord = false
                        openChat = "" to "Новый чат"
                    },
                )
            }
            1 -> Box(Modifier.padding(padding)) {
                TodayScreen(api = api, reloadKey = reloadToday)
            }
            else -> Box(Modifier.padding(padding)) {
                SettingsScreen(api = api, onLogout = { loggedIn = false })
            }
        }
    }
}
