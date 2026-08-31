package org.kindo.pad.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.kindo.pad.AppViewModel
import org.kindo.pad.core.MediaDetail
import org.kindo.pad.ui.theme.KidBackground
import org.kindo.pad.ui.theme.KindoColors

/**
 * 内容详情（交互 §4.3 + 学龄前视觉 v2，与 TV 端同构）：封面 + 元数据 +
 * 播放动作 + 季/集网格 + 问小熊入口。简介给家长念给孩子听，保留但视觉降级。
 * Pad 差异：≥640dp 宽用"封面左/信息右"横向布局（横屏/大板），
 * 窄竖屏退化为上下堆叠——同一份内容两个排布，视觉语言一致。
 */
@Composable
fun DetailScreen(viewModel: AppViewModel, screen: org.kindo.pad.Screen.Detail, voice: VoiceEntry) {
    val detail by viewModel.detail.collectAsState()
    val deny by viewModel.denyMessage.collectAsState()

    // 返回到本页时共享 _detail 可能已被其他集覆盖，兜底重载（否则永久转圈）
    LaunchedEffect(screen.mediaId) {
        viewModel.ensureDetail(screen.mediaId)
    }
    val detailError by viewModel.detailError.collectAsState()
    Box(modifier = Modifier.fillMaxSize()) {
        KidBackground()
        Column(modifier = Modifier.fillMaxSize()) {
            TopBar(
                title = detail?.takeIf { it.media_id == screen.mediaId }?.title ?: "详情",
                showBack = true, onBack = { viewModel.goBack() },
            )
            when (val d = detail?.takeIf { it.media_id == screen.mediaId }) {
                null -> if (detailError) {
                    // 加载失败错误态 + 重试（审计 P1-2 同口径）
                    Column(
                        modifier = Modifier.fillMaxSize(),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.Center,
                    ) {
                        Text("😵", fontSize = 60.sp)
                        Spacer(Modifier.height(10.dp))
                        Text("呀，没加载出来", color = KindoColors.textPrimary,
                             fontSize = 25.sp, fontWeight = FontWeight.Bold)
                        Spacer(Modifier.height(20.dp))
                        KidButton(emoji = "↻", text = "再试一次",
                                  onClick = { viewModel.retryDetail(screen.mediaId) }, fontSize = 22)
                    }
                } else KidLoading()
                else -> DetailContent(viewModel, d, voice)
            }
        }
        deny?.let { msg ->
            val retryable by viewModel.denyRetryable.collectAsState()
            DenyDialog(msg, { viewModel.dismissDeny() },
                if (retryable) ({ viewModel.retryLastPlay() }) else null)
        }
    }
}

@Composable
private fun DetailContent(viewModel: AppViewModel, d: MediaDetail, voice: VoiceEntry) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(horizontal = 40.dp, vertical = 24.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        item {
            BoxWithConstraints(Modifier.fillMaxWidth()) {
                if (maxWidth >= 640.dp) {
                    // 宽屏：封面左 / 信息右（与 TV 端同构的横向版式）
                    Row(modifier = Modifier.fillMaxWidth().height(360.dp)) {
                        PosterBlock(viewModel, d, Modifier.width(260.dp).height(360.dp))
                        Spacer(Modifier.width(32.dp))
                        Column(Modifier.weight(1f)) {
                            InfoBlock(viewModel, d, voice)
                        }
                    }
                } else {
                    // 窄竖屏：封面在上 / 信息在下
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        PosterBlock(viewModel, d, Modifier.width(230.dp).height(320.dp))
                        Spacer(Modifier.height(18.dp))
                        Column(Modifier.fillMaxWidth()) {
                            InfoBlock(viewModel, d, voice)
                        }
                    }
                }
            }
        }

        // 系列 / 季 / 集（交互 §4.3）
        d.series?.let { series ->
            item {
                Text(
                    "📖 ${series.title} · 全部集数",
                    color = KindoColors.textPrimary, fontSize = 22.sp, fontWeight = FontWeight.Bold,
                )
            }
            item {
                LazyRow(
                    horizontalArrangement = Arrangement.spacedBy(16.dp),
                    contentPadding = PaddingValues(horizontal = 4.dp),
                ) {
                    itemsIndexed(series.episodes) { _, ep ->
                        EpisodeCard(
                            episodeNo = ep.episode_no ?: 1,
                            lastPositionMs = ep.last_position_ms,
                            completed = ep.completed,
                            onClick = { viewModel.openDetail(ep.media_id, inPlace = true) },
                            modifier = Modifier.width(220.dp),
                        )
                    }
                }
            }
        }

        // 课程 / 章节 / 课时
        d.course?.let { course ->
            item {
                Text(
                    "✏️ ${course.title} · 课时",
                    color = KindoColors.textPrimary, fontSize = 22.sp, fontWeight = FontWeight.Bold,
                )
            }
            item {
                LazyRow(
                    horizontalArrangement = Arrangement.spacedBy(16.dp),
                    contentPadding = PaddingValues(horizontal = 4.dp),
                ) {
                    itemsIndexed(course.lessons) { _, lesson ->
                        Column(
                            modifier = Modifier
                                .size(width = 260.dp, height = 92.dp)
                                .padClickable { viewModel.openDetail(lesson.media_id, inPlace = true) }
                                .background(
                                    if (lesson.completed) KindoColors.success.copy(alpha = 0.20f)
                                    else KindoColors.surface,
                                    RoundedCornerShape(18.dp),
                                )
                                .border(2.dp, KindoColors.outline, RoundedCornerShape(18.dp))
                                .padding(16.dp),
                        ) {
                            Text("✏️ 第 ${lesson.lesson_no} 课", color = KindoColors.accentDeep,
                                 fontSize = 15.sp, fontWeight = FontWeight.Bold)
                            Text(lesson.title, color = KindoColors.textPrimary, fontSize = 18.sp,
                                 fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun PosterBlock(viewModel: AppViewModel, d: MediaDetail, modifier: Modifier) {
    // 集级海报 > 系列实体海报（TMDB）> 默认
    val posterUrl = if (d.has_poster)
        "${viewModel.hub.baseUrl}/api/v1/media/${d.media_id}/poster"
    else if (d.series_entity_poster == true && d.series_entity_id != null)
        "${viewModel.hub.baseUrl}/api/v1/entities/${d.series_entity_id}/poster"
    else
        "${viewModel.hub.baseUrl}/api/v1/media/${d.media_id}/poster"
    Box(modifier) {
        PosterImage(
            url = posterUrl,
            token = viewModel.hub.deviceToken,
            modifier = Modifier.fillMaxSize(),
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun InfoBlock(viewModel: AppViewModel, d: MediaDetail, voice: VoiceEntry) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
        Text(d.title, color = KindoColors.textPrimary, fontSize = 34.sp,
             fontWeight = FontWeight.Bold)
        val meta = buildList {
            d.age_band?.let { add(it) }
            d.language?.let { add(it) }
            (d.tags["themes"] ?: emptyList()).take(3).let { if (it.isNotEmpty()) addAll(it) }
        }.joinToString(" · ")
        if (meta.isNotEmpty()) {
            Text(meta, color = KindoColors.textSecondary, fontSize = 19.sp)
        }
        // Canonical 简介（交互 §4.3：儿童端只展示，不显示来源与锁定）。
        // 这是给家长念的——保留但视觉安静（小一号、浅色）
        d.overview?.takeIf { it.isNotBlank() }?.let {
            Text(
                it,
                color = KindoColors.textSecondary, fontSize = 16.sp,
                lineHeight = 23.sp, maxLines = 3,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Spacer(Modifier.height(4.dp))
        val play = d.actions?.play
        if (play?.allowed == false) {
            // 维度化预检提示（交互 §7.3；按钮保持可点，由服务端权威判定
            // 产生 deny 边界事件承接成长接力）
            Text(
                viewModel.deniedReasonText(play.reason_code, play.constraints),
                color = KindoColors.accentDeep, fontSize = 18.sp,
                fontWeight = FontWeight.Bold,
            )
        }
        if (!d.playable) {
            // §1.2 兼容矩阵外：明确提示（不转码；播放仍由服务端权威拒绝）
            Text(
                "⚠️ 这台设备放不了这个，我们换个内容吧",
                color = KindoColors.accentDeep, fontSize = 18.sp,
                fontWeight = FontWeight.Bold,
            )
        }
        val isAudio = d.modality == "AUDIO"
        // FlowRow：窄竖屏下两个大按钮放不下时自动换行（不挤压不溢出）
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(18.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            // 断点续播契约（v0.3）：详情断点唯一来源 watch.last_position_ms
            val resume = d.watch?.last_position_ms ?: d.resume_position_ms
            if (resume > 0) {
                KidButton(
                    emoji = if (isAudio) "🎧" else "▶",
                    text = "从 ${resume / 60000}:${"%02d".format(resume % 60000 / 1000)} 继续",
                    onClick = { viewModel.playFromUi(d.media_id, resume) },
                    fontSize = 24,
                )
                KidButton(
                    emoji = "↻",
                    text = if (isAudio) "从头听" else "从头看",
                    onClick = { viewModel.playFromUi(d.media_id, 0) },
                    container = KindoColors.kidBlue,
                    fontSize = 22,
                )
            } else {
                KidButton(
                    emoji = if (isAudio) "🎧" else "▶",
                    text = when {
                        isAudio -> "听这个"
                        d.series?.episodes?.isNotEmpty() == true ->
                            "看第 ${d.series.episode_no ?: 1} 集"
                        else -> "看这个"
                    },
                    onClick = { viewModel.playFromUi(d.media_id, 0) },
                    fontSize = 26,
                )
            }
        }
        Spacer(Modifier.height(2.dp))
        // AI 不可用时降级为提示态（交互 §7.5：不静默、不伪装可点）
        val caps by viewModel.capabilities.collectAsState()
        val aiReady = caps.capabilities.voice_available && caps.capabilities.ai_available
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier
                .let { m ->
                    if (aiReady) m.padClickable { voice.start(false) } else m
                }
                .background(KindoColors.surface, RoundedCornerShape(999.dp))
                .border(2.dp, KindoColors.outline, RoundedCornerShape(999.dp))
                .padding(horizontal = 22.dp, vertical = 12.dp),
        ) {
            Text("🎤", fontSize = 24.sp)
            Spacer(Modifier.width(10.dp))
            Text(
                if (aiReady) "问小熊" else "小熊在睡觉",
                color = if (aiReady) KindoColors.textPrimary else KindoColors.textSecondary,
                fontSize = 19.sp, fontWeight = FontWeight.Bold,
            )
        }
    }
}

/** Policy 拒绝提示（无绕过按钮，POL-009 / 交互 §10，与 TV 同一口径）。
 *  白卡 + 暖棕大字 + 糖果按钮——拒绝是温和的告知，不是系统报错。 */
@Composable
fun DenyDialog(message: String, onDismiss: () -> Unit, onRetry: (() -> Unit)? = null) {
    Box(
        modifier = Modifier.fillMaxSize().background(Color(0x59000000)),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier
                .background(KindoColors.surface, RoundedCornerShape(32.dp))
                .padding(44.dp),
        ) {
            Text("🌙", fontSize = 52.sp)
            Spacer(Modifier.height(10.dp))
            Text(message, color = KindoColors.textPrimary, fontSize = 25.sp,
                 fontWeight = FontWeight.Bold,
                 textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                 modifier = Modifier.padding(horizontal = 12.dp))
            Spacer(Modifier.height(26.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                // 网络/IO 类失败给"再试一次"；Policy 拒绝与解码失败无此按钮
                onRetry?.let {
                    KidButton(emoji = "↻", text = "再试一次", onClick = it, fontSize = 21)
                }
                KidButton(emoji = "👍", text = "知道了", onClick = onDismiss, fontSize = 21)
            }
        }
    }
}
