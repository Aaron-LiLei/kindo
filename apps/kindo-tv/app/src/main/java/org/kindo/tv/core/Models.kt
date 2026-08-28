package org.kindo.tv.core

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/** Hub REST 契约数据模型（技术方案 §3.1）。字段名与 Hub 响应一一对应。 */

@Serializable
data class BootstrapInfo(
    val instance_id: String = "",
    val display_name: String = "",
    val api_version: Int = 0,
    val capabilities: Capabilities = Capabilities(),
)

@Serializable
data class Capabilities(
    val voice_available: Boolean = false,
    val ai_available: Boolean = false,
    val tts_available: Boolean = false,
)

@Serializable
data class HomeData(
    val continue_watching: List<ContinueItem> = emptyList(),
    /** 未听完的音频（story/song，交互 §4.2"继续收听"行） */
    val continue_listening: List<ContinueItem> = emptyList(),
    val continue_learning: List<LearningItem> = emptyList(),
    val explore_themes: List<String> = emptyList(),
    val recent_series: List<SeriesRef> = emptyList(),
)

@Serializable
data class ContinueItem(
    val media_id: String,
    val title: String,
    val media_type: String = "",
    val duration_ms: Long = 0,
    val last_position_ms: Long = 0,
)

@Serializable
data class LearningItem(
    val course_id: String = "",
    val course_title: String = "",
    val lesson_no: Int = 0,
    val media_id: String,
    val title: String,
    val position_ms: Long = 0,
)

@Serializable
data class SeriesRef(
    val series_id: String,
    val title: String,
    val language: String? = null,
    val count: Int = 0,
    val cover_media_id: String? = null,
    val cover_has_poster: Boolean = false,
    /** v0.3：系列实体海报（TMDB，优先于集级） */
    val entity_id: String? = null,
    val entity_poster: Boolean = false,
)

/** 系列聚合（GET /api/v1/collections，镜像 Admin 聚合）。 */
@Serializable
data class CollectionsResp(
    val series: List<SeriesCollection> = emptyList(),
    val courses: List<SeriesCollection> = emptyList(),
    /** 库内实际类型分布（筛选 chips 按内容派生，空类型不显示） */
    val type_counts: Map<String, Int> = emptyMap(),
)

@Serializable
data class SeriesCollection(
    val series_id: String? = null,
    val course_id: String? = null,
    val title: String,
    val language: String? = null,
    val count: Int = 0,
    val cover_media_id: String? = null,
    val cover_has_poster: Boolean = false,
    val age_band: String? = null,
    val tags: List<String> = emptyList(),
    /** v0.3：系列实体锚点（Series poster 优先，MED-013） */
    val entity_id: String? = null,
    val entity_poster: Boolean = false,
    val match_status: String? = null,
)

@Serializable
data class MediaPage(
    val items: List<MediaSummary> = emptyList(),
    val next_cursor: String? = null,
)

@Serializable
data class MediaSummary(
    val media_id: String,
    val title: String,
    val media_type: String = "",
    val duration_ms: Long = 0,
    val language: String? = null,
    val age_band: String? = null,
    val tags: Map<String, List<String>> = emptyMap(),
    val playable: Boolean = true,
    val has_poster: Boolean = false,
    // 系列集列表专用（2026-08-27）：集号驱动"第 N 集"数字卡；
    // 旧 Hub（未部署）无这些字段 → null/默认，TV 回退 MediaCard 文件名卡
    val episode_no: Int? = null,
    val last_position_ms: Long = 0,
    val completed: Boolean = false,
)

@Serializable
data class WatchState(
    val last_position_ms: Long = 0,
    val watched_seconds: Long = 0,
    val completed: Boolean = false,
)

@Serializable
data class MediaDetail(
    val media_id: String,
    val title: String,
    val media_type: String = "",
    val duration_ms: Long = 0,
    val language: String? = null,
    val age_band: String? = null,
    val tags: Map<String, List<String>> = emptyMap(),
    val playable: Boolean = true,
    val has_poster: Boolean = false,
    val watch: WatchState? = null,
    val resume_position_ms: Long = 0,
    val series: SeriesInfo? = null,
    val course: CourseInfo? = null,
    val subtitle_tracks: List<TrackInfo> = emptyList(),
    val audio_tracks: List<TrackInfo> = emptyList(),
    val actions: ActionsInfo? = null,
    /** v0.3 Canonical 维度与简介（交互 §4.3：儿童端只展示） */
    val overview: String? = null,
    val modality: String? = null,
    val content_class: String? = null,
    /** 系列实体海报（集级无海报时详情页大图回退） */
    val series_entity_id: String? = null,
    val series_entity_poster: Boolean = false,
    /** §1.2 兼容信息（direct play 兜底，2026-08-26） */
    val compatibility: CompatInfo? = null,
)

@Serializable
data class CompatInfo(
    val playable: Boolean = true,
    val probed: Boolean = true,
    val container: String? = null,
    val video_codec: String? = null,
    val notes: List<String> = emptyList(),
)

@Serializable
data class TrackInfo(
    val id: String = "",
    val language: String? = null,
    val label: String? = null,
    val source_type: String? = null,
)

@Serializable
data class SeriesInfo(
    val series_id: String,
    val title: String,
    val season_no: Int? = null,
    val episode_no: Int? = null,
    val episodes: List<EpisodeInfo> = emptyList(),
)

@Serializable
data class EpisodeInfo(
    val media_id: String,
    val title: String,
    val season_no: Int = 1,
    val episode_no: Int = 0,
    val duration_ms: Long = 0,
    val has_poster: Boolean = false,
    val last_position_ms: Long = 0,
    val completed: Boolean = false,
)

@Serializable
data class CourseInfo(
    val course_id: String,
    val title: String,
    val chapter_no: Int? = null,
    val lesson_no: Int? = null,
    val lessons: List<LessonInfo> = emptyList(),
)

@Serializable
data class LessonInfo(
    val media_id: String,
    val lesson_id: String = "",
    val title: String,
    val chapter_no: Int = 0,
    val lesson_no: Int = 0,
    val duration_ms: Long = 0,
    val position_ms: Long = 0,
    val completed: Boolean = false,
)

@Serializable
data class ActionsInfo(val play: ActionPlay = ActionPlay())

@Serializable
data class ActionPlay(
    val allowed: Boolean = true,
    val reason_code: String? = null,
    /** 维度化预检数据（交互 v0.3 §7.3：按媒介/分类给儿童提示） */
    val constraints: PlayConstraints? = null,
)

/** Policy 结构化约束（403 body / 预检 actions 共用；未知字段忽略）。 */
@Serializable
data class PlayConstraints(
    val content_class: String? = null,
    val modality: String? = null,
    val allowed_modalities: List<String> = emptyList(),
    val remaining: BudgetRemaining? = null,
)

@Serializable
data class BudgetRemaining(
    val screen_total_seconds: Long? = null,
    val video_class_seconds: Long? = null,
    val audio_seconds: Long? = null,
    val ai_voice_seconds: Long? = null,
)

/** POST /api/v1/playbacks 成功响应。 */
@Serializable
data class PlaybackResponse(
    val playback_id: String,
    val decision: String = "allow",
    val stream_descriptor: StreamDescriptorDto = StreamDescriptorDto(),
)

/** GET /api/v1/playbacks/current（REST 对齐用）。 */
@Serializable
data class CurrentPlaybackResp(val playback: CurrentPlayback? = null)

@Serializable
data class CurrentPlayback(
    val playback_id: String = "",
    val media_id: String = "",
    val title: String? = null,
    val state: String = "",
    val position_ms: Long = 0,
    val duration_ms: Long = 0,
)

@Serializable
data class StreamDescriptorDto(
    val playback_id: String = "",
    val media_id: String = "",
    val url: String = "",
    val mime_type: String? = null,  // 网络源跳过探测时 Hub 可能回 null（2026-08-21）
    val grant: String = "",
    val duration_ms: Long = 0,
    val start_position_ms: Long = 0,
    val audio_tracks: List<TrackInfo> = emptyList(),
    val subtitle_tracks: List<TrackInfo> = emptyList(),
)

/** Policy 拒绝（403 结构化响应，技术方案 §15.1；顶层平铺含 constraints）。 */
@Serializable
data class DenyInfo(
    val decision: String = "deny",
    val reason_code: String = "policy_denied",
    val request_id: String? = null,
    val constraints: PlayConstraints? = null,
)

@Serializable
data class PairingRequestResult(
    val pairing_id: String,
    val pairing_secret: String = "",
    val display_code: String = "",
)

@Serializable
data class PairingStatus(
    val state: String = "pending",
    val device_token: String? = null,
)

@Serializable
data class ConversationCreated(
    val session_id: String,
    val state: String = "",
    val follow_up_seconds: Int = 6,
    val idle_timeout_seconds: Int = 600,
)

/** Realtime 事件中 stream_descriptor 以 JSON 字符串透传（§4.1 嵌套对象），此处解析。 */
object ModelsJson {
    private val json = Json { ignoreUnknownKeys = true }

    fun parseDescriptor(raw: String): StreamDescriptorDto? = try {
        json.decodeFromString<StreamDescriptorDto>(raw)
    } catch (_: Exception) {
        null
    }

    fun parseConstraints(raw: String): PlayConstraints? = try {
        json.decodeFromString<PlayConstraints>(raw)
    } catch (_: Exception) {
        null
    }
}
