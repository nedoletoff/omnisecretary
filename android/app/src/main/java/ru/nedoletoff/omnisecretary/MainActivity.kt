package ru.nedoletoff.omnisecretary

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.automirrored.filled.Note
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Today
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import ru.nedoletoff.omnisecretary.data.Api
import ru.nedoletoff.omnisecretary.data.Session
import ru.nedoletoff.omnisecretary.notify.Reminders
import ru.nedoletoff.omnisecretary.ui.ChatListScreen
import ru.nedoletoff.omnisecretary.ui.ChatScreen
import ru.nedoletoff.omnisecretary.ui.GruvboxBg
import ru.nedoletoff.omnisecretary.ui.GruvboxFg
import ru.nedoletoff.omnisecretary.ui.GruvboxGray
import ru.nedoletoff.omnisecretary.ui.GruvboxYellow
import ru.nedoletoff.omnisecretary.ui.LoginScreen
import ru.nedoletoff.omnisecretary.ui.NotesScreen
import ru.nedoletoff.omnisecretary.ui.OmniTheme
import ru.nedoletoff.omnisecretary.ui.SettingsScreen
import ru.nedoletoff.omnisecretary.ui.TodayScreen

private data class LaunchAction(
    val openVoice: Boolean = false,
    val openToday: Boolean = false,
    val sharedText: String = "",
    val sharedUri: android.net.Uri? = null,
)

class MainActivity : FragmentActivity() {
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
                    activity = this,
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
        var shared = ""
        var sharedUri: android.net.Uri? = null
        if (intent.action == Intent.ACTION_SEND) {
            shared = intent.getStringExtra(Intent.EXTRA_TEXT).orEmpty()
            sharedUri = intent.getParcelableExtra(Intent.EXTRA_STREAM)
        }
        val data = intent.data
        val path = data?.host ?: data?.path?.trim('/') ?: ""
        val voice = intent.action == ACTION_VOICE || path == "voice"
        val today = intent.action == ACTION_TODAY || path == "today"
        return LaunchAction(openVoice = voice, openToday = today,
            sharedText = shared, sharedUri = sharedUri)
    }

    companion object {
        const val ACTION_VOICE = "ru.nedoletoff.omnisecretary.VOICE"
        const val ACTION_TODAY = "ru.nedoletoff.omnisecretary.TODAY"
    }
}

@Composable
private fun BiometricGate(activity: FragmentActivity, onUnlocked: () -> Unit) {
    val context = LocalContext.current
    var error by remember { mutableStateOf("") }

    fun prompt() {
        val executor = ContextCompat.getMainExecutor(activity)
        val biometric = BiometricPrompt(activity, executor,
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    onUnlocked()
                }
                override fun onAuthenticationError(code: Int, msg: CharSequence) {
                    error = msg.toString()
                }
                override fun onAuthenticationFailed() {
                    error = "Не удалось подтвердить личность"
                }
            })
        val info = BiometricPrompt.PromptInfo.Builder()
            .setTitle("OmniSecretary")
            .setSubtitle("Подтвердите личность для входа")
            .setAllowedAuthenticators(
                BiometricManager.Authenticators.BIOMETRIC_STRONG or
                    BiometricManager.Authenticators.BIOMETRIC_WEAK or
                    BiometricManager.Authenticators.DEVICE_CREDENTIAL)
            .build()
        try {
            biometric.authenticate(info)
        } catch (e: Exception) {
            error = "Биометрия недоступна: ${e.message}"
            onUnlocked()
        }
    }

    LaunchedEffect(Unit) { prompt() }

    Box(Modifier.fillMaxSize().background(GruvboxBg), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
            modifier = Modifier.padding(24.dp)) {
            Icon(Icons.Filled.Lock, contentDescription = null, tint = GruvboxYellow)
            Text("Приложение заблокировано", color = GruvboxFg,
                style = MaterialTheme.typography.titleMedium)
            if (error.isNotBlank()) Text(error, color = GruvboxGray)
            Button(onClick = { prompt() }) { Text("Разблокировать") }
        }
    }
}

private data class Tab(val label: String, val icon: androidx.compose.ui.graphics.vector.ImageVector)

@Composable
private fun AppRoot(
    activity: FragmentActivity,
    action: LaunchAction,
    onActionConsumed: () -> Unit,
) {
    var loggedIn by remember { mutableStateOf(Session.isLoggedIn) }
    var unlocked by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val biometricEnabled = remember(loggedIn) {
        context.getSharedPreferences("omni_settings", Context.MODE_PRIVATE)
            .getBoolean("biometric_lock", false)
    }

    if (!loggedIn) {
        LoginScreen(onLoggedIn = { loggedIn = true; unlocked = false })
        return
    }

    // Биометрическая блокировка при входе в приложение (если включена).
    if (biometricEnabled && !unlocked) {
        val manager = BiometricManager.from(context)
        val canAuth = manager.canAuthenticate(
            BiometricManager.Authenticators.BIOMETRIC_STRONG or
                BiometricManager.Authenticators.BIOMETRIC_WEAK or
                BiometricManager.Authenticators.DEVICE_CREDENTIAL)
        if (canAuth == BiometricManager.BIOMETRIC_SUCCESS) {
            BiometricGate(activity = activity, onUnlocked = { unlocked = true })
            return
        } else {
            // Биометрия недоступна — не блокируем пользователя.
            unlocked = true
        }
    } else if (!biometricEnabled) {
        unlocked = true
    }

    val api = remember(Session.server, Session.token) { Api(Session.server, Session.token) }
    var tab by remember { mutableIntStateOf(0) }
    var openChat by remember { mutableStateOf<Pair<String, String>?>(null) }
    var tempChat by remember { mutableStateOf(false) }
    var draft by remember { mutableStateOf("") }
    var autoRecord by remember { mutableStateOf(false) }
    var sharedUri by remember { mutableStateOf<android.net.Uri?>(null) }
    var reloadChats by remember { mutableIntStateOf(0) }
    var reloadToday by remember { mutableIntStateOf(0) }

    // Внешние действия: виджет/плитка/ярлык/шаринг/deep link.
    LaunchedEffect(action) {
        if (action == LaunchAction()) return@LaunchedEffect
        if (action.openToday) {
            tab = 1
            reloadToday++
        }
        if (action.sharedText.isNotBlank() || action.sharedUri != null) {
            draft = action.sharedText
            sharedUri = action.sharedUri
            autoRecord = false
            tempChat = false
            tab = 0
            openChat = "" to "Новый чат"
        } else if (action.openVoice) {
            draft = ""
            autoRecord = true
            tempChat = false
            sharedUri = null
            tab = 0
            openChat = "" to "Голосовой запрос"
        }
        onActionConsumed()
    }

    val tabs = listOf(
        Tab("Чаты", Icons.AutoMirrored.Filled.Chat),
        Tab("Сегодня", Icons.Filled.Today),
        Tab("Заметки", Icons.AutoMirrored.Filled.Note),
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
            temporary = tempChat,
            initialAttachment = sharedUri,
            onBack = {
                openChat = null
                draft = ""
                autoRecord = false
                tempChat = false
                sharedUri = null
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
                        tempChat = false
                        openChat = summary.id to summary.title
                    },
                    onNewChat = {
                        draft = ""
                        autoRecord = false
                        tempChat = false
                        openChat = "" to "Новый чат"
                    },
                    onPrivateChat = {
                        draft = ""
                        autoRecord = false
                        tempChat = true
                        openChat = "" to "Приватный чат"
                    },
                )
            }
            1 -> Box(Modifier.padding(padding)) {
                TodayScreen(api = api, reloadKey = reloadToday)
            }
            2 -> Box(Modifier.padding(padding)) {
                NotesScreen(api = api)
            }
            else -> Box(Modifier.padding(padding)) {
                SettingsScreen(api = api, onLogout = { loggedIn = false; unlocked = false })
            }
        }
    }
}
