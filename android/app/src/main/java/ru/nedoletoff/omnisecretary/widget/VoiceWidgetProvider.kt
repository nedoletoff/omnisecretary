package ru.nedoletoff.omnisecretary.widget

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews
import ru.nedoletoff.omnisecretary.MainActivity
import ru.nedoletoff.omnisecretary.R

/**
 * Виджет 1×1: одно нажатие — голосовой ввод.
 * Ничего не держит в памяти и не опрашивает сервер, поэтому батарею не ест.
 */
class VoiceWidgetProvider : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        ids.forEach { id ->
            val views = RemoteViews(context.packageName, R.layout.widget_voice)
            val intent = Intent(context, MainActivity::class.java)
                .setAction(MainActivity.ACTION_VOICE)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            val pending = PendingIntent.getActivity(
                context, 0, intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            views.setOnClickPendingIntent(R.id.widget_mic, pending)
            manager.updateAppWidget(id, views)
        }
    }
}
