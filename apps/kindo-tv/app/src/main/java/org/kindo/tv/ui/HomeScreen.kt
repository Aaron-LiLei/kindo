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
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.kindo.tv.AppViewModel
import org.kindo.tv.Screen
import org.kindo.tv.core.LearningItem
import org.kindo.tv.core.MediaSummary
import org.kindo.tv.core.SeriesRef
import org.kindo.tv.ui.theme.CardShape
import org.kindo.tv.ui.theme.KidBackground
import org.kindo.tv.ui.theme.KindoColors
import org.kindo.tv.ui.theme.PillShape

/**
 * 儿童首页（交互 §4.2 + 学龄前视觉 v2）：语音入口是第一主角，
 * 继续观看 / 继续收听 / 继续学习 / 可探索主题 / 最近常看。
 * 没有数据的行不保留空模块；文案一律短句 + emoji 领衔（不识字也看得懂）。
 */
@Composable
fun HomeScreen(viewModel: AppViewModel, micGranted: Boolean) {
    val home by viewModel.home.collectAsState()
    val caps by viewModel.capabilities.collectAsState()
    val homeError by viewModel.homeError.collectAsState()

    // 返回首页恢复滚动位置（审计 P1-1）
    val listState = androidx.compose.foundation.lazy.rememberLazyListState(
        initialFirstVisibleItemIndex = viewModel.gridScrollOf("home"))
    androidx.compose.runtime.LaunchedEffect(listState.firstVisibleItemIndex) {
        viewModel.saveGridScroll("home", listState.firstVisibleItemIndex)
    }
    Box(Modifier.fillMaxSize()) {
        KidBackground()
        LazyColumn(
            state = listState,
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(horizontal = 56.dp, vertical = 40.dp),
            verticalArrangement = Arrangement.spacedBy(26.dp),
        ) {
            item {
                // 语音入口是首页首个可聚焦元素（交互 §4.2），但不阻止 D-pad 浏览。
                // 学龄前视觉 v2：超大药丸 + 笑脸 + 呼吸动画——孩子一眼知道"跟它说话"
                val voiceReady = caps.capabilities.voice_available && caps.capabilities.ai_available && micGranted
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .let { if (voiceReady) it.kidBreathing() else it }
                        .tvClickable(enabled = voiceReady) { viewModel.startConversation() }
                        .background(
                            if (voiceReady) KindoColors.accent else KindoColors.surface,
                            RoundedCornerShape(34.dp),
                        )
                        .border(
                            2.dp,
                            if (voiceReady) Color.Transparent else KindoColors.outline,
                            RoundedCornerShape(34.dp),
                        )
                        .padding(horizontal = 44.dp, vertical = 24.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("🎤", fontSize = 52.sp)
                        Spacer(Modifier.width(22.dp))
                        Text(
                            if (voiceReady) "和 AI 说话" else "AI 在睡觉",
                            color = if (voiceReady) Color.White else KindoColors.textSecondary,
                            fontSize = 36.sp, fontWeight = FontWeight.Bold,
                        )
                        if (voiceReady) {
                            Spacer(Modifier.width(10.dp))
                            Text("😊", fontSize = 34.sp)
                        }
                    }
                }
            }

            // "全部内容"紧邻语音按钮——不用语音的孩子一步 DOWN 即达（此前在最底 5 次 DOWN）
            item {
                Spacer(Modifier.height(6.dp))
                val hasThemes = home.explore_themes.isNotEmpty()
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    // 白卡 + 彩色大 emoji：图标的颜色承担"分区感"，不再是霓虹色块
                    QuickCard(
                        emoji = "🎬", text = "全部内容",
                        color = KindoColors.kidGreen,
                        arrow = true,
                        modifier = Modifier.weight(if (hasThemes) 1f else 1.55f),
                    ) { viewModel.navigate(Screen.Browse(null, null, "全部内容")) }
                    // 主题探索快捷（小卡，与全部内容并排；无主题时隐藏）
                    if (hasThemes) QuickCard(
                        emoji = "✨", text = "探索",
                        color = KindoColors.kidPurple,
                        modifier = Modifier.weight(0.55f),
                    ) {
                        val theme = home.explore_themes.firstOrNull() ?: return@QuickCard
                        viewModel.navigate(Screen.Browse(null, theme, theme))
                    }
                    // 文本找一找（P2）：不用语音的孩子可打字/选主题检索
                    QuickCard(
                        emoji = "🔍", text = "找一找",
                        color = KindoColors.kidBlue,
                        modifier = Modifier.weight(0.55f),
                    ) { viewModel.navigate(Screen.Search) }
                }
            }

            // 首载失败且无任何内容可显示：错误态 + 重试（此前静默只剩两个按钮）
            if (homeError && home.continue_watching.isEmpty() && home.continue_listening.isEmpty() &&
                home.continue_learning.isEmpty() && home.recent_series.isEmpty()
            ) {
                item {
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        modifier = Modifier.fillMaxWidth().padding(vertical = 30.dp),
                    ) {
                        Text("😵", fontSize = 64.sp)
                        Spacer(Modifier.height(10.dp))
                        Text("呀，没加载出来", color = KindoColors.textPrimary, fontSize = 26.sp,
                             fontWeight = FontWeight.Bold)
                        Spacer(Modifier.height(18.dp))
                        KidButton(emoji = "↻", text = "再试一次",
                                  onClick = { viewModel.retryHome() }, fontSize = 22)
                    }
                }
            }

            if (home.continue_watching.isNotEmpty()) {
                item { SectionTitle("▶", "继续观看") }
                item {
                    MediaRow(
                        items = home.continue_watching.map {
                            MediaSummary(media_id = it.media_id, title = it.title, has_poster = true)
                        },
                        badgeOf = { idx ->
                            val ms = home.continue_watching[idx].last_position_ms
                            if (ms >= 60_000) "▶ ${ms / 60000} 分钟" else "▶ 刚刚"
                        },
                        onClick = { viewModel.openDetail(it.media_id) },
                        tokenProvider = { viewModel.hub.deviceToken },
                        hubBase = viewModel.hub.baseUrl,
                    )
                }
            }

            // 继续收听（交互 §4.2：音频与视频分列，不混入"继续观看"）
            if (home.continue_listening.isNotEmpty()) {
                item { SectionTitle("🎧", "继续收听") }
                item {
                    MediaRow(
                        items = home.continue_listening.map {
                            MediaSummary(media_id = it.media_id, title = it.title, has_poster = true)
                        },
                        badgeOf = { idx ->
                            val ms = home.continue_listening[idx].last_position_ms
                            if (ms >= 60_000) "🎧 ${ms / 60000} 分钟" else "🎧 刚刚"
                        },
                        onClick = { viewModel.openDetail(it.media_id) },
                        tokenProvider = { viewModel.hub.deviceToken },
                        hubBase = viewModel.hub.baseUrl,
                    )
                }
            }

            if (home.continue_learning.isNotEmpty()) {
                item { SectionTitle("✏️", "继续学习") }
                item {
                    LearningRow(home.continue_learning) { viewModel.openDetail(it.media_id) }
                }
            }

            if (home.explore_themes.isNotEmpty()) {
                item { SectionTitle("✨", "想看什么？") }
                item {
                    val themeColors = listOf(
                        KindoColors.kidBlue, KindoColors.kidGreen, KindoColors.kidYellow,
                        KindoColors.kidPink, KindoColors.kidPurple, KindoColors.accent)
                    val themeEmojis = mapOf(
                        "海洋" to "🐬", "动物" to "🦁", "数字" to "🔢", "英语" to "🔤",
                        "工程" to "🚜", "汽车" to "🚗", "恐龙" to "🦕", "太空" to "🚀",
                        "音乐" to "🎵", "故事" to "📖")
                    LazyRow(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                        itemsIndexed(home.explore_themes) { i, theme ->
                            Box(
                                modifier = Modifier
                                    .size(width = 250.dp, height = 96.dp)
                                    .tvClickable { viewModel.navigate(Screen.Browse(null, theme, theme)) }
                                    .background(
                                        themeColors[i % themeColors.size].copy(alpha = 0.28f),
                                        RoundedCornerShape(22.dp),
                                    ),
                                contentAlignment = Alignment.Center,
                            ) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(themeEmojis[theme] ?: "✨", fontSize = 34.sp)
                                    Spacer(Modifier.width(12.dp))
                                    Text(theme, color = KindoColors.textPrimary, fontSize = 25.sp,
                                         fontWeight = FontWeight.Bold)
                                }
                            }
                        }
                    }
                }
            }

            item { SectionTitle("🧸", "最近常看") }
            if (home.recent_series.isEmpty()) {
                item {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("🏠", fontSize = 24.sp)
                        Spacer(Modifier.width(10.dp))
                        Text(
                            "还没有常看的节目，试试对 🎤 说话",
                            color = KindoColors.textSecondary, fontSize = 18.sp,
                        )
                    }
                }
            } else {
                item {
                    SeriesRow(
                        items = home.recent_series,
                        tokenProvider = { viewModel.hub.deviceToken },
                        hubBase = viewModel.hub.baseUrl,
                    ) { s ->
                        viewModel.navigate(Screen.Browse(null, null, s.title, seriesId = s.series_id))
                    }
                }
            }
        }
    }
}

/** 快捷入口卡：白底 + 彩色大 emoji + 短词（图标优先，§4.7）。 */
@Composable
private fun QuickCard(
    emoji: String,
    text: String,
    color: Color,
    modifier: Modifier = Modifier,
    arrow: Boolean = false,
    onClick: () -> Unit,
) {
    Box(
        modifier = modifier
            .height(76.dp)
            .tvClickable(onClick = onClick)
            .background(KindoColors.surface, RoundedCornerShape(22.dp))
            .border(2.dp, KindoColors.outline, RoundedCornerShape(22.dp))
            .padding(horizontal = 24.dp),
        contentAlignment = Alignment.CenterStart,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(emoji, fontSize = 34.sp)
            Spacer(Modifier.width(14.dp))
            Text(text, color = KindoColors.textPrimary, fontSize = 24.sp,
                 fontWeight = FontWeight.Bold)
            if (arrow) {
                Spacer(Modifier.weight(1f))
                Text("›", color = color, fontSize = 40.sp, fontWeight = FontWeight.Black)
            }
        }
    }
}

@Composable
private fun SectionTitle(icon: String, text: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(icon, fontSize = 27.sp)
        Spacer(Modifier.width(10.dp))
        Text(text, color = KindoColors.textPrimary, fontSize = 29.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
fun MediaRow(
    items: List<MediaSummary>,
    badgeOf: (Int) -> String? = { null },
    onClick: (MediaSummary) -> Unit,
    tokenProvider: () -> String,
    hubBase: String,
) {
    LazyRow(horizontalArrangement = Arrangement.spacedBy(18.dp)) {
        itemsIndexed(items) { idx, item ->
            MediaCard(item, badgeOf(idx), onClick, tokenProvider, hubBase)
        }
    }
}

@Composable
fun MediaCard(
    item: MediaSummary,
    badge: String?,
    onClick: (MediaSummary) -> Unit,
    tokenProvider: () -> String,
    hubBase: String,
) {
    Column(
        modifier = Modifier
            .width(280.dp)
            .tvClickable { onClick(item) },
    ) {
        Box {
            PosterImage(
                url = "$hubBase/api/v1/media/${item.media_id}/poster",
                token = tokenProvider(),
                modifier = Modifier.fillMaxWidth().height(200.dp).clip(CardShape),
            )
            badge?.let {
                Text(
                    it,
                    color = Color.White,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier
                        .align(Alignment.BottomStart)
                        .background(KindoColors.accent, PillShape)
                        .padding(horizontal = 10.dp, vertical = 3.dp),
                )
            }
        }
        Spacer(Modifier.height(8.dp))
        Text(
            item.title,
            color = KindoColors.textPrimary,
            fontSize = 20.sp,
            fontWeight = FontWeight.Bold,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(horizontal = 8.dp),
        )
    }
}

@Composable
private fun LearningRow(items: List<LearningItem>, onClick: (LearningItem) -> Unit) {
    LazyRow(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
        items(items) { it ->
            Column(
                modifier = Modifier
                    .width(300.dp)
                    .tvClickable { onClick(it) }
                    .background(KindoColors.surface, RoundedCornerShape(18.dp))
                    .border(2.dp, KindoColors.outline, RoundedCornerShape(18.dp))
                    .padding(20.dp),
            ) {
                Text("✏️ " + it.course_title, color = KindoColors.textSecondary, fontSize = 15.sp)
                Spacer(Modifier.height(6.dp))
                Text(it.title, color = KindoColors.textPrimary, fontSize = 20.sp,
                     fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (it.position_ms > 0) {
                    Spacer(Modifier.height(4.dp))
                    Text("学到 ${it.position_ms / 60000} 分钟", color = KindoColors.accentDeep,
                         fontSize = 15.sp, fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}

@Composable
private fun SeriesRow(
    items: List<SeriesRef>,
    tokenProvider: () -> String,
    hubBase: String,
    onOpen: (SeriesRef) -> Unit,
) {
    // 与全部内容的系列墙同规格海报卡（无海报内容叠标题区分）
    LazyRow(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
        items(items) { s ->
            Column(
                modifier = Modifier
                    .width(280.dp)
                    .tvClickable { onOpen(s) },
            ) {
                // 系列实体海报（TMDB）优先于集级媒体海报（MED-013）
                PosterImage(
                    url = if (s.entity_poster && s.entity_id != null)
                        "$hubBase/api/v1/entities/${s.entity_id}/poster"
                    else s.cover_media_id?.let { "$hubBase/api/v1/media/$it/poster" },
                    token = tokenProvider(),
                    modifier = Modifier.fillMaxWidth().height(200.dp).clip(CardShape),
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    s.title,
                    color = KindoColors.textPrimary,
                    fontSize = 20.sp,
                    fontWeight = FontWeight.Bold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(horizontal = 8.dp),
                )
                if (s.count > 0) {
                    Text("🎬 ${s.count} 集", color = KindoColors.textSecondary, fontSize = 14.sp,
                         fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}
