package org.kindo.pad.net

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * Voice WebSocket + AudioRecord 采集（技术方案 §5）。
 * PCM16LE 16kHz mono；仅显式会话/追问窗口采集；VAD 在 Hub 侧执行。
 */
class VoiceClient(context: Context) {
    /** 麦克风初始化失败上报（2026-08-26：不再静默失败，UI 呈现提示） */
    var onMicError: ((String) -> Unit)? = null

    /** 语音 WS 建立成功（重置上层重连计数）。 */
    var onOpen: (() -> Unit)? = null

    /** 语音 WS 意外断开（网络抖动/服务重启；主动 close 不触发）。
     *  录音循环已随 ws 置空自行退出，上层据此重连或转错误态——
     *  此前静默置空导致浮层永远停在"正在听"。 */
    var onDropped: (() -> Unit)? = null

    @Volatile private var wantCapture = false

    private val scope = CoroutineScope(Dispatchers.IO)
    private val client = OkHttpClient.Builder()
        .pingInterval(30, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()
    private var ws: WebSocket? = null
    private var recordJob: Job? = null
    private var streamId: String = UUID.randomUUID().toString()

    @SuppressLint("MissingPermission")  // 权限由 UI 层请求后再调用
    fun openStream() {
        // 采集已在进行则复用（TTS 打断后的恢复路径）
        if (recordJob?.isActive == true) return
        start()
    }

    fun captureAndSend(baseUrl: String, token: String, sessionId: String, onFrame: (Int) -> Unit) {
        wantCapture = true
        connect(baseUrl, token, sessionId)
    }

    private fun connect(baseUrl: String, token: String, sessionId: String) {
        val url = baseUrl.replaceFirst("http", "ws") +
            "/api/v1/voice?session_id=$sessionId"
        val request = Request.Builder().url(url)
            .header("Authorization", "Bearer $token")
            .build()
        ws = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, current: Response) {
                webSocket.send(
                    JSONObject()
                        .put("type", "voice.open")
                        .put("stream_id", streamId)
                        .put("format", "pcm_s16le")
                        .put("sample_rate", 16000)
                        .put("channels", 1).toString()
                )
                onOpen?.invoke()
                // 会话开启即采集（技术方案 §5：LISTENING/FOLLOW_UP 期间采集；
                // Hub 无会话起始 listening 事件，仅在 TTS 打断后下发）
                start()
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val obj = JSONObject(text)
                when (obj.optString("type")) {
                    "voice.backpressure" -> pauseSending = true
                    else -> pauseSending = false
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                ws = null
                if (wantCapture) onDropped?.invoke()
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                ws = null
                if (wantCapture) onDropped?.invoke()
            }
        })
    }

    @Volatile private var pauseSending = false

    private fun start() {
        // 设备兼容（2026-08-26 儿童麦克风治理）：
        // 1) getMinBufferSize 负值=设备不支持 16k mono 采集，明确上报不硬试
        // 2) audioSource 回退链：VOICE_RECOGNITION（多数 TV 盒子带厂商语音调优
        //    AGC/NS/远场处理）→ MIC（原始麦克风）
        val minBuf = AudioRecord.getMinBufferSize(
            16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minBuf <= 0) {
            onMicError?.invoke("unsupported_sample_rate")
            return
        }
        var record: AudioRecord? = null
        for (source in intArrayOf(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            MediaRecorder.AudioSource.MIC,
        )) {
            try {
                val r = AudioRecord(
                    source, 16000,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
                    maxOf(minBuf, 3200 * 4),
                )
                if (r.state == AudioRecord.STATE_INITIALIZED) {
                    record = r
                    break
                }
                r.release()
            } catch (e: Exception) {
                // 该 source 不可用，尝试下一个
            }
        }
        if (record == null) {
            onMicError?.invoke("init_failed")
            return
        }
        attachEffects(record.audioSessionId)
        record.startRecording()
        val buf = ShortArray(1600) // 100ms
        recordJob = scope.launch {
            try {
                while (!Thread.currentThread().isInterrupted && ws != null) {
                    val n = record.read(buf, 0, buf.size)
                    if (n > 0 && !pauseSending) {
                        val bytes = ByteArray(n * 2)
                        for (i in 0 until n) {
                            bytes[i * 2] = (buf[i].toInt() and 0xFF).toByte()
                            bytes[i * 2 + 1] = (buf[i].toInt() shr 8 and 0xFF).toByte()
                        }
                        ws?.send(bytes.toByteString())
                    }
                }
            } finally {
                record.stop()
                record.release()
            }
        }
    }

    /** 平台音效（设备支持才 attach；儿童远场拾音增益与降噪）。 */
    private fun attachEffects(sessionId: Int) {
        try {
            if (AcousticEchoCanceler.isAvailable()) {
                AcousticEchoCanceler.create(sessionId)?.enabled = true
            }
            if (NoiseSuppressor.isAvailable()) {
                NoiseSuppressor.create(sessionId)?.enabled = true
            }
            if (AutomaticGainControl.isAvailable()) {
                AutomaticGainControl.create(sessionId)?.enabled = true
            }
        } catch (e: Exception) {
            // 音效不可用不影响裸采集
        }
    }

    fun close() {
        wantCapture = false
        try {
            ws?.send(
                JSONObject().put("type", "voice.close").put("stream_id", streamId)
                    .put("reason", "user_stop").toString()
            )
        } catch (e: Exception) {
            // ignore
        }
        recordJob?.cancel()
        recordJob = null
        ws?.close(1000, "voice close")
        ws = null
    }
}
