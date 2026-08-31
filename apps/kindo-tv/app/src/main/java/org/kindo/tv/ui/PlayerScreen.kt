package org.kindo.tv.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectTapGestures
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
import androidx.compose.foundation.focusable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.activity.compose.BackHandler
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import kotlinx.coroutines.delay
import org.kindo.tv.AppViewModel
import org.kindo.tv.playback.TrackRef
import org.kindo.tv.ui.theme.KindoColors
import org.kindo.tv.ui.theme.PillShape

/**
 * 播放器（交互 §4.4 + 学龄前视觉 v2）：视频 + 进度条 + 基本控制 + 字幕/音轨 + 语音 AI。
 *
 * 交互对齐儿童场景（v2 调整）：
 * - 视频区保持纯黑（内容本体），控制条用暖黑玻璃药丸——不再是成人软件的灰色描边按钮
 * - 任意按键/单击画面唤出控制条；控制中/播放中 5s 无操作自动隐藏
 *   （以"交互计数"为计时锚点——此前以 positionMs 为 key 每秒重启协程，永不隐藏）
 * - 控制条隐藏时：OK/ENTER = 播放/暂停切换；左右 = ±10s 快退/快进
 * - 返回一律走遥控器 BACK：先收控制条，再退出播放返回来源页
 * - BACK 分级：先收起控制条，再退出播放返回来源页
 * - 缓冲中显示加载指示（网盘直连起播有秒级延迟，避免"黑屏无反馈"）
 * - 音频模式整页换阳光奶油底：听故事的屏幕不该是一块黑板
 */
@Composable
fun PlayerScreen(viewModel: AppViewModel, micGranted: Boolean) {
    val controller = viewModel.playbackController
    val nowPlaying by controller.nowPlaying.collectAsState()
    val positionMs by controller.positionMs.collectAsState()
    val durationMs by controller.durationMs.collectAsState()
    val isPlaying by controller.isPlaying.collectAsState()
    val isBuffering by controller.isBuffering.collectAsState()
    val ended by controller.playbackEnded.collectAsState()
    val speed by controller.playbackSpeed.collectAsState()
    val deny by viewModel.denyMessage.collectAsState()
    val denyRetryable by viewModel.denyRetryable.collectAsState()
    val caps by viewModel.capabilities.collectAsState()
    val aiReady = caps.capabilities.voice_available && caps.capabilities.ai_available

    var controlsVisible by remember { mutableStateOf(true) }
    var interactionTick by remember { mutableIntStateOf(0) }
    var trackDialog by remember { mutableStateOf<String?>(null) } // "audio" | "subtitle"
    val controlsFocus = remember { FocusRequester() }
    // 控制条隐藏后组合内无焦点节点，按键不会派发——隐藏时把焦点收回到全屏
    // 容器（仍可接收 OK/左右/BACK 等预览键），显示时焦点进控制条
    val screenFocus = remember { FocusRequester() }

    LaunchedEffect(controlsVisible) {
        if (controlsVisible) controlsFocus.requestFocus() else screenFocus.requestFocus()
    }

    // 无操作 5s 隐藏控制条（播放中）。key 不含 positionMs——进度每秒变化会把
    // delay(5000) 永久重启掉；interactionTick 在每次真实交互时 +1 重置计时。
    LaunchedEffect(controlsVisible, isPlaying, interactionTick) {
        if (controlsVisible && isPlaying) {
            delay(5000)
            controlsVisible = false
        }
    }

    // BACK 分级：控制条可见时先收控制条（对话框由各自 onDismissRequest 处理），
    // 收起后本 BackHandler 失效、落到 KindoApp 的栈级 BackHandler → 退出播放
    BackHandler(enabled = trackDialog == null && deny == null && controlsVisible) {
        controlsVisible = false
    }

    fun showControls() {
        controlsVisible = true
        interactionTick++
    }

    // 音频模式：整页阳光奶油底（视频模式仍是纯黑）
    val nowMediaId = nowPlaying.mediaId
    val isAudio = nowMediaId.isNotEmpty() &&
        org.kindo.tv.net.AudioPlaybackHint.isAudio(nowPlaying.mimeType)

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(if (isAudio) KindoColors.background else Color.Black)
            .focusRequester(screenFocus)
            .focusable()
            .pointerInput(Unit) {
                // 鼠标/触屏：单击画面切换控制条（此前无任何响应，鼠标用户被困）
                detectTapGestures(onTap = {
                    if (controlsVisible) controlsVisible = false else showControls()
                })
            }
            .onPreviewKeyEvent { event ->
                if (event.type == KeyEventType.KeyDown && event.key != Key.Back) {
                    if (!controlsVisible) {
                        when (event.key) {
                            // 控制条隐藏时 OK = 播放/暂停（Netflix 惯例）
                            Key.DirectionCenter, Key.Enter -> {
                                showControls()
                                if (isPlaying) controller.pause() else controller.resume()
                                return@onPreviewKeyEvent true
                            }
                            // 隐藏时左右 = ±10s 快退/快进
                            Key.DirectionLeft -> {
                                showControls()
                                controller.seekBy(-10_000)
                                return@onPreviewKeyEvent true
                            }
                            Key.DirectionRight -> {
                                showControls()
                                controller.seekBy(10_000)
                                return@onPreviewKeyEvent true
                            }
                            else -> showControls()
                        }
                    } else {
                        interactionTick++
                    }
                }
                false
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
                modifier = Modifier.align(Alignment.Center),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                Box(modifier = Modifier.kidBreathing()) {
                    PosterImage(
                        url = viewModel.hub.baseUrl.let { base ->
                            "$base/api/v1/media/$nowMediaId/poster"
                        },
                        token = viewModel.hub.deviceToken,
                        modifier = Modifier.width(280.dp).height(280.dp),
                    )
                }
                Text(
                    nowPlaying.title,
                    color = KindoColors.textPrimary, fontSize = 30.sp,
                    fontWeight = FontWeight.Bold, maxLines = 2,
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("🎵", fontSize = 22.sp)
                    Spacer(Modifier.width(8.dp))
                    Text("正在听", color = KindoColors.textSecondary, fontSize = 20.sp)
                }
            }
        }

        // 播完引导（TV 审计 P1-3 + PLY-007）：有下一集且服务端放行才出现"下一集"
        if (ended) {
            val next by viewModel.nextEpisode.collectAsState()
            Column(
                modifier = Modifier
                    .align(Alignment.Center)
                    .background(Color(0xCC1E140C), RoundedCornerShape(32.dp))
                    .padding(horizontal = 60.dp, vertical = 44.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(26.dp),
            ) {
                Text("🎉", fontSize = 64.sp)
                Text("看完啦！", color = KindoColors.onDark,
                     fontSize = 44.sp, fontWeight = FontWeight.Bold)
                Row(horizontalArrangement = Arrangement.spacedBy(22.dp)) {
                    next?.let { ep ->
                        // 显式选择下一集：同 D-pad 点播走 POST /playbacks 的 Policy 校验
                        KidButton(
                            emoji = "▶", text = "看第 ${ep.episode_no} 集",
                            onClick = { viewModel.playNextEpisode() },
                            fontSize = 24,
                        )
                    }
                    KidButton(
                        emoji = "↻", text = "再看一遍",
                        onClick = { controller.resume() }, // resume 对 ENDED 会 seek 0 重播
                        container = KindoColors.kidGreen, fontSize = 24,
                    )
                    KidButton(
                        emoji = "🏠", text = "回去看别的",
                        onClick = { viewModel.goBack() },
                        container = KindoColors.kidBlue, fontSize = 24,
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
                Text("马上就好…", color = KindoColors.onDark, fontSize = 20.sp)
            }
        }

        if (controlsVisible) {
            // 顶部：正在播放信息（返回一律走遥控器 BACK——先收控制条再退出；
            // 屏上 ← 已移除：孩子 D-pad 导航时不该撞到角落里的返回陷阱）
            Row(
                modifier = Modifier
                    .align(Alignment.TopStart)
                    .fillMaxWidth()
                    .background(if (isAudio) Color(0x334A3628) else Color(0x66000000))
                    .padding(horizontal = 32.dp, vertical = 14.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    nowPlaying.title,
                    color = KindoColors.onDark,
                    fontSize = 24.sp,
                    fontWeight = FontWeight.Bold,
                    maxLines = 1,
                )
            }
            Column(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .fillMaxWidth()
                    .background(if (isAudio) Color(0x664A3628) else Color(0x99000000))
                    .padding(horizontal = 48.dp, vertical = 24.dp)
                    .focusRequester(controlsFocus)
                    .focusable(),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                // 进度条（可聚焦 seek）
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(formatMs(positionMs), color = KindoColors.onDark, fontSize = 20.sp,
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
                        color = Color(0xCCFFF6EA), fontSize = 20.sp,
                    )
                }
                Row(
                    horizontalArrangement = Arrangement.spacedBy(14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    GlassPill("⏪ 10秒") { controller.seekBy(-10_000) }
                    GlassPill(if (isPlaying) "⏸ 暂停" else "▶ 播放") {
                        if (isPlaying) controller.pause() else controller.resume()
                    }
                    GlassPill("10秒 ⏩") { controller.seekBy(10_000) }
                    // 倍速循环 1.0→1.25→1.5→0.75（CRS-006；观看时长按实际区间累计不受影响）
                    GlassPill("⏩ x${speed}") { controller.cycleSpeed() }
                    if (nowPlaying.audioTracks.size > 1) {
                        GlassPill("🎧 声音") { trackDialog = "audio" }
                    }
                    if (nowPlaying.subtitleTracks.isNotEmpty()) {
                        GlassPill(if (nowPlaying.selectedSubtitleTrackId != null) "💬 字幕 开" else "💬 字幕 关") {
                            trackDialog = "subtitle"
                        }
                    }
                    Spacer(Modifier.width(8.dp))
                    // AI 不可用时按钮转提示态（交互 §7.5；此前静默无反馈）
                    if (micGranted && aiReady) {
                        Box(
                            modifier = Modifier
                                .tvClickable { viewModel.startConversation() }
                                .background(KindoColors.accent, PillShape)
                                .padding(horizontal = 22.dp, vertical = 10.dp),
                        ) {
                            Text("🎤 问小熊", color = Color.White, fontSize = 19.sp,
                                 fontWeight = FontWeight.Bold)
                        }
                    } else {
                        Box(
                            modifier = Modifier
                                .tvClickable(enabled = false) { }
                                .background(Color(0x2EFFFFFF), PillShape)
                                .padding(horizontal = 22.dp, vertical = 10.dp),
                        ) {
                            Text("🎤 小熊在睡觉", color = Color(0xB3FFF6EA), fontSize = 19.sp,
                                 fontWeight = FontWeight.Bold)
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
            .tvClickable(onClick = onClick)
            .background(Color(0x2EFFFFFF), PillShape)
            .border(1.dp, Color(0x33FFFFFF), PillShape)
            .padding(horizontal = 20.dp, vertical = 10.dp),
    ) {
        Text(label, color = KindoColors.onDark, fontSize = 21.sp, fontWeight = FontWeight.Bold)
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
                            fontSize = 20.sp, fontWeight = FontWeight.Bold,
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
