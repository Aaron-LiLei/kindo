package org.kindo.pad.ui

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
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
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
import org.kindo.pad.AppViewModel
import org.kindo.pad.Screen
import org.kindo.pad.core.MediaPage
import org.kindo.pad.core.SeriesCollection
import org.kindo.pad.ui.theme.CardShape
import org.kindo.pad.ui.theme.KidBackground
import org.kindo.pad.ui.theme.KindoColors
import org.kindo.pad.ui.theme.PillShape

/**
 * 分类浏览（交互 §2 全部内容/分类 + 学龄前视觉 v2，与 TV 端同构）。两级结构：
 * 默认 = 系列墙（紧凑合集卡）；点入系列 = 该系列集网格（按季/集号）。
 * Pad 差异：网格列数随宽度自适应（GridCells.Adaptive，各种 Pad 尺寸同一份
 * 代码），类型 chips 行内滚动（窄竖屏不折行错位）。
 */
@Composable
fun BrowseScreen(viewModel: AppViewModel, screen: Screen.Browse, voice: VoiceEntry) {
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
                    TopBar(
                        title = screen.title, showBack = true, onBack = { viewModel.goBack() },
                        onMic = { voice.start(false) },
                    )
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
                        showBack = true, onBack = { viewModel.goBack() },
                        onMic = { voice.start(false) },
                    )
                    if (screen.type != null) TypeChips(viewModel, screen)
                    MediaGridContent(viewModel, screen, pages[key], useSeriesKey = false)
                }
                // 默认：系列墙
                else -> {
                    LaunchedEffect(Unit) { viewModel.loadCollections() }
                    TopBar(
                        title = "全部内容",
                        showBack = true, onBack = { viewModel.goBack() },
                        onMic = { voice.start(false) },
                    )
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
        // 首载失败：错误态 + 重试（审计 P1-2 同口径）
        page == null && key in errors -> LoadErrorBox {
            viewModel.retryBrowse(screen.type, screen.tag, screen.query, screen.seriesId)
        }
        page == null -> KidLoading()
        page.items.isEmpty() -> KidEmptyHint()
        else -> {
            // 返回本页恢复滚动位置（审计 P1-1 同口径）
            val gridState = rememberLazyGridState(
                initialFirstVisibleItemIndex = viewModel.gridScrollOf(key))
            LaunchedEffect(gridState.firstVisibleItemIndex) {
                viewModel.saveGridScroll(key, gridState.firstVisibleItemIndex)
            }
            LazyVerticalGrid(
                state = gridState,
                // 列数随宽度自适应：~200dp 一列（7" 竖屏 3 列 → 12" 横屏 7 列）
                columns = GridCells.Adaptive(200.dp),
                contentPadding = PaddingValues(horizontal = 32.dp, vertical = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(18.dp),
                verticalArrangement = Arrangement.spacedBy(20.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                items(page.items, key = { it.media_id }) { item ->
                    if (useSeriesKey && item.episode_no != null) {
                        // 系列集网格：大数字卡"第 N 集"（2026-08-27 产品决策，与 TV 同口径）
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
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
                if (page.next_cursor != null) {
                    item(span = { GridItemSpan(maxCurrentLineSpan) }) {
                        Box(
                            Modifier.fillMaxWidth().padding(20.dp),
                            contentAlignment = Alignment.Center,
                        ) {
                            if (key in moreFailed) {
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
            Text("🏠", fontSize = 56.sp)
            Spacer(Modifier.height(12.dp))
            Text("还空空的，等爸爸妈妈放进来哦",
                 color = KindoColors.textSecondary, fontSize = 22.sp)
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
        Text("😵", fontSize = 60.sp)
        Spacer(Modifier.height(10.dp))
        Text("呀，没加载出来", color = KindoColors.textPrimary,
             fontSize = 25.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(20.dp))
        KidButton(emoji = "↻", text = "再试一次", onClick = onRetry, fontSize = 22)
    }
}

@Composable
private fun SeriesWall(
    viewModel: AppViewModel,
    collections: org.kindo.pad.core.CollectionsResp?,
    token: String,
    hubBase: String,
    onOpen: (SeriesCollection) -> Unit,
) {
    when {
        collections == null -> KidLoading()
        collections.series.isEmpty() && collections.courses.isEmpty() -> KidEmptyHint()
        else -> {
            // 返回系列墙恢复滚动位置（审计 P1-1 同口径）
            val wallKey = "-|-|-|-"
            val gridState = rememberLazyGridState(
                initialFirstVisibleItemIndex = viewModel.gridScrollOf(wallKey))
            LaunchedEffect(gridState.firstVisibleItemIndex) {
                viewModel.saveGridScroll(wallKey, gridState.firstVisibleItemIndex)
            }
            LazyVerticalGrid(
                state = gridState,
                columns = GridCells.Adaptive(200.dp),
                contentPadding = PaddingValues(horizontal = 32.dp, vertical = 16.dp),
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
            .fillMaxWidth()
            .padClickable { onOpen(s) },
    ) {
        // 系列卡优先 Series poster（v0.3 MED-013），无实体图回退成员海报
        PosterImage(
            url = if (s.entity_poster && s.entity_id != null)
                "$hubBase/api/v1/entities/${s.entity_id}/poster"
            else s.cover_media_id?.let { "$hubBase/api/v1/media/$it/poster" },
            token = token,
            modifier = Modifier.fillMaxWidth().height(190.dp).clip(CardShape),
        )
        Spacer(Modifier.height(8.dp))
        Text(
            s.title,
            color = KindoColors.textPrimary,
            fontSize = 20.sp,
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

/** 类型筛选 chips（顶栏下方独占一行，与 TV 同口径；系列=回系列墙）。
 *  按库内实际内容派生（空类型不显示，与 Admin 同口径 2026-08-25）。
 *  Pad：LazyRow 行内滚动——窄竖屏放不下时滑动可选，不折行不挤压。
 *  选中=橙色药丸白字，未选中=白卡棕字——糖果感、无灰色描边。 */
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
    LazyRow(
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        contentPadding = PaddingValues(horizontal = 32.dp, vertical = 6.dp),
    ) {
        items(filters) { f ->
            val selected = screen.type == f.type
            Box(
                modifier = Modifier
                    .padClickable {
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
                    .padding(horizontal = 24.dp, vertical = 10.dp),
            ) {
                Text(f.label,
                     color = if (selected) androidx.compose.ui.graphics.Color.White
                             else KindoColors.textPrimary,
                     fontSize = 19.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}
