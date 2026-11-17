package ru.nedoletoff.omnisecretary

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Chat
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Today
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import ru.nedoletoff.omnisecretary.data.Api
import ru.nedoletoff.omnisecretary.data.Session
import ru.nedoletoff.omnisecretary.ui.ChatListScreen
import ru.nedoletoff.omnisecretary.ui.ChatScreen
import ru.nedoletoff.omnisecretary.ui.LoginScreen
import ru.nedoletoff.omnisecretary.ui.OmniTheme
import ru.nedoletoff.omnisecretary.ui.SettingsScreen
import ru.nedoletoff.omnisecretary.ui.TodayScreen

class MainActivity : ComponentActivity() {

    private val notifications = registerForActivityResult(
        ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Session.init(this)
        enableEdgeToEdge()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        val openVoice = intent?.action == ACTION_VOICE
        val sharedText = when (intent?.action) {
            Intent.ACTION_SEND -> intent?.getStringExtra(Intent.EXTRA_TEXT).orEmpty()
            else -> ""
        }

        setContent {
            OmniTheme {
                AppRoot(
                    startVoice = openVoice,
                    sharedText = sharedText,
                    onShareHandled = { intent?.action = null },
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
    }

    companion object {
        const val ACTION_VOICE = "ru.nedoletoff.omnisecretary.VOICE"
        const val ACTION_TODAY = "ru.nedoletoff.omnisecretary.TODAY"
    }
}

private enum class Tab { Chats, Today, Settings }

@Composable
private fun AppRoot(
    startVoice: Boolean,
    sharedText: String,
    onShareHandled: () -> Unit,
) {
    var loggedIn by remember { mutableStateOf(Session.isLoggedIn) }
    var tab by remember { mutableStateOf(Tab.Chats) }
    var reloadKey by remember { mutableStateOf(0) }
    // Текст, которым поделились из другого приложения, сразу открываем
    // в новом чате — это и есть «поделиться в OmniSecretary».
    var openChat by remember {
        mutableStateOf<Pair<String, String>?>(
            if (sharedText.isNotBlank()) "" to "Новый чат" else null)
    }
    var draft by remember { mutableStateOf(sharedText) }
    var autoRecord by remember { mutableStateOf(startVoice) }

    if (!loggedIn) {
        LoginScreen(onLoggedIn = { loggedIn = true })
        return
    }

    val api = remember(Session.token, Session.server) { Api(Session.server, Session.token) }

    val current = openChat
    if (current != null) {
        ChatScreen(
            api = api,
            chatId = current.first,
            title = current.second,
            draft = draft,
            autoRecord = autoRecord,
            onBack = {
                openChat = null
                autoRecord = false
                draft = ""
                reloadKey += 1
            },
        )
        return
    }

    Scaffold(
        bottomBar = {
            NavigationBar {
                NavigationBarItem(
                    selected = tab == Tab.Chats,
                    onClick = { tab = Tab.Chats },
                    icon = { Icon(Icons.Filled.Chat, contentDescription = null) },
                    label = { Text("Чаты") },
                )
                NavigationBarItem(
                    selected = tab == Tab.Today,
                    onClick = { tab = Tab.Today },
                    icon = { Icon(Icons.Filled.Today, contentDescription = null) },
                    label = { Text("Сегодня") },
                )
                NavigationBarItem(
                    selected = tab == Tab.Settings,
                    onClick = { tab = Tab.Settings },
                    icon = { Icon(Icons.Filled.Settings, contentDescription = null) },
                    label = { Text("Настройки") },
                )
            }
        },
        containerColor = ru.nedoletoff.omnisecretary.ui.GruvboxBg,
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when (tab) {
                Tab.Chats -> ChatListScreen(
                    api = api,
                    reloadKey = reloadKey,
                    onOpen = { chat -> openChat = chat.id to chat.title },
                    onNewChat = {
                        openChat = "" to "Новый чат"
                        draft = ""
                        onShareHandled()
                    },
                )
                Tab.Today -> TodayScreen(api = api, reloadKey = reloadKey)
                Tab.Settings -> SettingsScreen(api = api, onLogout = { loggedIn = false })
            }
        }
    }
}
