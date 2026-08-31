package org.kindo.pad.tts

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Handler
import android.os.Looper
import android.util.Log
import org.kindo.pad.core.HubClient
import java.io.ByteArrayInputStream
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * hub_tts 播放器（技术方案 §6.7）：拉取 Hub 按句合成的 WAV（24kHz 单声道 PCM16）
 * 用 AudioTrack 顺序播放（内部单队列，等价系统 TTS 的 QUEUE_ADD 语义）。
 * 取音频失败/解码失败回退系统 TTS 读同句文本；事件回报（started/finished/interrupted）
 * 与系统 TTS 完全一致——追问窗口驱动不区分来源。
 */
class HubTtsPlayer(
    private val hub: HubClient,
    private val fallback: (ttsId: String, text: String, onEvent: (String) -> Unit) -> Unit,
) {
    private class Item(
        val ttsId: String,
        val audioPath: String,
        val text: String,
        val onEvent: (String) -> Unit,
    )

    private val main = Handler(Looper.getMainLooper())
    private val queue = LinkedBlockingQueue<Item>()

    @Volatile
    private var released = false

    @Volatile
    private var current: Item? = null

    @Volatile
    private var currentTrack: AudioTrack? = null

    private val worker = Thread {
        while (!released) {
            val item = try {
                queue.poll(500, TimeUnit.MILLISECONDS)
            } catch (e: InterruptedException) {
                break
            } ?: continue
            play(item)
        }
    }.apply {
        name = "kindo-hub-tts"
        start()
    }

    /** 入队一句（多句并发到达时按到达顺序排队播报，等价 QUEUE_ADD）。 */
    fun speak(ttsId: String, audioPath: String, text: String, onEvent: (String) -> Unit) {
        if (!released) queue.put(Item(ttsId, audioPath, text, onEvent))
    }

    /** 打断：清空队列并停当前句（仅当前句回报 interrupted；TV 层整体清空 pendingTtsIds）。 */
    fun stop() {
        queue.clear()
        val item = current
        if (item != null) {
            current = null
            currentTrack?.let { t ->
                try {
                    t.pause()
                    t.flush()
                } catch (_: IllegalStateException) {
                }
            }
            main.post { item.onEvent("interrupted") }
        }
    }

    fun release() {
        released = true
        stop()
        worker.interrupt()
    }

    private fun play(item: Item) {
        current = item
        val parsed = try {
            hub.ttsAudioBlocking(item.audioPath)?.let { WavParser.parse(it) }
        } catch (e: Exception) {
            Log.w(TAG, "hub_tts 拉取失败，回退系统 TTS: ${e.message}")
            null
        }
        if (current !== item) return // 已被打断/释放
        if (parsed == null) {
            current = null
            // 回退系统 TTS（同样的事件回调；KindoTts 内部不可用也会按 finished 降级）
            main.post { fallback(item.ttsId, item.text, item.onEvent) }
            return
        }
        val (pcm, sampleRate) = parsed
        val minBuf = AudioTrack.getMinBufferSize(
            sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT,
        )
        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(sampleRate)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build(),
            )
            .setBufferSizeInBytes(maxOf(minBuf, 16_384))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        currentTrack = track
        try {
            track.play()
        } catch (e: IllegalStateException) {
            Log.w(TAG, "hub_tts 播放器启动失败，回退系统 TTS: ${e.message}")
            currentTrack = null
            current = null
            main.post { fallback(item.ttsId, item.text, item.onEvent) }
            return
        }
        main.post { item.onEvent("started") }
        // 分块写入：打断时逐块检查停止标记，立即收尾
        val chunk = ByteArray(8_192)
        var offset = 0
        var done = false
        while (!released && current === item && offset < pcm.size) {
            val n = minOf(chunk.size, pcm.size - offset)
            System.arraycopy(pcm, offset, chunk, 0, n)
            val written = track.write(chunk, 0, n)
            if (written < 0) break
            offset += written
            done = offset >= pcm.size
        }
        try {
            track.stop()
        } catch (_: IllegalStateException) {
        }
        track.release()
        currentTrack = null
        val finished = done && current === item && !released
        current = null
        main.post { item.onEvent(if (finished) "finished" else "interrupted") }
    }

    private companion object {
        const val TAG = "KindoHubTts"
    }
}

/** 最小 WAV 解析（PCM16 单声道）：只认 fmt/data 块，其余格式交给系统 TTS 回退。 */
internal object WavParser {
    fun parse(bytes: ByteArray): Pair<ByteArray, Int>? {
        if (bytes.size < 44) return null
        val input = ByteArrayInputStream(bytes)
        val header = ByteArray(12)
        if (input.read(header) != 12) return null
        if (!header.copyOfRange(0, 4).contentEquals("RIFF".toByteArray())) return null
        if (!header.copyOfRange(8, 12).contentEquals("WAVE".toByteArray())) return null
        var sampleRate = 0
        var bitsPerSample = 0
        var channels = 0
        var data: ByteArray? = null
        while (data == null) {
            val chunkHeader = ByteArray(8)
            if (input.read(chunkHeader) != 8) break
            val id = String(chunkHeader, 0, 4, Charsets.US_ASCII)
            val size = readLeInt(chunkHeader, 4)
            if (id == "fmt ") {
                val fmt = ByteArray(size)
                if (input.read(fmt) != size) return null
                channels = readLeShort(fmt, 2)
                sampleRate = readLeInt(fmt, 4)
                bitsPerSample = readLeShort(fmt, 14)
            } else if (id == "data") {
                val pcm = ByteArray(size)
                val read = input.read(pcm)
                if (read <= 0) return null
                data = pcm.copyOf(read)
            } else {
                if (input.skip(size.toLong()) != size.toLong()) return null
            }
        }
        val pcm = data ?: return null
        if (channels != 1 || bitsPerSample != 16 || sampleRate !in 8_000..48_000) return null
        return pcm to sampleRate
    }

    private fun readLeShort(b: ByteArray, off: Int): Int = (b[off].toInt() and 0xFF) or
        ((b[off + 1].toInt() and 0xFF) shl 8)

    private fun readLeInt(b: ByteArray, off: Int): Int = readLeShort(b, off) or
        (readLeShort(b, off + 2) shl 16)
}
