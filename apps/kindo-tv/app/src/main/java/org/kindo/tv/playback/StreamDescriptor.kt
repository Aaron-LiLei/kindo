package org.kindo.tv.playback

/** Hub 下发的 stream descriptor（技术方案 §9.3）。Grant 绝不进入 URL/日志。 */
data class StreamDescriptor(
    val playbackId: String,
    val mediaId: String,
    val url: String,
    val mimeType: String,
    val grant: String,
    val durationMs: Long,
    val startPositionMs: Long = 0,
) {
    companion object {
        /** Realtime 事件中嵌套对象以 JSON 字符串透传，在此解析（字段名按技术方案 §9.3）。 */
        fun fromJson(raw: String): StreamDescriptor? = try {
            val o = org.json.JSONObject(raw)
            StreamDescriptor(
                playbackId = o.optString("playback_id"),
                mediaId = o.optString("media_id"),
                url = o.optString("url"),
                mimeType = o.optString("mime_type", "video/mp4"),
                grant = o.optString("grant"),
                durationMs = o.optLong("duration_ms", 0L),
                startPositionMs = o.optLong("start_position_ms", 0L),
            )
        } catch (e: Exception) {
            null
        }.takeIf { it?.playbackId?.isNotEmpty() == true && it.grant.isNotEmpty() }
    }
}
