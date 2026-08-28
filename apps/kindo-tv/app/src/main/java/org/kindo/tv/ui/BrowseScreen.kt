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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.kindo.tv.AppViewModel
import org.kindo.tv.Screen
import org.kindo.tv.core.MediaPage
import org.kindo.tv.core.SeriesCollection
import org.kindo.tv.ui.theme.CardShape
import org.kindo.tv.ui.theme.KidBackground
import org.kindo.tv.ui.theme.KindoColors
import org.kindo.tv.ui.theme.PillShape

/**
 * 分类浏览（交互 §2 全部内容/分类 + 学龄前视觉 v2）。两级结构：
 * 默认 = 系列墙（紧凑合集卡）；点入系列 = 该系列集网格（按季/集号）。
 * 所有子页都有顶栏返回（交互 §3 Back 规则 + 鼠标/触屏可用性）。
 */
@Composable
fun BrowseScreen(viewModel: AppViewModel, screen: Screen.Browse) {
    val pages by viewModel.browsePages.collectAsState()
    val collections by viewModel.collections.collectAsState()

    Box(Modifier.fillMaxSize()) {
        KidBackground()
        Column(modifier = Modifier.fillMaxSize()) {
            when {
                // 系列内集网格
                screen.seriesId != null -> {
                    val key = viewModel.browseKey(null, null, null, screen.seriesId)
                    val page = pages[key]
                    LaunchedEffect(screen) { viewModel.loadBrowse(null, null, null, screen.seriesId) }
                    TopBar(title = screen.title, onMic = { viewModel.startConversation() })
                    MediaGridContent(viewModel, screen, page, useSeriesKey = true)
                }
                // 平铺（类型 / 主题 / 检索）
                screen.type != null || screen.tag != null || screen.query != null -> {
                    LaunchedEffect(screen) {
                        viewModel.loadBrowse(screen.type, screen.tag, screen.query)
                    }
                    val key = viewModel.browseKey(screen.type, screen.tag, screen.query)
                    TopBar(
                        title = screen.title,
                        onMic = { viewModel.startConversation() },
                    )
                    if (screen.type != null) TypeChips(viewModel, screen)
                    MediaGridContent(viewModel, screen, pages[key], useSeriesKey = false)
                }
                // 默认：系列墙
                else -> {
                    LaunchedEffect(Unit) { viewModel.loadCollections() }
                    TopBar(title = "全部内容", onMic = { viewModel.startConversation() })
                    TypeChips(viewModel, screen)
                    SeriesWall(
                        viewModel = viewModel,
                        collections = collections,
                        token = viewModel.hub.deviceToken,
                        hubBase = viewModel.hub.baseUrl,
                        onOpen = { s ->
                            s.series_id?.let {
                                viewModel.navigate(Screen.Browse(null, null, s.title, seriesId = it))
                            } ?: s.cover_media_id?.let { viewModel.openDetail(it) }
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun MediaGridContent(
    viewModel: AppViewModel,
    screen: Screen.Browse,
    page: MediaPage?,
    useSeriesKey: Boolean,
) {
    val key = viewModel.browseKey(screen.type, screen.tag, screen.query, screen.seriesId)
    val errors by viewModel.browseErrors.collectAsState()
    val moreFailed by viewModel.browseMoreFailed.collectAsState()
    when {
        // 首载失败：错误态 + 重试（不再无限转圈，审计 P1-2）
        page == null && key in errors -> LoadErrorBox {
            viewModel.retryBrowse(screen.type, screen.tag, screen.query, screen.seriesId)
        }
        page == null -> KidLoading()
        page.items.isEmpty() -> KidEmptyHint()
        else -> {
            // 返回本页恢复滚动位置（审计 P1-1）
            val gridState = rememberLazyGridState(
                initialFirstVisibleItemIndex = viewModel.gridScrollOf(key))
            LaunchedEffect(gridState.firstVisibleItemIndex) {
                viewModel.saveGridScroll(key, gridState.firstVisibleItemIndex)
            }
            LazyVerticalGrid(
                state = gridState,
                // 4 列大卡（2026-08-27 产品决策）：284dp 宽卡 + 16dp 间距
                // = 4×284+3×16 = 1184dp，1280dp 屏留 48dp 边距刚好
                columns = GridCells.Fixed(4),
                contentPadding = PaddingValues(horizontal = 48.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(18.dp),
                verticalArrangement = Arrangement.spacedBy(20.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                items(page.items, key = { it.media_id }) { item ->
                    if (useSeriesKey && item.episode_no != null) {
                        // 系列集网格：大数字卡"第 N 集"（2026-08-27 产品决策：
                        // 不展示完整文件名；episode_no 仅新 Hub 系列 listing 提供，
                        // 旧 Hub 回退 MediaCard）
                        EpisodeCard(
                            episodeNo = item.episode_no,
                            lastPositionMs = item.last_position_ms,
                            completed = item.completed,
                            onClick = { viewModel.openDetail(item.media_id) },
                        )
                    } else {
                        MediaCard(
                            item = item,
                            badge = null,
                            onClick = { viewModel.openDetail(item.media_id) },
                            tokenProvider = { viewModel.hub.deviceToken },
                            hubBase = viewModel.hub.baseUrl,
                        )
                    }
                }
                if (page.next_cursor != null) {
                    item(span = { GridItemSpan(4) }) {
                        Box(
                            Modifier.fillMaxWidth().padding(20.dp),
                            contentAlignment = Alignment.Center,
                        ) {
                            if (key in moreFailed) {
                                // 分页失败：重试按钮（审计 P1-2/P2-10）
                                KidButton(emoji = "↻", text = "再试一次",
                                    onClick = {
                                        viewModel.loadMoreBrowse(
                                            screen.type, screen.tag, screen.query, screen.seriesId)
                                    })
                            } else {
                                LaunchedEffect(page.items.size) {
                                    viewModel.loadMoreBrowse(
                                        screen.type, screen.tag, screen.query, screen.seriesId)
                                }
                                CircularProgressIndicator(color = KindoColors.accent)
                            }
                        }
                    }
                }
            }
        }
    }
}

/** 加载中：安静的橙色转圈（不配文字——学龄前等不起句子，转圈本身就够）。 */
@Composable
fun KidLoading() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        CircularProgressIndicator(color = KindoColors.accent, strokeWidth = 6.dp)
    }
}

/** 空库提示：emoji 领衔 + 一句短话。 */
@Composable
fun KidEmptyHint() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("🏠", fontSize = 60.sp)
            Spacer(Modifier.height(12.dp))
            Text("还空空的，等爸爸妈妈放进来哦",
                 color = KindoColors.textSecondary, fontSize = 24.sp)
        }
    }
}

/** 首载失败错误框（儿童可读 + 大重试按钮）。 */
@Composable
private fun LoadErrorBox(onRetry: () -> Unit) {
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
        KidButton(emoji = "↻", text = "再试一次", onClick = onRetry, fontSize = 22)
    }
}

@Composable
private fun SeriesWall(
    viewModel: AppViewModel,
    collections: org.kindo.tv.core.CollectionsResp?,
    token: String,
    hubBase: String,
    onOpen: (SeriesCollection) -> Unit,
) {
    when {
        collections == null -> KidLoading()
        collections.series.isEmpty() && collections.courses.isEmpty() -> KidEmptyHint()
        else -> {
            // 返回系列墙恢复滚动位置（审计 P1-1）
            val wallKey = "-|-|-|-"
            val gridState = rememberLazyGridState(
                initialFirstVisibleItemIndex = viewModel.gridScrollOf(wallKey))
            LaunchedEffect(gridState.firstVisibleItemIndex) {
                viewModel.saveGridScroll(wallKey, gridState.firstVisibleItemIndex)
            }
            LazyVerticalGrid(
                state = gridState,
                columns = GridCells.Fixed(4),
                contentPadding = PaddingValues(horizontal = 48.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(18.dp),
                verticalArrangement = Arrangement.spacedBy(20.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                items(collections.series, key = { "s-" + (it.series_id ?: it.title) }) { s ->
                    SeriesCard(s, token, hubBase, onOpen)
                }
                items(collections.courses, key = { "c-" + (it.course_id ?: it.title) }) { c ->
                    SeriesCard(c, token, hubBase, onOpen)
                }
            }
        }
    }
}

@Composable
private fun SeriesCard(
    s: SeriesCollection,
    token: String,
    hubBase: String,
    onOpen: (SeriesCollection) -> Unit,
) {
    Column(
        modifier = Modifier
            .width(280.dp)
            .tvClickable { onOpen(s) },
    ) {
        // 系列卡优先 Series poster（v0.3 MED-013），无实体图回退成员海报
        PosterImage(
            url = if (s.entity_poster && s.entity_id != null)
                "$hubBase/api/v1/entities/${s.entity_id}/poster"
            else s.cover_media_id?.let { "$hubBase/api/v1/media/$it/poster" },
            token = token,
            modifier = Modifier.fillMaxWidth().height(200.dp).clip(CardShape),
        )
        Spacer(Modifier.height(8.dp))
        Text(
            s.title,
            color = KindoColors.textPrimary,
            fontSize = 21.sp,
            modifier = Modifier.padding(horizontal = 8.dp),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            fontWeight = FontWeight.Bold,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.padding(horizontal = 8.dp)) {
            Text("🎬 ${s.count} 集", color = KindoColors.textSecondary, fontSize = 15.sp,
                 fontWeight = FontWeight.Bold)
            s.age_band?.let {
                Text("· ${it}岁", color = KindoColors.textSecondary, fontSize = 15.sp)
            }
        }
    }
}

private data class TypeFilter(val label: String, val type: String?)

/** 类型筛选 chips（顶栏下方独占一行；系列=回系列墙）。
 *  按库内实际内容派生（空类型不显示，与 Admin 同口径 2026-08-25）；
 *  不进顶栏——顶栏塞多个 chips 会超出 1280dp 屏宽导致末位折行错位。
 *  v2：选中=橙色药丸白字，未选中=白卡棕字——糖果感、无灰色描边。 */
@Composable
private fun TypeChips(viewModel: AppViewModel, screen: Screen.Browse) {
    val collections by viewModel.collections.collectAsState()
    // 库内容决定的类型（按数量降序），加"系列"回墙
    val typeCounts = collections?.type_counts ?: emptyMap()
    val filters = buildList {
        add(TypeFilter("🏠 系列", null))
        typeCounts.entries
            .sortedByDescending { it.value }
            .forEach { (t, _) ->
                TYPE_LABELS[t]?.let { label ->
                    add(TypeFilter("${TYPE_EMOJIS[t] ?: "✨"} $label", t))
                }
            }
    }
    Row(
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        modifier = Modifier.padding(start = 40.dp, top = 2.dp, bottom = 12.dp),
    ) {
        filters.forEach { f ->
            val selected = screen.type == f.type
            Box(
                modifier = Modifier
                    .tvClickable {
                        // 筛选是原地切换，不压返回栈（否则逐个筛选逐层回退）
                        viewModel.replace(
                            if (f.type == null) Screen.Browse(null, null, "全部内容")
                            else Screen.Browse(f.type, null, f.label)
                        )
                    }
                    .background(
                        if (selected) KindoColors.accent else KindoColors.surface,
                        PillShape,
                    )
                    .border(
                        2.dp,
                        if (selected) KindoColors.accent else KindoColors.outline,
                        PillShape,
                    )
                    .padding(horizontal = 26.dp, vertical = 10.dp),
            ) {
                Text(f.label,
                     color = if (selected) androidx.compose.ui.graphics.Color.White
                             else KindoColors.textPrimary,
                     fontSize = 20.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}
