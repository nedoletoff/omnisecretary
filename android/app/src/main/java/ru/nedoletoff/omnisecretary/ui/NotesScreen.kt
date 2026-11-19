package ru.nedoletoff.omnisecretary.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Save
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.Switch
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import ru.nedoletoff.omnisecretary.data.Api
import ru.nedoletoff.omnisecretary.data.OwuiNote
import ru.nedoletoff.omnisecretary.data.VaultConfig
import ru.nedoletoff.omnisecretary.data.VaultFile
import ru.nedoletoff.omnisecretary.data.VaultPermission
import ru.nedoletoff.omnisecretary.data.VaultResult

@Composable
fun NotesScreen(api: Api) {
    val c = OmniPalette.colors
    var tab by remember { mutableIntStateOf(0) }
    val tabs = listOf("Заметки", "Obsidian")

    Column(Modifier.fillMaxSize()) {
        TabRow(selectedTabIndex = tab, containerColor = c.surface,
            contentColor = c.accent) {
            tabs.forEachIndexed { index, title ->
                Tab(selected = tab == index, onClick = { tab = index },
                    text = { Text(title, color = if (tab == index) c.accent else c.textSecondary) })
            }
        }
        when (tab) {
            0 -> OwuiNotesTab(api)
            1 -> VaultTab(api)
        }
    }
}

@Composable
private fun OwuiNotesTab(api: Api) {
    val c = OmniPalette.colors
    val scope = rememberCoroutineScope()
    var notes by remember { mutableStateOf<List<OwuiNote>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf("") }
    var editing by remember { mutableStateOf<OwuiNote?>(null) }
    var creating by remember { mutableStateOf(false) }

    suspend fun load() {
        loading = true; error = ""
        try { notes = api.owuiNotes() }
        catch (e: Exception) { error = e.message ?: "Ошибка загрузки" }
        finally { loading = false }
    }
    LaunchedEffect(Unit) { load() }

    if (editing != null) {
        NoteEditor(
            title = editing!!.title,
            content = editing!!.content,
            isNew = false,
            onBack = { editing = null },
            onSave = { title, content ->
                scope.launch {
                    try {
                        api.updateOwuiNote(editing!!.id, title, content)
                        editing = null; load()
                    } catch (e: Exception) { error = e.message ?: "Не удалось сохранить" }
                }
            },
            onDelete = {
                scope.launch {
                    try { api.deleteOwuiNote(editing!!.id); editing = null; load() }
                    catch (e: Exception) { error = e.message ?: "Не удалось удалить" }
                }
            }
        )
        return
    }
    if (creating) {
        NoteEditor(
            title = "", content = "", isNew = true,
            onBack = { creating = false },
            onSave = { title, content ->
                scope.launch {
                    try { api.createOwuiNote(title.ifBlank { "Заметка" }, content); creating = false; load() }
                    catch (e: Exception) { error = e.message ?: "Не удалось создать" }
                }
            },
            onDelete = null
        )
        return
    }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Заметки", style = MaterialTheme.typography.headlineSmall,
                color = c.accent, modifier = Modifier.weight(1f))
            IconButton(onClick = { scope.launch { load() } }) {
                Icon(Icons.Filled.Refresh, "Обновить", tint = c.action)
            }
            IconButton(onClick = { creating = true }) {
                Icon(Icons.Filled.Add, "Новая заметка", tint = c.success)
            }
        }
        Text("Заметки Open WebUI — хранятся на вашем сервере и доступны в веб-интерфейсе. Нажмите на заметку, чтобы открыть и изменить.",
            color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
        if (error.isNotBlank()) Text(error, color = c.danger)
        if (loading) { CircularProgressIndicator(); return@Column }
        if (notes.isEmpty()) {
            Card(colors = CardDefaults.cardColors(containerColor = c.surface),
                modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("Пока нет заметок", color = c.text)
                    Text("Нажмите «+», чтобы создать первую заметку. Она сохранится на сервере.",
                        color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
                }
            }
            return@Column
        }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(notes, key = { it.id }) { note ->
                Card(colors = CardDefaults.cardColors(containerColor = c.surface),
                    modifier = Modifier.fillMaxWidth().clickable {
                        scope.launch {
                            try { editing = api.owuiNote(note.id) }
                            catch (e: Exception) { error = e.message ?: "Не удалось открыть" }
                        }
                    }) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text(note.title.ifBlank { "Без названия" }, color = c.text,
                            style = MaterialTheme.typography.titleMedium)
                        Text("Нажмите, чтобы открыть", color = c.textSecondary,
                            style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
    }
}

@Composable
private fun NoteEditor(
    title: String, content: String, isNew: Boolean,
    onBack: () -> Unit, onSave: (String, String) -> Unit, onDelete: (() -> Unit)?,
) {
    val c = OmniPalette.colors
    var t by remember { mutableStateOf(title) }
    var body by remember { mutableStateOf(content) }
    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) { Icon(Icons.Filled.ArrowBack, "Назад", tint = c.text) }
            Text(if (isNew) "Новая заметка" else "Заметка",
                style = MaterialTheme.typography.titleMedium, color = c.accent,
                modifier = Modifier.weight(1f))
            if (onDelete != null) {
                IconButton(onClick = onDelete) { Icon(Icons.Filled.Delete, "Удалить", tint = c.danger) }
            }
            IconButton(onClick = { onSave(t, body) }) { Icon(Icons.Filled.Save, "Сохранить", tint = c.success) }
        }
        OutlinedTextField(t, { t = it }, label = { Text("Заголовок") },
            modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(body, { body = it }, label = { Text("Текст") },
            modifier = Modifier.fillMaxWidth().weight(1f))
    }
}

@Composable
private fun VaultTab(api: Api) {
    val c = OmniPalette.colors
    val scope = rememberCoroutineScope()
    var config by remember { mutableStateOf<VaultConfig?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf("") }
    var notice by remember { mutableStateOf("") }
    var showConfig by remember { mutableStateOf(false) }

    // браузер
    var query by remember { mutableStateOf("") }
    var files by remember { mutableStateOf<List<VaultFile>>(emptyList()) }
    var results by remember { mutableStateOf<List<VaultResult>>(emptyList()) }
    var opened by remember { mutableStateOf<Pair<String, String>?>(null) }
    var browsing by remember { mutableStateOf(false) }

    suspend fun loadConfig() {
        loading = true; error = ""
        try { config = api.vaultConfig() }
        catch (e: Exception) { error = e.message ?: "Ошибка загрузки настроек вольта" }
        finally { loading = false }
    }

    fun loadFiles(q: String = "") {
        scope.launch {
            browsing = true; error = ""; results = emptyList()
            try { files = api.vaultFiles(q) }
            catch (e: Exception) {
                val msg = e.message ?: "Ошибка"
                when {
                    msg.contains("NOT_CONFIGURED") -> { config = config?.copy(configured = false) ?: config }
                    msg.contains("NOT_READY") -> { config = config?.copy(ready = false) ?: config }
                    else -> error = msg
                }
            } finally { browsing = false }
        }
    }

    fun search() {
        val q = query.trim()
        if (q.isBlank()) { loadFiles(); return }
        scope.launch {
            browsing = true; error = ""
            try { results = api.vaultSearch(q); files = emptyList() }
            catch (e: Exception) {
                val msg = e.message ?: "Ошибка поиска"
                when {
                    msg.contains("NOT_CONFIGURED") -> { config = config?.copy(configured = false) ?: config }
                    msg.contains("NOT_READY") -> { config = config?.copy(ready = false) ?: config }
                    else -> error = msg
                }
            } finally { browsing = false }
        }
    }

    LaunchedEffect(Unit) {
        loadConfig()
    }

    // после загрузки конфига — подгружаем файлы, если готово
    LaunchedEffect(config?.configured, config?.ready) {
        val cfg = config
        if (cfg?.configured == true && cfg.ready && files.isEmpty() && results.isEmpty() && !browsing) {
            loadFiles()
        }
    }

    if (opened != null) {
        Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = { opened = null }) {
                    Icon(Icons.Filled.ArrowBack, contentDescription = "Назад", tint = c.text)
                }
                Text(opened!!.first, style = MaterialTheme.typography.titleSmall,
                    color = c.accent, modifier = Modifier.weight(1f))
            }
            Text(opened!!.second, color = c.text,
                modifier = Modifier.weight(1f).verticalScroll(rememberScrollState()))
        }
        return
    }

    if (showConfig || (config != null && config!!.configured.not())) {
        VaultConfigScreen(
            api = api,
            initial = config,
            onClose = { showConfig = false; scope.launch { loadConfig() } },
            onSaved = { showConfig = false; notice = it; scope.launch { loadConfig(); loadFiles() } },
        )
        return
    }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Obsidian-вольт", style = MaterialTheme.typography.headlineSmall,
                color = c.accent, modifier = Modifier.weight(1f))
            IconButton(onClick = {
                scope.launch {
                    notice = "Синхронизация…"
                    try {
                        val r = api.syncVault()
                        notice = if (r.optBoolean("ok", true)) "Синхронизировано" else "Ошибка синхронизации"
                        loadConfig(); loadFiles()
                    } catch (e: Exception) { notice = e.message ?: "Ошибка синхронизации" }
                }
            }) { Icon(Icons.Filled.Sync, contentDescription = "Синхронизировать", tint = c.action) }
            IconButton(onClick = { showConfig = true }) {
                Icon(Icons.Filled.Settings, contentDescription = "Настройки вольта", tint = c.text)
            }
        }
        Text("Ваш личный Obsidian-вольт в GitHub. Просмотр, поиск и права доступа настраиваются здесь; записи создаются с коммитом в разрешённые папки.",
            color = c.textSecondary, style = MaterialTheme.typography.bodySmall)

        val cfg = config
        if (loading) { CircularProgressIndicator(); return@Column }
        if (cfg != null && cfg.configured && !cfg.ready) {
            Text("Вольт подключён, но не готов: ${cfg.status}", color = c.warning)
            if (cfg.lastError.isNotBlank()) Text(cfg.lastError, color = c.danger,
                style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(onClick = {
                    scope.launch {
                        notice = "Подключение…"
                        try { api.syncVault(); loadConfig(); loadFiles(); notice = "Готово" }
                        catch (e: Exception) { notice = e.message ?: "Ошибка" }
                    }
                }) { Text("Подключить / синхронизировать") }
                TextButton(onClick = { showConfig = true }) { Text("Изменить настройки") }
            }
            if (notice.isNotBlank()) Text(notice, color = c.info)
            return@Column
        }

        if (notice.isNotBlank()) Text(notice, color = c.info, style = MaterialTheme.typography.bodySmall)
        Row(verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(query, { query = it }, label = { Text("Поиск") },
                modifier = Modifier.weight(1f), singleLine = true)
            IconButton(onClick = { search() }) {
                Icon(Icons.Filled.Search, contentDescription = "Искать", tint = c.action)
            }
            IconButton(onClick = { query = ""; loadFiles() }) {
                Icon(Icons.Filled.Refresh, contentDescription = "Обновить", tint = c.text)
            }
        }
        if (error.isNotBlank()) Text(error, color = c.danger)
        if (browsing) { CircularProgressIndicator(); return@Column }
        if (results.isNotEmpty()) {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(results) { r ->
                    Card(colors = CardDefaults.cardColors(containerColor = c.surface),
                        modifier = Modifier.fillMaxWidth().clickable {
                            scope.launch {
                                try { opened = r.path to api.vaultRead(r.path) }
                                catch (e: Exception) { error = e.message ?: "Нет доступа или файл не найден" }
                            }
                        }) {
                        Column(Modifier.padding(12.dp)) {
                            Text(r.path, color = c.info, style = MaterialTheme.typography.titleSmall)
                            if (r.snippet.isNotBlank())
                                Text(r.snippet, color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        } else {
            if (files.isEmpty() && error.isBlank())
                Text("Нет доступных заметок (проверьте права на папки в настройках вольта).", color = c.textSecondary)
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(files, key = { it.path }) { f ->
                    Card(colors = CardDefaults.cardColors(containerColor = c.surface),
                        modifier = Modifier.fillMaxWidth().clickable {
                            scope.launch {
                                try { opened = f.path to api.vaultRead(f.path) }
                                catch (e: Exception) { error = e.message ?: "Нет доступа или файл не найден" }
                            }
                        }) {
                        Text(f.path, color = c.text, modifier = Modifier.padding(12.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun VaultConfigScreen(
    api: Api,
    initial: VaultConfig?,
    onClose: () -> Unit,
    onSaved: (String) -> Unit,
) {
    val c = OmniPalette.colors
    val scope = rememberCoroutineScope()
    var repoUrl by remember { mutableStateOf(initial?.repoUrl ?: "") }
    var branch by remember { mutableStateOf(initial?.branch ?: "main") }
    var token by remember { mutableStateOf("") }
    var showToken by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf(initial?.lastError ?: "") }
    var perms by remember {
        mutableStateOf(
            if (initial != null && initial.permissions.isNotEmpty()) initial.permissions
            else defaultVaultPermissions()
        )
    }
    var showPermsHelp by remember { mutableStateOf(false) }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onClose) {
                Icon(Icons.Filled.ArrowBack, contentDescription = "Назад", tint = c.text)
            }
            Text("Настройка Obsidian-вольта", style = MaterialTheme.typography.titleMedium,
                color = c.accent, modifier = Modifier.weight(1f))
        }

        Text("Подключите свой приватный репозиторий GitHub. Токен хранится в зашифрованном виде на сервере и используется только для синхронизации этого вольта.",
            color = c.textSecondary, style = MaterialTheme.typography.bodySmall)

        OutlinedTextField(repoUrl, { repoUrl = it },
            label = { Text("URL репозитория (https)") },
            placeholder = { Text("https://github.com/user/vault.git") },
            modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(branch, { branch = it }, label = { Text("Ветка") },
            modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(
            token, { token = it },
            label = { Text(if (initial?.hasToken == true) "Новый GitHub PAT (пусто — оставить текущий)" else "GitHub PAT (Contents: read/write)") },
            modifier = Modifier.fillMaxWidth(), singleLine = true,
            visualTransformation = if (showToken) VisualTransformation.None else PasswordVisualTransformation(),
            trailingIcon = {
                IconButton(onClick = { showToken = !showToken }) {
                    Icon(if (showToken) Icons.Filled.VisibilityOff else Icons.Filled.Visibility,
                        contentDescription = "Показать токен", tint = c.textSecondary)
                }
            },
        )

        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Права доступа нейросети по папкам", color = c.accent,
                style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
            TextButton(onClick = { showPermsHelp = !showPermsHelp }) { Text("?") }
        }
        if (showPermsHelp) {
            Text("Права проверяются программно на сервере при каждом действии — ассистент не может их обойти. " +
                "Укажите папки/префиксы (например AI/, Дневник/, или пусто для всего вольта). " +
                "Для пути применяется самое точное (длинное) совпадающее правило. " +
                "Если правило не найдено — доступ запрещён.",
                color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
        }

        perms.forEachIndexed { index, rule ->
            Card(colors = CardDefaults.cardColors(containerColor = c.surface),
                modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(
                            rule.path, { v ->
                                perms = perms.toMutableList().also { it[index] = rule.copy(path = v) }
                            },
                            label = { Text("Папка/префикс (пусто = весь вольт)") },
                            modifier = Modifier.weight(1f), singleLine = true)
                        if (perms.size > 1) {
                            IconButton(onClick = {
                                perms = perms.toMutableList().also { it.removeAt(index) }
                            }) { Icon(Icons.Filled.Delete, contentDescription = "Удалить правило", tint = c.danger) }
                        }
                    }
                    PermissionToggles(rule) { updated ->
                        perms = perms.toMutableList().also { it[index] = updated }
                    }
                }
            }
        }
        TextButton(onClick = {
            perms = perms + VaultPermission(path = "", read = true, list = true, search = true)
        }) { Text("+ Добавить правило для папки") }

        if (error.isNotBlank()) Text(error, color = c.danger)

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (initial?.configured == true) {
                TextButton(onClick = {
                    scope.launch {
                        busy = true; error = ""
                        try { api.disconnectVault(removeLocal = false); onSaved("Вольт отключён") }
                        catch (e: Exception) { error = e.message ?: "Ошибка" }
                        finally { busy = false }
                    }
                }, enabled = !busy) { Text("Отключить", color = c.danger) }
            }
            TextButton(onClick = { if (repoUrl.isNotBlank()) scope.launch {
                busy = true; error = ""
                try {
                    val res = api.saveVaultConfig(repoUrl.trim(), branch.trim().ifBlank { "main" },
                        token.trim().ifBlank { null }, perms)
                    if (res.optBoolean("ok", false) || res.optString("status") == "ready") {
                        onSaved("Вольт подключён")
                    } else {
                        error = res.optString("error").ifBlank { "Не удалось подключить (проверьте URL и токен)" }
                    }
                } catch (e: Exception) {
                    error = e.message ?: "Ошибка сохранения"
                } finally { busy = false }
            } }, enabled = !busy && repoUrl.isNotBlank()) {
                if (busy) CircularProgressIndicator(Modifier.padding(4.dp)) else Text("Сохранить и подключить")
            }
        }

        Text("Важно: ассистент сможет читать/писать/изменять только то, что вы явно разрешили выше. " +
            "Запись без соответствующего права блокируется на сервере.",
            color = c.textSecondary, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun PermissionToggles(rule: VaultPermission, onChange: (VaultPermission) -> Unit) {
    val c = OmniPalette.colors
    val items = listOf(
        Triple("Чтение", "read", rule.read),
        Triple("Список", "list", rule.list),
        Triple("Поиск", "search", rule.search),
        Triple("Создание", "create", rule.create),
        Triple("Дополнение", "append", rule.append),
        Triple("Изменение", "modify", rule.modify),
        Triple("Удаление", "delete", rule.delete),
    )
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        items.chunked(2).forEach { rowItems ->
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                rowItems.forEach { (label, key, value) ->
                    Row(verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.weight(1f)) {
                        Switch(checked = value, onCheckedChange = { checked ->
                            onChange(when (key) {
                                "read" -> rule.copy(read = checked)
                                "list" -> rule.copy(list = checked)
                                "search" -> rule.copy(search = checked)
                                "create" -> rule.copy(create = checked)
                                "append" -> rule.copy(append = checked)
                                "modify" -> rule.copy(modify = checked)
                                "delete" -> rule.copy(delete = checked)
                                else -> rule
                            })
                        })
                        Text(label, color = c.text, style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.padding(start = 4.dp))
                    }
                }
                if (rowItems.size == 1) androidx.compose.foundation.layout.Spacer(Modifier.weight(1f))
            }
        }
    }
}

private fun defaultVaultPermissions(): List<VaultPermission> = listOf(
    VaultPermission(path = "", read = true, list = true, search = true),
    VaultPermission(path = "AI/", read = true, list = true, search = true,
        create = true, append = true, modify = true, delete = false),
)

