package ru.nedoletoff.omnisecretary.tile

import android.content.Intent
import android.service.quicksettings.TileService
import ru.nedoletoff.omnisecretary.MainActivity

/** Плитка в шторке: голосовой ввод из любого экрана. */
class VoiceTileService : TileService() {
    override fun onClick() {
        val intent = Intent(this, MainActivity::class.java)
            .setAction(MainActivity.ACTION_VOICE)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        startActivityAndCollapse(intent)
    }
}
