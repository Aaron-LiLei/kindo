package org.kindo.tv.net

import android.content.Context
import android.os.Handler
import android.os.Looper
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * Realtime WebSocket 客户端（技术方案 §4）。
 * - hello 携带 last_server_seq，断线自动重连（指数退避，上限 15s）
 * - last_server_seq 持久化（§4.2：冷启动只重放缺口）
 * - 事件回调统一主线程分发（回调链触及 ExoPlayer，Media3 要求主线程）
 */
class RealtimeClient(
    context: Context,
    private val onEvent: (Map<String, Any?>) -> Unit,
) {
    private val scope = CoroutineScope(Dispatchers.IO)
    private val mainHandler = Handler(Looper.getMainLooper())
    private val client = OkHttpClient.Builder()
        .pingInterval(30, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()
    private var ws: WebSocket? = null

    // 断线窗口内的上行消息排队，重连 hello 后补发（transition.select 等
    // 不可丢事件；此前在死连接上 send 静默失败 → TV/Hub 状态分叉）
    private val pendingSends = ArrayDeque<String>()

    private val prefs = context.getSharedPreferences("kindo_realtime", Context.MODE_PRIVATE)
    private var lastSeq: Long = prefs.getLong("last_server_seq", 0L)

    @Volatile private var wantConnected = false
    @Volatile private var baseUrl = ""
    @Volatile private var token = ""
    @Volatile private var reconnectAttempt = 0

    private fun persistSeq(seq: Long) {
        prefs.edit().putLong("last_server_seq", seq).apply()
    }

    fun connect(base: String, token: String) {
        baseUrl = base
        this.token = token
        wantConnected = true
        openSocket()
    }

    private fun openSocket() {
        if (!wantConnected) return
        // 幂等：重复 connect（bound retry 重入等）必须先关旧连接。旧连接若仍留在
        // Hub 广播集合里，同一事件会投递两份（曾致同一条回复 TTS 连播两遍）
        ws?.close(1000, "superseded")
        val url = baseUrl.replaceFirst("http", "ws") + "/api/v1/realtime"
        val request = Request.Builder().url(url)
            .header("Authorization", "Bearer $token")
            .build()
        ws = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                if (ws !== webSocket) {  // 输掉了竞态：更新的连接已存在
                    webSocket.close(1000, "superseded")
                    return
                }
                reconnectAttempt = 0
                webSocket.send(
                    JSONObject().put("type", "hello").put("last_server_seq", lastSeq).toString(),
                )
                // 补发断线窗口内排队的事件
                while (pendingSends.isNotEmpty()) {
                    if (webSocket.send(pendingSends.first())) pendingSends.removeFirst()
                    else break
                }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                if (ws !== webSocket) return  // 旧 socket 残留帧不进处理链
                try {
                    val obj = JSONObject(text)
                    val type = obj.optString("type")
                    // Hub 空闲 30s 发文字 ping、60s 判死；必须回 pong 维持连接
                    // （此前只静默忽略 → 每 ~60s 被“idle timeout”断开一次）
                    if (type == "ping") {
                        webSocket.send(JSONObject().put("type", "pong").toString())
                        return
                    }
                    if (type == "pong" || type == "ack") return
                    val seq = obj.optLong("seq", -1)
                    if (seq > lastSeq) {
                        lastSeq = seq
                        persistSeq(seq)
                    }
                    if (type == "sync.required") {
                        // 超出重放窗口：TV 走 REST 拉取当前状态（§4.2）
                        dispatch(mapOf("type" to "sync.required"))
                        return
                    }
                    val payload = obj.optJSONObject("payload")?.let { p ->
                        p.keys().asSequence().associateWith { key ->
                            if (p.get(key) is org.json.JSONArray || p.get(key) is JSONObject) {
                                p.get(key).toString()
                            } else p.get(key)
                        }
                    } ?: emptyMap()
                    dispatch(buildMap {
                        put("type", type)
                        put("payload", payload)
                    })
                } catch (_: Exception) {
                    // 忽略无法解析的帧
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (ws !== webSocket) return  // 旧 socket 的关闭不得触发重连
                scheduleReconnect()
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (ws !== webSocket) return
                scheduleReconnect()
            }
        })
    }

    private fun dispatch(event: Map<String, Any?>) {
        mainHandler.post { onEvent(event) }
    }

    private fun scheduleReconnect() {
        if (!wantConnected) return
        ws = null
        val backoff = (2000L shl reconnectAttempt.coerceAtMost(3)).coerceAtMost(15_000)
        reconnectAttempt += 1
        scope.launch {
            delay(backoff)
            openSocket()
        }
    }

    val connected: Boolean get() = ws != null

    /** 单发出口：连接断开时入队，重连后补发（上限 32 条防膨胀）。 */
    private fun sendOrQueue(json: String) {
        val socket = ws
        if (socket != null && socket.send(json)) return
        synchronized(pendingSends) {
            if (pendingSends.size >= 32) pendingSends.removeFirst()
            pendingSends.addLast(json)
        }
    }

    // ---------- TV → Hub 上行 ----------

    fun sendTts(ttsId: String, kind: String) {
        sendOrQueue(JSONObject().put("type", "tts.$kind").put("tts_id", ttsId).toString())
    }

    fun sendSelection(sessionId: String, optionId: String) {
        sendOrQueue(
            JSONObject().put("type", "ui.selection")
                .put("session_id", sessionId)
                .put("option_id", optionId).toString())
    }

    fun sendPlaybackEvent(eventId: String, playbackId: String, kind: String, positionMs: Long) {
        sendOrQueue(
            JSONObject().put("type", "playback.$kind")
                .put("event_id", eventId)
                .put("playback_id", playbackId)
                .put("position_ms", positionMs)
                .put("player_state", kind).toString())
    }

    fun sendTransitionSelect(transitionId: String, optionType: String) {
        sendOrQueue(
            JSONObject().put("type", "transition.select")
                .put("event_id", UUID.randomUUID().toString())
                .put("transition_id", transitionId)
                .put("option_type", optionType).toString())
    }

    fun sendTransitionReject(transitionId: String) {
        sendOrQueue(
            JSONObject().put("type", "transition.reject")
                .put("event_id", UUID.randomUUID().toString())
                .put("transition_id", transitionId).toString())
    }

    fun sendTransitionActivityDone(transitionId: String) {
        sendOrQueue(
            JSONObject().put("type", "transition.activity_done")
                .put("event_id", UUID.randomUUID().toString())
                .put("transition_id", transitionId).toString())
    }

    fun sendTrackChanged(
        playbackId: String,
        audioTrackId: String? = null,
        subtitleTrackId: String? = null,
    ) {
        val obj = JSONObject()
            .put("type", "playback.track_changed")
            .put("event_id", UUID.randomUUID().toString())
            .put("playback_id", playbackId)
        audioTrackId?.let { obj.put("audio_track_id", it) }
        subtitleTrackId?.let { obj.put("subtitle_track_id", it) }
        sendOrQueue(obj.toString())
    }

    fun close() {
        wantConnected = false
        ws?.close(1000, "app exit")
        ws = null
    }
}
