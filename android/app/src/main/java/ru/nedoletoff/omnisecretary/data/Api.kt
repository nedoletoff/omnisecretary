package ru.nedoletoff.omnisecretary.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

data class ChatSummary(val id: String, val title: String, val updated: Long)
data class ChatMessage(val role: String, val content: String, val timestamp: Long)
data class TodayData(
    val place: String,
    val temperature: Int?,
    val wind: Int?,
    val events: List<Pair<String, String>>,
    val tasks: List<String>,
    val notes: List<String>,
)

/** HTTP-клиент к Pi. Все запросы — только по HTTPS (см. network_security_config). */
class Api(private val server: String, private val token: String = "") {

    private val json = "application/json; charset=utf-8".toMediaType()
    private val client = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(300, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    private fun base(): String = server.trimEnd('/')

    private fun request(path: String, method: String = "GET",
                        body: JSONObject? = null): Request.Builder {
        val builder = Request.Builder().url(base() + path).method(
            method,
            if (body == null && method == "GET") null else
                (body ?: JSONObject()).toString().toRequestBody(json),
        )
        if (token.isNotBlank()) builder.header("Authorization", "Bearer $token")
        return builder
    }

    private fun call(path: String, method: String = "GET",
                     body: JSONObject? = null): JSONObject {
        client.newCall(request(path, method, body).build()).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                val detail = runCatching { JSONObject(text).optString("detail") }
                    .getOrDefault("")
                error(if (detail.isNotBlank()) detail else "HTTP ${response.code}")
            }
            return if (text.isBlank()) JSONObject() else JSONObject(text)
        }
    }

    suspend fun signIn(login: String, password: String): JSONObject =
        withContext(Dispatchers.IO) {
            call("/api/v1/auths/signin", "POST",
                JSONObject().put("email", login).put("password", password))
        }

    suspend fun chats(): List<ChatSummary> = withContext(Dispatchers.IO) {
        val array = call("/api/v1/chats/?page=1").optJSONArray("data") ?: JSONArray()
        (0 until array.length()).mapNotNull { index ->
            val item = array.optJSONObject(index) ?: return@mapNotNull null
            ChatSummary(
                id = item.optString("id"),
                title = item.optString("title").ifBlank { "Без названия" },
                updated = item.optLong("updated_at"),
            )
        }.sortedByDescending { it.updated }
    }

    suspend fun newChat(): String = withContext(Dispatchers.IO) {
        call("/api/v1/chats/new", "POST", JSONObject()).optString("id")
    }

    suspend fun deleteChat(id: String) = withContext(Dispatchers.IO) {
        call("/api/v1/chats/$id", "DELETE")
        Unit
    }

    suspend fun messages(chatId: String): List<ChatMessage> = withContext(Dispatchers.IO) {
        val body = call("/api/v1/chats/$chatId")
        val chat = body.optJSONObject("chat") ?: body
        val map = chat.optJSONObject("messages") ?: JSONObject()
        map.keys().asSequence().mapNotNull { key ->
            val item = map.optJSONObject(key) ?: return@mapNotNull null
            val content = item.opt("content")
            val text = when (content) {
                is String -> content
                is JSONArray -> content.optString(0)
                else -> ""
            }
            if (text.isBlank()) null
            else ChatMessage(
                role = item.optString("role", "user"),
                content = text,
                timestamp = item.optLong("timestamp"),
            )
        }.sortedBy { it.timestamp }.toList()
    }

    suspend fun models(): List<Pair<String, String>> = withContext(Dispatchers.IO) {
        val array = call("/api/models").optJSONArray("data") ?: JSONArray()
        (0 until array.length()).mapNotNull { index ->
            val item = array.optJSONObject(index) ?: return@mapNotNull null
            item.optString("id") to item.optString("name", item.optString("id"))
        }
    }

    /**
     * Потоковый ответ модели. Возвращает полный текст, вызывая onDelta
     * по мере поступления кусков (SSE, как в веб-интерфейсе).
     */
    suspend fun stream(
        chatId: String,
        model: String,
        history: List<ChatMessage>,
        prompt: String,
        onDelta: (String) -> Unit,
    ): String = withContext(Dispatchers.IO) {
        val messages = JSONArray()
        history.forEach {
            messages.put(JSONObject().put("role", it.role).put("content", it.content))
        }
        messages.put(JSONObject().put("role", "user").put("content", prompt))
        val payload = JSONObject()
            .put("chat_id", chatId)
            .put("model", model)
            .put("messages", messages)
            .put("stream", true)
        val response = client.newCall(request("/api/chat/completions", "POST", payload).build())
            .execute()
        if (!response.isSuccessful) {
            val text = response.body?.string().orEmpty()
            response.close()
            error("HTTP ${response.code}: ${text.take(200)}")
        }
        val builder = StringBuilder()
        response.body?.source()?.use { source ->
            while (!source.exhausted()) {
                val line = source.readUtf8Line() ?: break
                if (!line.startsWith("data:")) continue
                val chunk = line.removePrefix("data:").trim()
                if (chunk == "[DONE]" || chunk.isEmpty()) continue
                val delta = runCatching {
                    JSONObject(chunk).optJSONArray("choices")
                        ?.optJSONObject(0)
                        ?.optJSONObject("delta")
                        ?.optString("content")
                        .orEmpty()
                }.getOrDefault("")
                if (delta.isNotEmpty()) {
                    builder.append(delta)
                    withContext(Dispatchers.Main) { onDelta(delta) }
                }
            }
        }
        builder.toString()
    }

    suspend fun today(): TodayData = withContext(Dispatchers.IO) {
        val body = call("/api/m/today")
        val weather = body.optJSONObject("weather") ?: JSONObject()
        val events = mutableListOf<Pair<String, String>>()
        body.optJSONArray("events")?.let { array ->
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                events.add(item.optString("time") to item.optString("title"))
            }
        }
        val tasks = mutableListOf<String>()
        body.optJSONArray("tasks")?.let { array ->
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                val due = item.optString("due")
                tasks.add(if (due.isBlank()) item.optString("title")
                else "${item.optString("title")} · $due")
            }
        }
        val notes = mutableListOf<String>()
        body.optJSONArray("notes")?.let { array ->
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                notes.add(item.optString("title"))
            }
        }
        TodayData(
            place = body.optString("place"),
            temperature = if (weather.has("temperature")) weather.optInt("temperature") else null,
            wind = if (weather.has("wind")) weather.optInt("wind") else null,
            events = events,
            tasks = tasks,
            notes = notes,
        )
    }

    suspend fun transcribe(audio: ByteArray, name: String = "voice.m4a"): String =
        withContext(Dispatchers.IO) {
            val part = MultipartBody.Builder().setType(MultipartBody.FORM)
                .addFormDataPart("file", name,
                    audio.toRequestBody("audio/mp4".toMediaType()))
                .addFormDataPart("model", "vosk")
                .build()
            val request = Request.Builder().url(base() + "/api/m/voice")
                .header("Authorization", "Bearer $token")
                .post(part).build()
            client.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (!response.isSuccessful) error("HTTP ${response.code}")
                JSONObject(text).optString("text")
            }
        }

    suspend fun speak(text: String): ByteArray = withContext(Dispatchers.IO) {
        val body = JSONObject().put("text", text).put("voice", "baya")
        client.newCall(request("/api/m/speak", "POST", body).build()).execute().use { response ->
            if (!response.isSuccessful) error("HTTP ${response.code}")
            response.body?.bytes() ?: ByteArray(0)
        }
    }

    suspend fun saveNote(title: String, text: String, folder: String = "AI/Inbox"): String =
        withContext(Dispatchers.IO) {
            val body = JSONObject().put("title", title).put("text", text).put("folder", folder)
            call("/api/m/note", "POST", body).optString("path")
        }

    suspend fun reminder(text: String, dueAt: String, telegram: Boolean): JSONObject =
        withContext(Dispatchers.IO) {
            call("/api/m/reminder", "POST", JSONObject()
                .put("text", text).put("due_at", dueAt).put("telegram", telegram))
        }

    /** Событие в календаре Open WebUI. start — ISO 8601 (локальное время). */
    suspend fun createEvent(title: String, start: String, durationMin: Int = 60,
                            description: String = ""): JSONObject =
        withContext(Dispatchers.IO) {
            call("/api/m/event", "POST", JSONObject()
                .put("title", title).put("start", start)
                .put("duration_min", durationMin).put("description", description))
        }

    suspend fun deleteEvent(id: String) = withContext(Dispatchers.IO) {
        call("/api/m/event/$id/delete", "POST")
        Unit
    }

    /** Задача в Vikunja. due — YYYY-MM-DD или пусто. */
    suspend fun createTask(title: String, due: String = ""): JSONObject =
        withContext(Dispatchers.IO) {
            call("/api/m/task", "POST", JSONObject().put("title", title).put("due", due))
        }

    suspend fun status(): JSONObject = withContext(Dispatchers.IO) { call("/api/m/status") }
}
