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
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import ru.nedoletoff.omnisecretary.data.Api
import ru.nedoletoff.omnisecretary.data.OwuiNote
import ru.nedoletoff.omnisecretary.data.VaultFile
import ru.nedoletoff.omnisecretary.data.VaultResult

@Composable
fun NotesScreen(api: Api) {
    var tab by remember { mutableIntStateOf(0) }
    val tabs = listOf("Заметки", "Obsidian")

    Column(Modifier.fillMaxSize()) {
        TabRow(selectedTabIndex = tab, containerColor = GruvboxBg1,
            contentColor = GruvboxYellow) {
            tabs.forEachIndexed { index, title ->
                Tab(selected = tab == index, onClick = { tab = index },
                    text = { Text(title, color = if (tab == index) GruvboxYellow else GruvboxGray) })
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
                color = GruvboxYellow, modifier = Modifier.weight(1f))
            IconButton(onClick = { scope.launch { load() } }) {
                Icon(Icons.Filled.Refresh, "Обновить", tint = GruvboxBlue)
            }
            IconButton(onClick = { creating = true }) {
                Icon(Icons.Filled.Add, "Новая", tint = GruvboxGreen)
            }
        }
        if (error.isNotBlank()) Text(error, color = GruvboxRed)
        if (loading) { CircularProgressIndicator(); return@Column }
        if (notes.isEmpty()) { Text("Нет заметок", color = GruvboxGray); return@Column }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(notes, key = { it.id }) { note ->
                Card(colors = CardDefaults.cardColors(containerColor = GruvboxBg1),
                    modifier = Modifier.fillMaxWidth().clickable {
                        scope.launch {
                            try { editing = api.owuiNote(note.id) }
                            catch (e: Exception) { error = e.message ?: "Не удалось открыть" }
                        }
                    }) {
                    Column(Modifier.padding(16.dp)) {
                        Text(note.title.ifBlank { "Без названия" }, color = GruvboxFg,
                            style = MaterialTheme.typography.titleMedium)
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
    var t by remember { mutableStateOf(title) }
    var c by remember { mutableStateOf(content) }
    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) { Icon(Icons.Filled.ArrowBack, "Назад", tint = GruvboxFg) }
            Text(if (isNew) "Новая заметка" else "Заметка",
                style = MaterialTheme.typography.titleMedium, color = GruvboxYellow,
                modifier = Modifier.weight(1f))
            if (onDelete != null) {
                IconButton(onClick = onDelete) { Icon(Icons.Filled.Delete, "Удалить", tint = GruvboxRed) }
            }
            IconButton(onClick = { onSave(t, c) }) { Icon(Icons.Filled.Save, "Сохранить", tint = GruvboxGreen) }
        }
        OutlinedTextField(t, { t = it }, label = { Text("Заголовок") },
            modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(c, { c = it }, label = { Text("Текст") },
            modifier = Modifier.fillMaxWidth().weight(1f))
    }
}

@Composable
private fun VaultTab(api: Api) {
    val scope = rememberCoroutineScope()
    var query by remember { mutableStateOf("") }
    var files by remember { mutableStateOf<List<VaultFile>>(emptyList()) }
    var results by remember { mutableStateOf<List<VaultResult>>(emptyList()) }
    var opened by remember { mutableStateOf<Pair<String, String>?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf("") }
    var forbidden by remember { mutableStateOf(false) }

    fun loadFiles(q: String = "") {
        scope.launch {
            loading = true; error = ""; results = emptyList()
            try { files = api.vaultFiles(q) }
            catch (e: Exception) {
                val msg = e.message ?: ""
                if (msg.contains("403") || msg.contains("владельц", true)) forbidden = true
                else error = msg.ifBlank { "Ошибка" }
            } finally { loading = false }
        }
    }
    fun search() {
        if (query.isBlank()) { loadFiles(); return }
        scope.launch {
            loading = true; error = ""
            try { results = api.vaultSearch(query); files = emptyList() }
            catch (e: Exception) { error = e.message ?: "Ошибка поиска" }
            finally { loading = false }
        }
    }
    LaunchedEffect(Unit) { loadFiles() }

    if (opened != null) {
        Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = { opened = null }) { Icon(Icons.Filled.ArrowBack, "Назад", tint = GruvboxFg) }
                Text(opened!!.first, style = MaterialTheme.typography.titleSmall,
                    color = GruvboxYellow, modifier = Modifier.weight(1f))
            }
            Text(opened!!.second, color = GruvboxFg,
                modifier = Modifier.weight(1f).verticalScroll(rememberScrollState()))
        }
        return
    }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Obsidian-вольт", style = MaterialTheme.typography.headlineSmall, color = GruvboxYellow)
        if (forbidden) {
            Text("Вольт доступен только владельцу.", color = GruvboxGray)
            return@Column
        }
        Row(verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(query, { query = it }, label = { Text("Поиск") },
                modifier = Modifier.weight(1f), singleLine = true)
            IconButton(onClick = { search() }) { Icon(Icons.Filled.Search, "Искать", tint = GruvboxBlue) }
        }
        if (error.isNotBlank()) Text(error, color = GruvboxRed)
        if (loading) { CircularProgressIndicator(); return@Column }
        if (results.isNotEmpty()) {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(results) { r ->
                    Card(colors = CardDefaults.cardColors(containerColor = GruvboxBg1),
                        modifier = Modifier.fillMaxWidth().clickable {
                            scope.launch {
                                try { opened = r.path to api.vaultRead(r.path) }
                                catch (e: Exception) { error = e.message ?: "Не удалось открыть" }
                            }
                        }) {
                        Column(Modifier.padding(12.dp)) {
                            Text(r.path, color = GruvboxAqua, style = MaterialTheme.typography.titleSmall)
                            if (r.snippet.isNotBlank())
                                Text(r.snippet, color = GruvboxGray, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(files, key = { it.path }) { f ->
                    Card(colors = CardDefaults.cardColors(containerColor = GruvboxBg1),
                        modifier = Modifier.fillMaxWidth().clickable {
                            scope.launch {
                                try { opened = f.path to api.vaultRead(f.path) }
                                catch (e: Exception) { error = e.message ?: "Не удалось открыть" }
                            }
                        }) {
                        Text(f.path, color = GruvboxFg, modifier = Modifier.padding(12.dp))
                    }
                }
            }
        }
    }
}
