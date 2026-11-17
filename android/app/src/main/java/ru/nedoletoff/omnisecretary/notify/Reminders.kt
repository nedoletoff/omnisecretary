package ru.nedoletoff.omnisecretary.notify

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import ru.nedoletoff.omnisecretary.MainActivity
import ru.nedoletoff.omnisecretary.R

/**
 * Локальные напоминания приложения — отдельный канал от Telegram.
 * Пользователь может отключить один, не трогая другой.
 */
object Reminders {
    const val CHANNEL = "omni_reminders"

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL) != null) return
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, "Напоминания OmniSecretary",
                NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "Напоминания, созданные в приложении"
            })
    }

    fun schedule(context: Context, text: String, atMillis: Long) {
        ensureChannel(context)
        val intent = Intent(context, ReminderReceiver::class.java)
            .putExtra("text", text)
        val pending = PendingIntent.getBroadcast(
            context, text.hashCode(), intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val alarm = context.getSystemService(AlarmManager::class.java)
        alarm.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMillis, pending)
    }

    fun show(context: Context, text: String) {
        ensureChannel(context)
        val open = PendingIntent.getActivity(
            context, 0,
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val notification = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_mic)
            .setContentTitle("Напоминание")
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setAutoCancel(true)
            .setContentIntent(open)
            .build()
        runCatching {
            NotificationManagerCompat.from(context).notify(text.hashCode(), notification)
        }
    }
}

class ReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val text = intent.getStringExtra("text").orEmpty()
        if (text.isNotBlank()) Reminders.show(context, text)
    }
}
