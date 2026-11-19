package ru.nedoletoff.omnisecretary.device

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.AlarmClock
import android.provider.CalendarContract
import android.provider.Settings
import java.util.Locale

/**
 * Действия на устройстве — как у голосового ассистента: таймеры, будильники,
 * открытие приложений, звонки, навигация, поиск и т.д. Выполняются через
 * системные интенты (без привилегий, где возможно).
 */
object DeviceActions {

    data class Result(val ok: Boolean, val message: String)

    fun setTimer(context: Context, seconds: Int, label: String = ""): Result {
        if (seconds <= 0) return Result(false, "Укажите длительность таймера")
        val intent = Intent(AlarmClock.ACTION_SET_TIMER).apply {
            putExtra(AlarmClock.EXTRA_LENGTH, seconds)
            if (label.isNotBlank()) putExtra(AlarmClock.EXTRA_MESSAGE, label)
            putExtra(AlarmClock.EXTRA_SKIP_UI, false)
        }
        return launch(context, intent, "Таймер на ${formatDuration(seconds)}")
    }

    fun setAlarm(context: Context, hour: Int, minute: Int, label: String = ""): Result {
        if (hour !in 0..23 || minute !in 0..59) return Result(false, "Некорректное время будильника")
        val intent = Intent(AlarmClock.ACTION_SET_ALARM).apply {
            putExtra(AlarmClock.EXTRA_HOUR, hour)
            putExtra(AlarmClock.EXTRA_MINUTES, minute)
            if (label.isNotBlank()) putExtra(AlarmClock.EXTRA_MESSAGE, label)
            putExtra(AlarmClock.EXTRA_SKIP_UI, false)
        }
        return launch(context, intent, "Будильник на %02d:%02d".format(hour, minute))
    }

    fun showAlarms(context: Context): Result =
        launch(context, Intent(AlarmClock.ACTION_SHOW_ALARMS), "Будильники")

    fun showTimers(context: Context): Result =
        launch(context, Intent(AlarmClock.ACTION_SHOW_TIMERS), "Таймеры")

    fun openUrl(context: Context, url: String): Result {
        val safe = if (url.startsWith("http://") || url.startsWith("https://")) url else "https://$url"
        return launch(context, Intent(Intent.ACTION_VIEW, Uri.parse(safe)), "Открываю ссылку")
    }

    fun searchWeb(context: Context, query: String): Result {
        val intent = Intent(Intent.ACTION_WEB_SEARCH).apply { putExtra("query", query) }
        return try {
            launch(context, intent, "Поиск: $query")
        } catch (_: Exception) {
            openUrl(context, "https://www.google.com/search?q=${Uri.encode(query)}")
        }
    }

    fun dial(context: Context, number: String): Result {
        val clean = number.filter { it.isDigit() || it == '+' }
        if (clean.isBlank()) return Result(false, "Не указан номер")
        return launch(context, Intent(Intent.ACTION_DIAL, Uri.parse("tel:$clean")), "Набор $clean")
    }

    fun sendSms(context: Context, number: String, text: String = ""): Result {
        val clean = number.filter { it.isDigit() || it == '+' }
        val intent = Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:$clean")).apply {
            if (text.isNotBlank()) putExtra("sms_body", text)
        }
        return launch(context, intent, "Сообщение")
    }

    fun navigate(context: Context, destination: String): Result {
        val uri = Uri.parse("geo:0,0?q=${Uri.encode(destination)}")
        return launch(context, Intent(Intent.ACTION_VIEW, uri), "Маршрут: $destination")
    }

    fun openAppSettings(context: Context): Result =
        launch(context, Intent(Settings.ACTION_SETTINGS), "Настройки")

    fun openCalendarInsert(context: Context, title: String = ""): Result {
        val intent = Intent(Intent.ACTION_INSERT).setData(CalendarContract.Events.CONTENT_URI).apply {
            if (title.isNotBlank()) putExtra(CalendarContract.Events.TITLE, title)
        }
        return launch(context, intent, "Новое событие")
    }

    fun openClock(context: Context): Result {
        val intent = context.packageManager.getLaunchIntentForPackage("com.google.android.deskclock")
            ?: context.packageManager.getLaunchIntentForPackage("com.android.deskclock")
            ?: Intent(AlarmClock.ACTION_SHOW_ALARMS)
        return launch(context, intent, "Часы")
    }

    fun openApp(context: Context, query: String): Result {
        val q = query.trim().lowercase(Locale.getDefault())
        if (q.isBlank()) return Result(false, "Укажите приложение")
        knownApps[q]?.let { pkg ->
            context.packageManager.getLaunchIntentForPackage(pkg)?.let {
                return launch(context, it, "Открываю $query")
            }
        }
        val pm = context.packageManager
        val main = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val apps = pm.queryIntentActivities(main, 0)
        val match = apps.firstOrNull { info ->
            val label = info.loadLabel(pm).toString().lowercase(Locale.getDefault())
            label == q || label.contains(q) || q.contains(label)
        } ?: apps.firstOrNull { info ->
            info.activityInfo.packageName.lowercase(Locale.getDefault()).contains(q)
        }
        return if (match != null) {
            val intent = Intent(Intent.ACTION_MAIN)
                .addCategory(Intent.CATEGORY_LAUNCHER)
                .setClassName(match.activityInfo.packageName, match.activityInfo.name)
            launch(context, intent, "Открываю ${match.loadLabel(pm)}")
        } else {
            Result(false, "Приложение «$query» не найдено")
        }
    }

    fun listLaunchableApps(context: Context): List<Pair<String, String>> {
        val pm = context.packageManager
        val main = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        return pm.queryIntentActivities(main, 0)
            .map { it.loadLabel(pm).toString() to it.activityInfo.packageName }
            .distinctBy { it.second }
            .sortedBy { it.first.lowercase(Locale.getDefault()) }
    }

    /** Попытка распознать и выполнить команду устройству из текста пользователя. */
    fun tryExecute(context: Context, text: String): Result? {
        val t = text.trim()
        val lower = t.lowercase(Locale.getDefault())

        // Таймер: "таймер на 5 минут", "поставь таймер 10 мин", "timer 5 min"
        parseTimer(lower)?.let { (sec, label) -> return setTimer(context, sec, label) }

        // Будильник: "будильник на 7:30", "разбуди в 07 00", "alarm 7:30"
        parseAlarm(lower)?.let { (h, m) -> return setAlarm(context, h, m) }

        // Открыть приложение: "открой youtube", "запусти карты", "open telegram"
        parseOpenApp(t, lower)?.let { return openApp(context, it) }

        // Поиск: "найди в интернете ...", "загугли ..."
        parseSearch(t, lower)?.let { return searchWeb(context, it) }

        // Навигация: "маршрут до ...", "как доехать до ..."
        parseNavigate(t, lower)?.let { return navigate(context, it) }

        // Позвонить: "позвони +7...", "набери ..."
        parseDial(lower)?.let { return dial(context, it) }

        // Ссылка
        if (lower.startsWith("открой http") || lower.startsWith("open http")) {
            val url = t.substringAfter(' ', "").trim()
            if (url.startsWith("http")) return openUrl(context, url)
        }

        return null
    }

    private fun parseTimer(lower: String): Pair<Int, String>? {
        val triggers = listOf("таймер", "timer")
        if (triggers.none { lower.contains(it) }) return null
        // ищем числа и единицы
        var total = 0
        val h = Regex("(\\d+)\\s*(час|ч\\b|hour|hr)").find(lower)?.groupValues?.get(1)?.toIntOrNull() ?: 0
        val m = Regex("(\\d+)\\s*(минут|мин|minute|min|м\\b)").find(lower)?.groupValues?.get(1)?.toIntOrNull() ?: 0
        val s = Regex("(\\d+)\\s*(секунд|сек|second|sec|с\\b)").find(lower)?.groupValues?.get(1)?.toIntOrNull() ?: 0
        total = h * 3600 + m * 60 + s
        if (total == 0) {
            // просто "таймер на 5" -> минуты
            val n = Regex("таймер\\s*(?:на\\s*)?(\\d+)").find(lower)?.groupValues?.get(1)?.toIntOrNull()
                ?: Regex("timer\\s*(?:for\\s*)?(\\d+)").find(lower)?.groupValues?.get(1)?.toIntOrNull()
            if (n != null) total = n * 60
        }
        if (total <= 0) return null
        return total to ""
    }

    private fun parseAlarm(lower: String): Pair<Int, Int>? {
        val triggers = listOf("будильник", "разбуди", "alarm", "wake")
        if (triggers.none { lower.contains(it) }) return null
        val m = Regex("(\\d{1,2})[:\\s.](\\d{2})").find(lower) ?: return null
        val h = m.groupValues[1].toIntOrNull() ?: return null
        val min = m.groupValues[2].toIntOrNull() ?: return null
        if (h !in 0..23 || min !in 0..59) return null
        return h to min
    }

    private fun parseOpenApp(original: String, lower: String): String? {
        val prefixes = listOf("открой ", "запусти ", "открыть ", "запустить ", "open ", "launch ", "start ")
        for (p in prefixes) {
            if (lower.startsWith(p)) return original.substring(p.length).trim().ifBlank { null }
            val idx = lower.indexOf(" $p")
            if (idx >= 0) return original.substring(idx + 1 + p.length).trim().ifBlank { null }
        }
        return null
    }

    private fun parseSearch(original: String, lower: String): String? {
        val prefixes = listOf("найди в интернете ", "загугли ", "поищи в интернете ", "поищи ", "search ", "google ")
        for (p in prefixes) {
            if (lower.startsWith(p)) return original.substring(p.length).trim().ifBlank { null }
        }
        return null
    }

    private fun parseNavigate(original: String, lower: String): String? {
        val prefixes = listOf("маршрут до ", "как доехать до ", "навигация до ", "navigate to ", "directions to ")
        for (p in prefixes) {
            if (lower.startsWith(p)) return original.substring(p.length).trim().ifBlank { null }
        }
        return null
    }

    private fun parseDial(lower: String): String? {
        val prefixes = listOf("позвони ", "набери ", "call ", "dial ")
        for (p in prefixes) {
            if (lower.startsWith(p)) {
                val num = lower.substring(p.length).filter { it.isDigit() || it == '+' }
                if (num.length >= 3) return num
            }
        }
        return null
    }

    private fun launch(context: Context, intent: Intent, successMsg: String): Result {
        return try {
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
            Result(true, successMsg)
        } catch (e: Exception) {
            Result(false, e.message ?: "Не удалось выполнить действие")
        }
    }

    private fun formatDuration(seconds: Int): String {
        val h = seconds / 3600
        val m = (seconds % 3600) / 60
        val s = seconds % 60
        return when {
            h > 0 && m > 0 -> "${h} ч ${m} мин"
            h > 0 -> "${h} ч"
            m > 0 && s > 0 -> "${m} мин ${s} с"
            m > 0 -> "${m} мин"
            else -> "${s} с"
        }
    }
}
