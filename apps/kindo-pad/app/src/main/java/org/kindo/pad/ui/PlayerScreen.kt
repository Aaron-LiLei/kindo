package org.kindo.pad.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import kotlinx.coroutines.delay
import org.kindo.pad.AppViewModel
import org.kindo.pad.playback.TrackRef
import org.kindo.pad.ui.theme.KindoColors
import org.kindo.pad.ui.theme.PillShape

/**
 * 播放器（交互 §4.4 + 学龄前视觉 v2，与 TV 端同构）：视频 + 进度条 + 基本
 * 控制 + 字幕/音轨 + 语音 AI。音频模式整页阳光奶油底（PLY-008）。
 *
 * Pad 差异（Pad 端设计决策 2026-08-31）：
 * - 沉浸式全屏（隐藏系统栏），退出播放时恢复——触屏视频 App 主流惯例
 * - 单击画面切换控制条（无操作 5s 自动隐藏，播放中）——与 TV 相同的
 *   interactionTick 计时口径
 * - 屏上 ← 退出（TV 移除屏上返回是 D-pad 焦点陷阱问题；触屏无此问题，
 *   学龄前孩子不认识手势导航，需要可见出口）+ 系统返回手势双通道
 * - 控制按钮全部触摸直达（无焦点轮巡）
 */
@Composable
fun PlayerScreen(viewModel: AppViewModel, voice: VoiceEntry) {
    val controller = viewModel.playbackController
    val nowPlaying by controller.nowPlaying.collectAsState()
    val positionMs by controller.positionMs.collectAsState()
    val durationMs by controller.durationMs.collectAsState()
    val isPlaying by controller.isPlaying.collectAsState()
    val isBuffering by controller.isBuffering.collectAsState()
    val ended by controller.playbackEnded.collectAsState()
    val everStarted by controller.everStarted.collectAsState()
    val speed by controller.playbackSpeed.collectAsState()
    val deny by viewModel.denyMessage.collectAsState()
    val denyRetryable by viewModel.denyRetryable.collectAsState()
    val caps by viewModel.capabilities.collectAsState()
    val aiReady = caps.capabilities.voice_available && caps.capabilities.ai_available

    var controlsVisible by remember { mutableStateOf(true) }
    var interactionTick by remember { mutableIntStateOf(0) }
    var trackDialog by remember { mutableStateOf<String?>(null) } // "audio" | "subtitle"

    // 沉浸式：隐藏系统栏；离开播放器（组合销毁）自动恢复
    val view = LocalView.current
    val context = LocalContext.current
    DisposableEffect(Unit) {
        val window = (context as? android.app.Activity)?.window
        val insetsController = window?.let { WindowCompat.getInsetsController(it, view) }
        insetsController?.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        insetsController?.hide(WindowInsetsCompat.Type.systemBars())
        onDispose { insetsController?.show(WindowInsetsCompat.Type.systemBars()) }
    }

    // 无操作 5s 隐藏控制条（播放中）。interactionTick 在每次真实交互时 +1 重置计时。
    LaunchedEffect(controlsVisible, isPlaying, interactionTick) {
        if (controlsVisible && isPlaying) {
            delay(5000)
            controlsVisible = false
        }
    }

    // BACK 分级：未起播的问题态（卡缓冲/起播失败）直接退出——黑屏下"先收控制条"
    // 读作没反应（模拟器实测反馈，Pad 返回决策 2026-09-01）；已起播时控制条可见先收
    // 控制条，收起后本 BackHandler 失效、落到 KindoApp 的栈级 BackHandler → 退出播放
    // （对话框由各自 onDismissRequest 处理）
    BackHandler(enabled = trackDialog == null && deny == null && controlsVisible && everStarted) {
        controlsVisible = false
    }

    fun showControls() {
        controlsVisible = true
        interactionTick++
    }

    // 音频模式：整页阳光奶油底（视频模式仍是纯黑）
    val nowMediaId = nowPlaying.mediaId
    val isAudio = nowMediaId.isNotEmpty() &&
        org.kindo.pad.net.AudioPlaybackHint.isAudio(nowPlaying.mimeType)

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(if (isAudio) KindoColors.background else Color.Black)
            .pointerInput(Unit) {
                // 触屏：单击画面切换控制条
                detectTapGestures(onTap = {
                    if (controlsVisible) controlsVisible = false else showControls()
                })
            },
    ) {
        AndroidView(
            factory = { ctx ->
                PlayerView(ctx).apply {
                    useController = false
                    resizeMode = AspectRatioFrameLayout.RESIZE_MODE_FIT
                    setShutterBackgroundColor(android.graphics.Color.BLACK)
                }
            },
            update = { view -> view.player = controller.player },
            modifier = Modifier.fillMaxSize(),
        )

        // 音频播放页（PLY-008）：modality=AUDIO 时以封面 + 标题呈现（无视频画面）
        if (isAudio) {
            Box(
                Modifier.fillMaxSize().background(
                    Brush.verticalGradient(
                        listOf(KindoColors.backgroundTop, KindoColors.background)
                    )
                )
            )
            Column(
                modifier = Modifier
                    .align(Alignment.Center)
                    .windowInsetsPadding(WindowInsets.systemBars.only(WindowInsetsSides.Vertical)),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                Box(modifier = Modifier.kidBreathing()) {
                    PosterImage(
                        url = viewModel.hub.baseUrl.let { base ->
                            "$base/api/v1/media/$nowMediaId/poster"
                        },
                        token = viewModel.hub.deviceToken,
                        modifier = Modifier.width(260.dp).height(260.dp),
                    )
                }
                Text(
                    nowPlaying.title,
                    color = KindoColors.textPrimary, fontSize = 28.sp,
                    fontWeight = FontWeight.Bold, maxLines = 2,
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("🎵", fontSize = 20.sp)
                    Spacer(Modifier.width(8.dp))
                    Text("正在听", color = KindoColors.textSecondary, fontSize = 19.sp)
                }
            }
        }

        // 播完引导（TV 审计 P1-3 + PLY-007 同口径）：有下一集且服务端放行才出现"下一集"
        if (ended) {
            val next by viewModel.nextEpisode.collectAsState()
            Column(
                modifier = Modifier
                    .align(Alignment.Center)
                    .background(Color(0xCC1E140C), RoundedCornerShape(32.dp))
                    .padding(horizontal = 48.dp, vertical = 36.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(22.dp),
            ) {
                Text("🎉", fontSize = 56.sp)
                Text("看完啦！", color = KindoColors.onDark,
                     fontSize = 38.sp, fontWeight = FontWeight.Bold)
                Row(horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                    next?.let { ep ->
                        // 显式选择下一集：同触屏点播走 POST /playbacks 的 Policy 校验
                        KidButton(
                            emoji = "▶", text = "看第 ${ep.episode_no} 集",
                            onClick = { viewModel.playNextEpisode() },
                            fontSize = 22,
                        )
                    }
                    KidButton(
                        emoji = "↻", text = "再看一遍",
                        // 重播=新的播放请求：Grant 已随播放结束收口，旧 Grant 重播
                        // 会被逐次校验拒绝（BAD_HTTP_STATUS）；同触屏点播走
                        // POST /playbacks 统一过 Policy（硬性约束 3/架构 A-06）
                        onClick = { viewModel.playFromUi(nowPlaying.mediaId, 0L) },
                        container = KindoColors.kidGreen, fontSize = 22,
                    )
                    KidButton(
                        emoji = "🏠", text = "回去看别的",
                        onClick = { viewModel.goBack() },
                        container = KindoColors.kidBlue, fontSize = 22,
                    )
                }
            }
        }

        // 缓冲指示（网盘起播/seek 后的加载期）
        if (isBuffering) {
            Column(
                modifier = Modifier.align(Alignment.Center),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                CircularProgressIndicator(color = KindoColors.accent)
                Text("马上就好…", color = KindoColors.onDark, fontSize = 19.sp)
            }
        }

        if (controlsVisible) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .windowInsetsPadding(WindowInsets.systemBars),
            ) {
                // 顶部：← 退出（触屏可见出口）+ 正在播放标题
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(if (isAudio) Color(0x334A3628) else Color(0x66000000))
                        .padding(horizontal = 20.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box(
                        modifier = Modifier
                            .padClickable { viewModel.goBack() }
                            .background(Color(0x33FFFFFF), RoundedCornerShape(999.dp))
                            .padding(horizontal = 18.dp, vertical = 8.dp),
                    ) {
                        Text("←", color = KindoColors.onDark, fontSize = 24.sp,
                             fontWeight = FontWeight.Black)
                    }
                    Spacer(Modifier.width(16.dp))
                    Text(
                        nowPlaying.title,
                        color = KindoColors.onDark,
                        fontSize = 22.sp,
                        fontWeight = FontWeight.Bold,
                        maxLines = 1,
                    )
                }
                Spacer(Modifier.weight(1f))
                // 底部控制条：进度 + 触摸按钮行
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(if (isAudio) Color(0x664A3628) else Color(0x99000000))
                        .padding(horizontal = 32.dp, vertical = 18.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    // 进度条（可拖动 seek）
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(formatMs(positionMs), color = KindoColors.onDark, fontSize = 18.sp,
                             fontWeight = FontWeight.Bold)
                        var dragFraction by remember { mutableStateOf<Float?>(null) }
                        Slider(
                            value = dragFraction
                                ?: (if (durationMs > 0) positionMs.toFloat() / durationMs else 0f),
                            onValueChange = { frac -> dragFraction = frac },
                            onValueChangeFinished = {
                                dragFraction?.let { frac ->
                                    if (durationMs > 0) controller.seekTo((frac * durationMs).toLong())
                                }
                                dragFraction = null
                            },
                            valueRange = 0f..1f,
                            colors = SliderDefaults.colors(
                                thumbColor = KindoColors.accent,
                                activeTrackColor = KindoColors.accent,
                                inactiveTrackColor = Color(0x66FFFFFF),
                            ),
                            modifier = Modifier.weight(1f).padding(horizontal = 14.dp),
                        )
                        Text(
                            formatMs(durationMs),
                            color = Color(0xCCFFF6EA), fontSize = 18.sp,
                        )
                    }
                    // 横向滚动兜底：窄横屏放不下全部控制药丸时滑动可选
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                    ) {
                        GlassPill("⏪ 10秒") {
                            showControls(); controller.seekBy(-10_000)
                        }
                        GlassPill(if (isPlaying) "⏸" else "▶") {
                            showControls()
                            if (isPlaying) controller.pause() else controller.resume()
                        }
                        GlassPill("10秒 ⏩") {
                            showControls(); controller.seekBy(10_000)
                        }
                        // 倍速循环 1.0→1.25→1.5→0.75（CRS-006；观看时长按实际区间累计不受影响）
                        GlassPill("⏩ x${speed}") { showControls(); controller.cycleSpeed() }
                        if (nowPlaying.audioTracks.size > 1) {
                            GlassPill("🎧 声音") { trackDialog = "audio" }
                        }
                        if (nowPlaying.subtitleTracks.isNotEmpty()) {
                            GlassPill(if (nowPlaying.selectedSubtitleTrackId != null) "💬 字幕 开" else "💬 字幕 关") {
                                trackDialog = "subtitle"
                            }
                        }
                        Spacer(Modifier.width(4.dp))
                        Spacer(Modifier.weight(1f))
                        // AI 不可用时按钮转提示态（交互 §7.5 同口径；此前静默无反馈）
                        if (aiReady) {
                            Box(
                                modifier = Modifier
                                    .padClickable { voice.start(false) }
                                    .background(KindoColors.accent, PillShape)
                                    .padding(horizontal = 20.dp, vertical = 10.dp),
                            ) {
                                Text("🎤 问小熊", color = Color.White, fontSize = 18.sp,
                                     fontWeight = FontWeight.Bold)
                            }
                        } else {
                            Box(
                                modifier = Modifier
                                    .padClickable(enabled = false) { }
                                    .background(Color(0x2EFFFFFF), PillShape)
                                    .padding(horizontal = 20.dp, vertical = 10.dp),
                            ) {
                                Text("🎤 小熊在睡觉", color = Color(0xB3FFF6EA), fontSize = 18.sp,
                                     fontWeight = FontWeight.Bold)
                            }
                        }
                    }
                }
            }
        }

        trackDialog?.let { which ->
            TrackSelectionDialog(
                title = if (which == "audio") "选择音轨" else "选择字幕",
                tracks = if (which == "audio") nowPlaying.audioTracks else
                    listOf(TrackRef("", "关闭字幕")) + nowPlaying.subtitleTracks,
                selectedId = if (which == "audio") nowPlaying.selectedAudioTrackId
                else (nowPlaying.selectedSubtitleTrackId ?: ""),
                onSelect = { trackId ->
                    if (which == "audio" && trackId != null) {
                        controller.selectAudioTrack(trackId)
                    } else if (which == "subtitle") {
                        controller.selectSubtitleTrack(trackId?.takeIf { it.isNotEmpty() })
                    }
                    trackDialog = null
                },
                // 点外部关闭仅收起对话框，不改选择（此前误把字幕关掉，审计 P2-5）
                onClose = { trackDialog = null },
            )
        }

        deny?.let { msg ->
            DenyDialog(msg, { viewModel.dismissDeny() },
                if (denyRetryable) ({ viewModel.retryLastPlay() }) else null)
        }
    }
}

/** 播放器控制条按钮：暖黑玻璃药丸（学龄前：超大、可辨、无灰色描边）。 */
@Composable
private fun GlassPill(label: String, onClick: () -> Unit) {
    Box(
        modifier = Modifier
            .padClickable(onClick = onClick)
            .background(Color(0x2EFFFFFF), PillShape)
            .border(1.dp, Color(0x33FFFFFF), PillShape)
            .padding(horizontal = 18.dp, vertical = 10.dp),
    ) {
        Text(label, color = KindoColors.onDark, fontSize = 19.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun TrackSelectionDialog(
    title: String,
    tracks: List<TrackRef>,
    selectedId: String?,
    onSelect: (String?) -> Unit,
    onClose: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text(title, color = KindoColors.textPrimary) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                tracks.forEach { track ->
                    val selected = track.id == selectedId && !(track.id.isEmpty() && selectedId.isNullOrEmpty())
                    TextButton(onClick = { onSelect(track.id) }) {
                        Text(
                            (if (selected) "✓ " else "") + track.label,
                            color = if (selected) KindoColors.accentDeep else KindoColors.textPrimary,
                            fontSize = 19.sp, fontWeight = FontWeight.Bold,
                        )
                    }
                }
            }
        },
        confirmButton = {},
        containerColor = KindoColors.surface,
    )
}

private fun formatMs(ms: Long): String {
    val total = ms / 1000
    val h = total / 3600
    val m = (total % 3600) / 60
    val s = total % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, s) else "%02d:%02d".format(m, s)
}
