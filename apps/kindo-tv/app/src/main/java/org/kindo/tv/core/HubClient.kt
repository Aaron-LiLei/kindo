package org.kindo.tv.core

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.Call
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** 统一播放请求结果（§3.1 POST /playbacks）。 */
sealed class PlayResult {
    data class Allow(
        val playbackId: String,
        val descriptor: StreamDescriptorDto,
    ) : PlayResult()

    data class Deny(
        val reasonCode: String,
        val constraints: PlayConstraints? = null,
    ) : PlayResult()

    data class Failure(val message: String, val code: String? = null) : PlayResult()
}

/** 错误 envelope（§2.3）：400 的 details.reason_code 供分型文案（如 media_not_playable）。 */
@Serializable
data class ErrorEnvelope(val error: ErrorBody? = null)

@Serializable
data class ErrorBody(
    val code: String? = null,
    val message: String? = null,
    val details: ErrorDetails? = null,
)

@Serializable
data class ErrorDetails(val reason_code: String? = null)

/** Hub REST 客户端（技术方案 §3.1/§3.2）。所有调用挂起函数，错误不抛出以外流。 */
class HubClient {
    var baseUrl: String = ""
        private set
    var deviceToken: String = ""
        private set
    val configured: Boolean get() = baseUrl.isNotEmpty() && deviceToken.isNotEmpty()

    private val json = Json { ignoreUnknownKeys = true }
    private val http = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .build()

    fun configure(base: String, token: String) {
        baseUrl = base.trimEnd('/')
        deviceToken = token
    }

    fun posterUrl(mediaId: String): String = "$baseUrl/api/v1/media/$mediaId/poster"

    /**
     * hub_tts 合成音频同步拉取（技术方案 §6.7）。在播放工作线程内调用；
     * 非 200 或网络异常返回 null（调用方回退系统 TTS）。
     */
    fun ttsAudioBlocking(path: String): ByteArray? = try {
        http.newCall(authBuilder(path).build()).execute().use { resp ->
            if (resp.code == 200) resp.body?.bytes() else null
        }
    } catch (e: Exception) {
        null
    }

    private fun authBuilder(path: String): Request.Builder =
        Request.Builder().url("$baseUrl$path").header("Authorization", "Bearer $deviceToken")

    private suspend fun execute(request: Request): Pair<Int, String> = withContext(Dispatchers.IO) {
        suspendCancellableCoroutine { cont ->
            val call: Call = http.newCall(request)
            cont.invokeOnCancellation { call.cancel() }
            try {
                call.execute().use { resp ->
                    cont.resume(resp.code to (resp.body?.string() ?: ""))
                }
            } catch (e: Exception) {
                cont.resumeWithException(e)
            }
        }
    }

    // ---------- Pairing（§3.2，未绑定阶段无 token） ----------

    suspend fun pairingInfo(base: String): BootstrapInfo? = try {
        val (code, body) = execute(
            Request.Builder().url("$base/api/v1/pairing/info").build())
        if (code == 200) json.decodeFromString<BootstrapInfo>(body) else null
    } catch (e: Exception) {
        null
    }

    suspend fun createPairingRequest(
        base: String,
        deviceName: String,
        appInstanceId: String,
    ): PairingRequestResult? = try {
        val bodyJson = """{"device_name":"$deviceName","app_instance_id":"$appInstanceId","capabilities":{"mic":true,"tts":"android"}}"""
        val (code, body) = execute(
            Request.Builder().url("$base/api/v1/pairing/requests")
                .post(bodyJson.toRequestBody("application/json".toMediaType())).build())
        if (code == 200) json.decodeFromString<PairingRequestResult>(body) else null
    } catch (e: Exception) {
        null
    }

    suspend fun pollPairingStatus(
        base: String,
        pairingId: String,
        secret: String,
    ): PairingStatus? = try {
        val (code, body) = execute(
            Request.Builder()
                .url("$base/api/v1/pairing/requests/$pairingId?pairing_secret=$secret")
                .build())
        if (code == 200) json.decodeFromString<PairingStatus>(body) else null
    } catch (e: Exception) {
        null
    }

    // ---------- 已绑定（Device Token） ----------

    suspend fun bootstrap(): BootstrapInfo? = try {
        val (code, body) = execute(authBuilder("/api/v1/bootstrap").build())
        if (code == 200) json.decodeFromString<BootstrapInfo>(body) else null
    } catch (e: Exception) {
        null
    }

    suspend fun home(): HomeData? = try {
        val (code, body) = execute(authBuilder("/api/v1/home").build())
        if (code == 200) json.decodeFromString<HomeData>(body) else null
    } catch (e: Exception) {
        null
    }

    suspend fun collections(): CollectionsResp? = try {
        val (code, body) = execute(authBuilder("/api/v1/collections").build())
        if (code == 200) json.decodeFromString<CollectionsResp>(body) else null
    } catch (e: Exception) {
        null
    }

    suspend fun mediaPage(
        type: String? = null,
        language: String? = null,
        tag: String? = null,
        query: String? = null,
        seriesId: String? = null,
        cursor: String? = null,
        limit: Int = 30,
    ): MediaPage? = try {
        val params = buildList {
            type?.let { add("type=$it") }
            language?.let { add("language=$it") }
            tag?.let { add("tag=${java.net.URLEncoder.encode(it, "UTF-8")}") }
            query?.let { add("query=${java.net.URLEncoder.encode(it, "UTF-8")}") }
            seriesId?.let { add("series_id=$it") }
            cursor?.let { add("cursor=${java.net.URLEncoder.encode(it, "UTF-8")}") }
            add("limit=$limit")
        }.joinToString("&")
        val (code, body) = execute(authBuilder("/api/v1/media?$params").build())
        if (code == 200) json.decodeFromString<MediaPage>(body) else null
    } catch (e: Exception) {
        null
    }

    suspend fun mediaDetail(mediaId: String): MediaDetail? = try {
        val (code, body) = execute(authBuilder("/api/v1/media/$mediaId").build())
        if (code == 200) json.decodeFromString<MediaDetail>(body) else null
    } catch (e: Exception) {
        null
    }

    /** D-pad / AI 共用统一播放入口；D-pad 路径 source=ui 同样过 Policy（A-06）。 */
    suspend fun requestPlayback(
        mediaId: String,
        action: String = "play",
        startPositionMs: Long? = null,
        source: String = "ui",
    ): PlayResult = try {
        val start = startPositionMs ?: 0
        val bodyJson = """{"media_id":"$mediaId","action":"$action","start_position_ms":$start,"source":"$source"}"""
        val (code, body) = execute(
            authBuilder("/api/v1/playbacks")
                .header("Idempotency-Key", UUID.randomUUID().toString())
                .post(bodyJson.toRequestBody("application/json".toMediaType())).build())
        when {
            code == 200 -> {
                val resp = json.decodeFromString<PlaybackResponse>(body)
                PlayResult.Allow(resp.playback_id, resp.stream_descriptor)
            }
            code == 403 -> {
                val deny = runCatching { json.decodeFromString<DenyInfo>(body) }.getOrNull()
                PlayResult.Deny(
                    deny?.reason_code ?: "policy_denied",
                    deny?.constraints,
                )
            }
            else -> {
                val reason = runCatching {
                    json.decodeFromString<ErrorEnvelope>(body).error?.details?.reason_code
                }.getOrNull()
                PlayResult.Failure("HTTP $code", reason)
            }
        }
    } catch (e: Exception) {
        PlayResult.Failure(e.message ?: "network")
    }

    suspend fun controlPlayback(playbackId: String, action: String, positionMs: Long? = null): Boolean = try {
        val pos = positionMs ?: 0
        val bodyJson = if (positionMs != null) {
            """{"action":"$action","position_ms":$pos}"""
        } else {
            """{"action":"$action"}"""
        }
        val (code, _) = execute(
            authBuilder("/api/v1/playbacks/$playbackId/control")
                .post(bodyJson.toRequestBody("application/json".toMediaType())).build())
        code == 200
    } catch (e: Exception) {
        false
    }

    suspend fun createConversation(
        resumeSessionId: String? = null,
        uiContextJson: String = """{"screen":"home"}""",
    ): ConversationCreated? = try {
        val resume = resumeSessionId ?: "null"
        val (code, body) = execute(
            authBuilder("/api/v1/conversations")
                .post("""{"resume_session_id":$resume,"ui_context":$uiContextJson}"""
                    .toRequestBody("application/json".toMediaType())).build())
        if (code == 200) json.decodeFromString<ConversationCreated>(body) else null
    } catch (e: Exception) {
        null
    }

    /** 当前活跃播放（REST 对齐用；无 stream 数据）。 */
    suspend fun currentPlayback(): CurrentPlayback? = try {
        val (code, body) = execute(authBuilder("/api/v1/playbacks/current").build())
        if (code == 200) json.decodeFromString<CurrentPlaybackResp>(body).playback else null
    } catch (e: Exception) {
        null
    }

    /** 对自己设备名下的活跃 playback 重发新 Grant（接力 audio handoff 的 REST 兜底）。 */
    suspend fun regrantPlayback(playbackId: String): PlaybackResponse? = try {
        val (code, body) = execute(
            authBuilder("/api/v1/playbacks/$playbackId/regrant")
                .post("{}".toRequestBody("application/json".toMediaType())).build())
        if (code == 200) json.decodeFromString<PlaybackResponse>(body) else null
    } catch (e: Exception) {
        null
    }

    suspend fun endConversation(sessionId: String) {
        try {
            execute(
                authBuilder("/api/v1/conversations/$sessionId/end")
                    .post("{}".toRequestBody("application/json".toMediaType())).build())
        } catch (_: Exception) {
        }
    }
}
