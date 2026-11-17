package ru.nedoletoff.omnisecretary.voice

import android.content.Context
import android.media.MediaRecorder
import android.os.Build
import java.io.File

/**
 * Запись голоса для распознавания.
 *
 * Пишем AAC в m4a: файл в 5–10 раз меньше WAV, а STT всё равно декодирует
 * его через ffmpeg. Частота 16 кГц — родная для модели Vosk.
 */
class Recorder(private val context: Context) {
    private var recorder: MediaRecorder? = null
    private var file: File? = null

    val isRecording: Boolean get() = recorder != null

    fun start(): File {
        stop()
        val target = File.createTempFile("voice-", ".m4a", context.cacheDir)
        val media = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            MediaRecorder(context)
        } else {
            @Suppress("DEPRECATION")
            MediaRecorder()
        }
        media.setAudioSource(MediaRecorder.AudioSource.MIC)
        media.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
        media.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
        media.setAudioSamplingRate(16000)
        media.setAudioChannels(1)
        media.setAudioEncodingBitRate(32000)
        media.setOutputFile(target.absolutePath)
        media.prepare()
        media.start()
        recorder = media
        file = target
        return target
    }

    /** Останавливает запись и возвращает байты аудио. */
    fun stop(): ByteArray? {
        val media = recorder ?: return null
        val target = file
        recorder = null
        file = null
        return try {
            media.stop()
            media.release()
            target?.readBytes()
        } catch (error: RuntimeException) {
            runCatching { media.release() }
            null
        } finally {
            target?.delete()
        }
    }
}
