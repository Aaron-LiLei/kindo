package org.kindo.tv.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.kindo.tv.AppViewModel
import org.kindo.tv.core.MediaDetail
import org.kindo.tv.ui.theme.KidBackground
import org.kindo.tv.ui.theme.KindoColors

/** 内容详情（交互 §4.3 + 学龄前视觉 v2）：封面 + 元数据 + 播放动作 +
 *  季/集网格 + AI 入口。简介给家长念给孩子听，保留但视觉降级。 */
@Composable
fun DetailScreen(viewModel: AppViewModel, screen: org.kindo.tv.Screen.Detail) {
    val detail by viewModel.detail.collectAsState()
    val deny by viewModel.denyMessage.collectAsState()

    // 返回到本页时共享 _detail 可能已被其他集覆盖，兜底重载（否则永久转圈）
    androidx.compose.runtime.LaunchedEffect(screen.mediaId) {
        viewModel.ensureDetail(screen.mediaId)
    }
    val detailError by viewModel.detailError.collectAsState()
    Box(modifier = Modifier.fillMaxSize()) {
        KidBackground()
        Column(modifier = Modifier.fillMaxSize()) {
            TopBar(
                title = detail?.takeIf { it.media_id == screen.mediaId }?.title ?: "详情",
            )
            when (val d = detail?.takeIf { it.media_id == screen.mediaId }) {
                null -> if (detailError) {
                    // 加载失败错误态 + 重试（审计 P1-2）
                    Column(
                        modifier = Modifier.fillMaxSize(),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.Center,
                    ) {
                        Text("😵", fontSize = 64.sp)
                        Spacer(Modifier.height(10.dp))
                        Text("呀，没加载出来", color = KindoColors.textPrimary,
                             fontSize = 27.sp, fontWeight = FontWeight.Bold)
                        Spacer(Modifier.height(20.dp))
                        KidButton(emoji = "↻", text = "再试一次",
                                  onClick = { viewModel.retryDetail(screen.mediaId) }, fontSize = 22)
                    }
                } else KidLoading()
                else -> DetailContent(viewModel, d)
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
private fun DetailContent(viewModel: AppViewModel, d: MediaDetail) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(horizontal = 56.dp, vertical = 36.dp),
        verticalArrangement = Arrangement.spacedBy(22.dp),
    ) {
        item {
            Row(modifier = Modifier.fillMaxWidth().height(340.dp)) {
                // 集级海报 > 系列实体海报（TMDB）> 默认
                val posterUrl = if (d.has_poster)
                    "${viewModel.hub.baseUrl}/api/v1/media/${d.media_id}/poster"
                else if (d.series_entity_poster == true && d.series_entity_id != null)
                    "${viewModel.hub.baseUrl}/api/v1/entities/${d.series_entity_id}/poster"
                else
                    "${viewModel.hub.baseUrl}/api/v1/media/${d.media_id}/poster"
                PosterImage(
                    url = posterUrl,
                    token = viewModel.hub.deviceToken,
                    modifier = Modifier.width(240.dp).height(340.dp),
                )
                Spacer(Modifier.width(40.dp))
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(d.title, color = KindoColors.textPrimary, fontSize = 40.sp,
                         fontWeight = FontWeight.Bold)
                    val meta = buildList {
                        d.age_band?.let { add(it) }
                        d.language?.let { add(it) }
                        (d.tags["themes"] ?: emptyList()).take(3).let { if (it.isNotEmpty()) addAll(it) }
                    }.joinToString(" · ")
                    if (meta.isNotEmpty()) {
                        Text(meta, color = KindoColors.textSecondary, fontSize = 21.sp)
                    }
                    // Canonical 简介（交互 §4.3：儿童端只展示，不显示来源与锁定）。
                    // 这是给家长念的——保留但视觉安静（小一号、浅色）
                    d.overview?.takeIf { it.isNotBlank() }?.let {
                        Text(
                            it,
                            color = KindoColors.textSecondary, fontSize = 17.sp,
                            lineHeight = 25.sp, maxLines = 3,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    Spacer(Modifier.height(8.dp))
                    val play = d.actions?.play
                    if (play?.allowed == false) {
                        // 维度化预检提示（交互 §7.3；按钮保持可点，由服务端权威判定
                        // 产生 deny 边界事件承接成长接力）
                        Text(
                            viewModel.deniedReasonText(play.reason_code, play.constraints),
                            color = KindoColors.accentDeep, fontSize = 19.sp,
                            fontWeight = FontWeight.Bold,
                        )
                    }
                    if (!d.playable) {
                        // §1.2 兼容矩阵外：明确提示（不转码；播放仍由服务端权威拒绝）
                        Text(
                            "⚠️ 这台电视放不了这个，我们换个内容吧",
                            color = KindoColors.accentDeep, fontSize = 19.sp,
                            fontWeight = FontWeight.Bold,
                        )
                    }
                    val isAudio = d.modality == "AUDIO"
                    Row(verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                        // 断点续播契约（v0.3）：详情断点唯一来源 watch.last_position_ms
                        val resume = d.watch?.last_position_ms ?: d.resume_position_ms
                        if (resume > 0) {
                            KidButton(
                                emoji = if (isAudio) "🎧" else "▶",
                                text = "从 ${resume / 60000}:${"%02d".format(resume % 60000 / 1000)} 继续",
                                onClick = { viewModel.playFromUi(d.media_id, resume) },
                                modifier = Modifier.height(80.dp),
                                fontSize = 26,
                            )
                            KidButton(
                                emoji = "↻",
                                text = if (isAudio) "从头听" else "从头看",
                                onClick = { viewModel.playFromUi(d.media_id, 0) },
                                modifier = Modifier.height(80.dp),
                                container = KindoColors.kidBlue,
                                fontSize = 24,
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
                                modifier = Modifier.height(80.dp),
                                fontSize = 28,
                            )
                        }
                    }
                    Spacer(Modifier.height(6.dp))
                    // AI 不可用时降级为提示态（交互 §7.5：不静默、不伪装可点）
                    val caps by viewModel.capabilities.collectAsState()
                    val aiReady = caps.capabilities.voice_available && caps.capabilities.ai_available
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier
                            .let { m ->
                                if (aiReady) m.tvClickable { viewModel.startConversation() } else m
                            }
                            .background(KindoColors.surface, RoundedCornerShape(999.dp))
                            .border(2.dp, KindoColors.outline, RoundedCornerShape(999.dp))
                            .padding(horizontal = 22.dp, vertical = 12.dp),
                    ) {
                        Text("🎤", fontSize = 26.sp)
                        Spacer(Modifier.width(10.dp))
                        Text(
                            if (aiReady) "问 AI" else "AI 在睡觉",
                            color = if (aiReady) KindoColors.textPrimary else KindoColors.textSecondary,
                            fontSize = 20.sp, fontWeight = FontWeight.Bold,
                        )
                    }
                }
            }
        }

        // 系列 / 季 / 集（交互 §4.3）
        d.series?.let { series ->
            item {
                Text(
                    "📖 ${series.title} · 全部集数",
                    color = KindoColors.textPrimary, fontSize = 24.sp, fontWeight = FontWeight.Bold,
                )
            }
            item {
                // 18dp 间距与内容边距：聚焦卡 1.08x + 外发光画在边界外，
                // 行首/行尾需要留出空间，否则被 LazyRow 视口直线裁切
                LazyRow(
                    horizontalArrangement = Arrangement.spacedBy(18.dp),
                    contentPadding = PaddingValues(horizontal = 14.dp),
                ) {
                    itemsIndexed(series.episodes) { _, ep ->
                        EpisodeCard(
                            episodeNo = ep.episode_no ?: 1,
                            lastPositionMs = ep.last_position_ms,
                            completed = ep.completed,
                            onClick = { viewModel.openDetail(ep.media_id, inPlace = true) },
                            modifier = Modifier.width(260.dp),
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
                    color = KindoColors.textPrimary, fontSize = 24.sp, fontWeight = FontWeight.Bold,
                )
            }
            item {
                LazyRow(
                    horizontalArrangement = Arrangement.spacedBy(18.dp),
                    contentPadding = PaddingValues(horizontal = 14.dp),
                ) {
                    itemsIndexed(course.lessons) { _, lesson ->
                        Column(
                            modifier = Modifier
                                .size(width = 280.dp, height = 96.dp)
                                .tvClickable { viewModel.openDetail(lesson.media_id, inPlace = true) }
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
                            Text(lesson.title, color = KindoColors.textPrimary, fontSize = 19.sp,
                                 fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }
        }
    }
}

/** Policy 拒绝提示（无绕过按钮，POL-009 / 交互 §10）。
 *  v2：白卡 + 暖棕大字 + 糖果按钮——拒绝是温和的告知，不是系统报错。 */
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
                .padding(52.dp),
        ) {
            Text("🌙", fontSize = 56.sp)
            Spacer(Modifier.height(10.dp))
            Text(message, color = KindoColors.textPrimary, fontSize = 28.sp,
                 fontWeight = FontWeight.Bold,
                 textAlign = androidx.compose.ui.text.style.TextAlign.Center)
            Spacer(Modifier.height(28.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                // 网络/IO 类失败给"再试一次"；Policy 拒绝与解码失败无此按钮
                onRetry?.let {
                    KidButton(emoji = "↻", text = "再试一次", onClick = it, fontSize = 22)
                }
                KidButton(emoji = "👍", text = "知道了", onClick = onDismiss, fontSize = 22)
            }
        }
    }
}
