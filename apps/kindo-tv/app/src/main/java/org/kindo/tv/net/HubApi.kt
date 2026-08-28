package org.kindo.tv.net

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.kindo.tv.playback.StreamDescriptor
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume

/** TV ↔ Hub REST 客户端（技术方案 §3.1）。Device Token 只存在本机私有存储。 */
class HubApi(context: Context) {
    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()
    private val store = DeviceStore(context)
    private var baseUrl: String = ""
    private var token: String = ""

    val json = Json { ignoreUnknownKeys = true }

    fun configure(baseUrl: String, token: String) {
        this.baseUrl = baseUrl.trimEnd('/')
        this.token = token
    }

    fun baseUrl() = baseUrl
    fun deviceToken() = token.takeIf { it.isNotEmpty() }

    suspend fun pingBootstrap(): Result<Boolean> = withContext(Dispatchers.IO) {
        runCatching {
            val resp = client.newCall(
                Request.Builder().url("$baseUrl/api/v1/bootstrap").header("Authorization", bearer()).build()
            ).execute()
            resp.use { it.isSuccessful }
        }
    }

    suspend fun getHome(): HomePayload? = get("/api/v1/home")?.let { parseHome(it) }

    suspend fun createConversation(): String? {
        val body = "{}".toRequestBody("application/json".toMediaType())
        val resp = post("/api/v1/conversations", body) ?: return null
        return resp
    }

    suspend fun endConversation(sessionId: String) {
        post("/api/v1/conversations/$sessionId/end", "{}".toRequestBody("application/json".toMediaType()))
    }

    suspend fun getPlayback(playbackId: String): StreamDescriptor? {
        val raw = get("/api/v1/playbacks/current") ?: return null
        return try {
            val obj = json.parseToJsonElement(raw).jsonObject
            val pb = obj["playback"]?.jsonObject ?: return null
            if (pb["playback_id"]?.jsonPrimitive?.content != playbackId) return null
            // current 接口不含 grant：通过 play 路径获取；此处仅在 command.start 后由 TV 重放
            null
        } catch (e: Exception) {
            null
        }
    }

    private fun bearer() = "Bearer $token"

    private suspend fun get(path: String): String? = withContext(Dispatchers.IO) {
        runCatching {
            client.newCall(
                Request.Builder().url("$baseUrl$path").header("Authorization", bearer()).build()
            ).execute().use { resp ->
                if (resp.isSuccessful) resp.body?.string() else null
            }
        }.getOrNull()
    }

    private suspend fun post(path: String, body: okhttp3.RequestBody): String? =
        withContext(Dispatchers.IO) {
            runCatching {
                client.newCall(
                    Request.Builder().url("$baseUrl$path").post(body)
                        .header("Authorization", bearer()).build()
                ).execute().use { resp ->
                    if (resp.isSuccessful) resp.body?.string() else null
                }
            }.getOrNull()
        }

    private fun parseHome(raw: String): HomePayload? = try {
        val obj = json.parseToJsonElement(raw).jsonObject
        HomePayload(
            continueWatching = (obj["continue_watching"]?.jsonArray ?: kotlinx.serialization.json.JsonArray(emptyList()))
                .map { el ->
                    val o = el.jsonObject
                    ContinueItem(
                        o["media_id"]?.jsonPrimitive?.content ?: "",
                        o["title"]?.jsonPrimitive?.content ?: "",
                        o["last_position_ms"]?.jsonPrimitive?.content?.toLongOrNull() ?: 0L,
                    )
                }
        )
    } catch (e: Exception) {
        null
    }
}

data class HomePayload(val continueWatching: List<ContinueItem>)
data class ContinueItem(val mediaId: String, val title: String, val lastPositionMs: Long)
