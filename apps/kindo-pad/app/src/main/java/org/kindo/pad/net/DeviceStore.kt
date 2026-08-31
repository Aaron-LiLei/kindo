package org.kindo.pad.net

import android.content.Context
import java.io.File

/** 绑定信息（hub 地址 + device_token + 设备身份）。 */
data class DeviceBinding(
    val baseUrl: String? = null,
    val token: String? = null,
    val deviceId: String? = null,
)

/**
 * 绑定信息持久化：hub 地址 + device_token + device_id（技术方案 §12.2）。
 * V0.1 存应用私有目录；设备 root 场景不在威胁模型内（儿童 + 局域网）。
 *
 * device_id 随绑定落盘（2026-08-26 修复：此前每进程随机重建，同一台电视
 * 每次重装/重配对在 Hub 设备列表变成新行，家长无法按设备管理）。
 */
class DeviceStore(context: Context) {
    private val file = File(context.filesDir, "binding.json")

    fun save(baseUrl: String, token: String, deviceId: String) {
        file.writeText(
            """{"base_url":"$baseUrl","token":"$token","device_id":"$deviceId"}""")
    }

    /** 旧版绑定文件（无 device_id）补写设备身份，不动地址与 token。 */
    fun updateDeviceId(deviceId: String) {
        val cur = load()
        if (cur.baseUrl != null && cur.token != null) {
            save(cur.baseUrl, cur.token, deviceId)
        }
    }

    fun load(): DeviceBinding = try {
        val raw = file.readText()
        val obj = org.kindo.pad.net.parseJson(raw)
        DeviceBinding(
            baseUrl = obj["base_url"] as? String,
            token = obj["token"] as? String,
            deviceId = obj["device_id"] as? String,
        )
    } catch (e: Exception) {
        DeviceBinding()
    }

    fun clear() {
        file.delete()
    }
}

internal fun parseJson(raw: String): Map<String, Any?> =
    kotlinx.serialization.json.Json.parseToJsonElement(raw).let { el ->
        (el as? kotlinx.serialization.json.JsonObject)?.entries?.associate { (k, v) ->
            k to (v as? kotlinx.serialization.json.JsonPrimitive)?.content
        } ?: emptyMap()
    }
