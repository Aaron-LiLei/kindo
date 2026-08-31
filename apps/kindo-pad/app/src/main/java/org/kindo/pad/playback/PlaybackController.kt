package org.kindo.pad.playback

import android.content.Context
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.ProgressiveMediaSource
import androidx.media3.exoplayer.trackselection.DefaultTrackSelector
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import java.util.UUID
import java.util.concurrent.TimeUnit

/** 当前播放条目（UI 展示 + 轨道切换）。 */
data class NowPlaying(
    val playbackId: String = "",
    val mediaId: String = "",
    val title: String = "",
    val mimeType: String? = null,
    val audioTracks: List<TrackRef> = emptyList(),
    val subtitleTracks: List<TrackRef> = emptyList(),
    val selectedAudioTrackId: String? = null,
    val selectedSubtitleTrackId: String? = null,
)

data class TrackRef(val id: String, val label: String)

/**
 * 播放会话（技术方案 §9）：Media3 + Grant/Authorization Header（§9.3）、
 * 播放事件实时上报（§9.5）、音轨/字幕轨选择 + track_changed 上报（§4.1）。
 * 全部方法必须在主线程调用。
 */
class PlaybackController(private val appContext: Context) {
    private val scope = CoroutineScope(Dispatchers.Main)
    var player: ExoPlayer? = null
        private set
    private var progressJob: Job? = null
    private var currentPlaybackId: String? = null
    private var lastReportedState: String = "idle"

    var eventSender: ((eventId: String, playbackId: String, kind: String, positionMs: Long) -> Unit)? = null
    var trackChangedSender: ((playbackId: String, audioTrackId: String?, subtitleTrackId: String?) -> Unit)? = null
    var onPlayerError: ((String) -> Unit)? = null

    private val _nowPlaying = MutableStateFlow(NowPlaying())
    val nowPlaying: StateFlow<NowPlaying> = _nowPlaying

    private val _positionMs = MutableStateFlow(0L)
    val positionMs: StateFlow<Long> = _positionMs

    private val _durationMs = MutableStateFlow(0L)
    val durationMs: StateFlow<Long> = _durationMs

    private val _isPlaying = MutableStateFlow(false)
    val isPlaying: StateFlow<Boolean> = _isPlaying

    private val _isBuffering = MutableStateFlow(false)
    val isBuffering: StateFlow<Boolean> = _isBuffering

    /** 播放自然结束（STATE_ENDED）——UI 呈现"看完啦"引导（TV 审计 P1-3）。 */
    private val _playbackEnded = MutableStateFlow(false)
    val playbackEnded: StateFlow<Boolean> = _playbackEnded

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)  // 网盘链路首字节可达 30s+（stat+range 两跳）
        .build()

    private var trackSelector: DefaultTrackSelector? = null
    private var currentDescriptor: StreamDescriptor? = null
    private var deviceToken: String = ""
    private var baseUrl: String = ""

    fun ensureStarted() {
        if (player != null) return
        trackSelector = DefaultTrackSelector(appContext)
        player = ExoPlayer.Builder(appContext)
            .setTrackSelector(trackSelector!!)
            .build().also { p ->
                p.addListener(object : Player.Listener {
                    override fun onIsPlayingChanged(isPlaying: Boolean) {
                        _isPlaying.value = isPlaying
                        val pid = currentPlaybackId ?: return
                        val pos = p.currentPosition
                        when {
                            isPlaying && lastReportedState != "started" -> {
                                lastReportedState = "started"
                                eventSender?.invoke(UUID.randomUUID().toString(), pid, "started", pos)
                            }
                            !isPlaying && p.playbackState == Player.STATE_READY &&
                                lastReportedState == "started" -> {
                                lastReportedState = "paused"
                                eventSender?.invoke(UUID.randomUUID().toString(), pid, "paused", pos)
                            }
                        }
                    }

                    override fun onPlaybackStateChanged(playbackState: Int) {
                        _isBuffering.value = playbackState == Player.STATE_BUFFERING
                        _durationMs.value = p.duration.coerceAtLeast(0)
                        if (playbackState == Player.STATE_ENDED) {
                            _playbackEnded.value = true
                            val pid = currentPlaybackId ?: return
                            eventSender?.invoke(UUID.randomUUID().toString(), pid, "ended", p.currentPosition)
                        }
                    }

                    override fun onPlayerError(error: PlaybackException) {
                        lastReportedState = "error"
                        _isPlaying.value = false
                        val pid = currentPlaybackId
                        if (pid != null) {
                            eventSender?.invoke(UUID.randomUUID().toString(), pid, "error", p.currentPosition)
                        }
                        onPlayerError?.invoke(error.errorCodeName)
                    }
                })
                scope.launch {
                    while (isActive) {
                        _positionMs.value = p.currentPosition.coerceAtLeast(0)
                        _durationMs.value = p.duration.coerceAtLeast(0)
                        delay(500)
                    }
                }
            }
    }

    /** 播放（descriptor 可来自 REST /playbacks 或 realtime playback.command）。 */
    fun play(
        descriptor: StreamDescriptor,
        title: String,
        deviceToken: String,
        baseUrl: String,
        audioTracks: List<TrackRef>,
        subtitleTracks: List<TrackRef>,
    ) {
        val p = player ?: return
        currentDescriptor = descriptor
        this.deviceToken = deviceToken
        this.baseUrl = baseUrl
        currentPlaybackId = descriptor.playbackId
        lastReportedState = "idle"
        _playbackEnded.value = false

        // 媒体请求注入 Authorization 与 X-Kindo-Playback-Grant（§9.3；Grant 不进 URL）
        val okFactory = OkHttpDataSource.Factory(http)
            .setDefaultRequestProperties(
                mapOf(
                    "Authorization" to "Bearer $deviceToken",
                    "X-Kindo-Playback-Grant" to descriptor.grant,
                ),
            )
        val dataSourceFactory = DefaultDataSource.Factory(appContext, okFactory)

        val subtitleConfigs = subtitleTracks.map { track ->
            MediaItem.SubtitleConfiguration.Builder(
                android.net.Uri.parse(baseUrl + "/api/v1/media/${descriptor.mediaId}/subtitles/${track.id}"),
            )
                .setMimeType(MimeTypes.TEXT_VTT)
                .setLabel(track.label)
                .build()
        }
        val source = ProgressiveMediaSource.Factory(dataSourceFactory)
            .createMediaSource(
                MediaItem.Builder()
                    .setUri(baseUrl + descriptor.url)
                    .setMimeType(descriptor.mimeType)
                    .setSubtitleConfigurations(subtitleConfigs)
                    .build(),
            )
        p.setMediaSource(source)
        p.prepare()
        p.seekTo(descriptor.startPositionMs)
        p.setPlaybackSpeed(1f)  // 新播放重置倍速（速度不跨内容记忆）
        _playbackSpeed.value = 1f
        p.playWhenReady = true

        _nowPlaying.value = NowPlaying(
            playbackId = descriptor.playbackId,
            mediaId = descriptor.mediaId,
            title = title,
            mimeType = descriptor.mimeType,
            audioTracks = audioTracks,
            subtitleTracks = subtitleTracks,
            selectedAudioTrackId = audioTracks.firstOrNull()?.id,
            selectedSubtitleTrackId = null,
        )

        progressJob?.cancel()
        progressJob = scope.launch {
            while (isActive) {
                delay(5000)
                val pid = currentPlaybackId ?: continue
                if (p.isPlaying) {
                    eventSender?.invoke(UUID.randomUUID().toString(), pid, "progress", p.currentPosition)
                }
            }
        }
    }

    fun pause() {
        player?.pause()
    }

    // ---------- 倍速（CRS-006 P1：课程/视频通用的播放速度） ----------

    private val _playbackSpeed = MutableStateFlow(1f)
    val playbackSpeed: StateFlow<Float> = _playbackSpeed

    /** 在 1.0 → 1.25 → 1.5 → 0.75 间循环；新播放重置为 1.0。 */
    fun cycleSpeed() {
        val next = when (_playbackSpeed.value) {
            1f -> 1.25f
            1.25f -> 1.5f
            1.5f -> 0.75f
            else -> 1f
        }
        setSpeed(next)
    }

    fun setSpeed(speed: Float) {
        player?.setPlaybackSpeed(speed)
        _playbackSpeed.value = speed
    }

    /** 语音/AI 路径补标题（titleOf 无详情上下文时异步回填）。 */
    fun updateTitle(title: String) {
        if (_nowPlaying.value.title.isEmpty()) {
            _nowPlaying.value = _nowPlaying.value.copy(title = title)
        }
    }

    fun resume() {
        val p = player ?: return
        if (p.playbackState == Player.STATE_ENDED) {
            // 播放结束后重新点播放：从头开始（交互 §4.4 停留结束页不连播）
            p.seekTo(0)
        }
        p.play()
    }

    fun seekTo(ms: Long) {
        val p = player ?: return
        _playbackEnded.value = false
        p.seekTo(ms.coerceIn(0, if (p.duration > 0) p.duration else Long.MAX_VALUE))
        currentPlaybackId?.let {
            eventSender?.invoke(UUID.randomUUID().toString(), it, "seeked", p.currentPosition)
        }
    }

    fun seekBy(deltaMs: Long) {
        val p = player ?: return
        seekTo(p.currentPosition + deltaMs)
    }

    fun stop() {
        val pid = currentPlaybackId
        progressJob?.cancel()
        if (pid != null) {
            eventSender?.invoke(UUID.randomUUID().toString(), pid, "stopped", player?.currentPosition ?: 0)
        }
        player?.stop()
        player?.clearMediaItems()
        currentPlaybackId = null
        _playbackEnded.value = false
        _nowPlaying.value = NowPlaying()
        _isPlaying.value = false
        _positionMs.value = 0
        _durationMs.value = 0
    }

    /** 会话问答时压低（V0.1 基线=暂停，交互 §6）。 */
    fun duck() {
        player?.pause()
    }

    /** TTS 结束后恢复播放；仅在此前确实因 duck 暂停时（§6 追问结束自动恢复）。 */
    fun unduck() {
        if (currentPlaybackId != null && lastReportedState == "paused") player?.play()
    }

    /** 音轨选择：按第 n 个音频轨道组对应 hub 提供的第 n 条（渐进式媒体的近似映射）。 */
    fun selectAudioTrack(trackId: String) {
        val p = player ?: return
        val idx = _nowPlaying.value.audioTracks.indexOfFirst { it.id == trackId }
        if (idx < 0) return
        val groups = p.currentTracks.groups.filter { it.type == androidx.media3.common.C.TRACK_TYPE_AUDIO }
        if (idx < groups.size) {
            trackSelector?.parameters = trackSelector!!.buildUponParameters()
                .setOverrideForType(TrackSelectionOverride(groups[idx].mediaTrackGroup, 0))
                .build()
            _nowPlaying.value = _nowPlaying.value.copy(selectedAudioTrackId = trackId)
            currentPlaybackId?.let { trackChangedSender?.invoke(it, trackId, null) }
        }
    }

    /** 字幕轨选择；null = 关闭字幕。 */
    fun selectSubtitleTrack(trackId: String?) {
        val p = player ?: return
        val builder = trackSelector!!.buildUponParameters()
        if (trackId == null) {
            builder.setTrackTypeDisabled(androidx.media3.common.C.TRACK_TYPE_TEXT, true)
        } else {
            builder.setTrackTypeDisabled(androidx.media3.common.C.TRACK_TYPE_TEXT, false)
            val idx = _nowPlaying.value.subtitleTracks.indexOfFirst { it.id == trackId }
            val groups = p.currentTracks.groups.filter { it.type == androidx.media3.common.C.TRACK_TYPE_TEXT }
            if (idx in groups.indices) {
                builder.setOverrideForType(TrackSelectionOverride(groups[idx].mediaTrackGroup, 0))
            }
        }
        trackSelector?.parameters = builder.build()
        _nowPlaying.value = _nowPlaying.value.copy(selectedSubtitleTrackId = trackId)
        currentPlaybackId?.let { trackChangedSender?.invoke(it, null, trackId) }
    }

    fun release() {
        progressJob?.cancel()
        player?.release()
        player = null
    }
}
