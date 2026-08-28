package org.kindo.tv.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.kindo.tv.AppViewModel
import org.kindo.tv.Screen
import org.kindo.tv.ui.theme.KidBackground
import org.kindo.tv.ui.theme.KindoColors

/**
 * 找一找（学龄前视觉 v2 修订，2026-08-27 设计审视（"还让小朋友通过遥控器
 * 输入文字？"后定版）：孩子的"找"只有两条真实路径——**说**（语音）和
 * **看图点**（类型/主题图标）。文本检索（P2 轮产物，基线文档从未记载）
 * 从 TV 端移除：3~6 岁不识字更不会拼音，D-pad 键盘格子输入远超学龄前
 * 能力；家长侧找片走 Web Admin。
 *
 * 结构：语音主角卡（说）→ 按类型挑（服务端 type_counts 派生）→
 * 按主题挑（服务端 explore_themes 派生）。无 TextField、无 IME。
 */
@Composable
fun SearchScreen(viewModel: AppViewModel) {
    val home by viewModel.home.collectAsState()
    val collections by viewModel.collections.collectAsState()

    // 类型/主题一律服务端派生（§4.7 分类选项同源派生，与 Admin/Browse 同口径）
    LaunchedEffect(Unit) { viewModel.loadCollections() }

    Box(Modifier.fillMaxSize()) {
        KidBackground()
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState()),
        ) {
            TopBar(
                title = "找一找",
                onMic = { viewModel.startConversation() },
            )
            Column(modifier = Modifier.padding(horizontal = 56.dp)) {
                // 主角：说给 Kindo 听（找的第一路径就是开口）
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .kidBreathing()
                        .tvClickable { viewModel.startConversation() }
                        .background(KindoColors.accent, RoundedCornerShape(28.dp))
                        .padding(horizontal = 36.dp, vertical = 22.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("🎤", fontSize = 48.sp)
                        Spacer(Modifier.width(18.dp))
                        Text("说出你想看的", color = Color.White, fontSize = 32.sp,
                             fontWeight = FontWeight.Bold)
                    }
                }
                Spacer(Modifier.height(10.dp))
                Text(
                    "比如“我想看汪汪队”",
                    color = KindoColors.textSecondary, fontSize = 17.sp,
                    modifier = Modifier.align(Alignment.CenterHorizontally),
                )

                // 按类型挑：大图标卡（白卡 + 彩色大 emoji，看图即懂）
                val typeCounts = collections?.type_counts ?: emptyMap()
                if (typeCounts.isNotEmpty()) {
                    Spacer(Modifier.height(34.dp))
                    SectionIconTitle("🧩", "按类型挑")
                    Spacer(Modifier.height(14.dp))
                    val typeColors = mapOf(
                        "episode" to KindoColors.kidBlue,
                        "movie" to KindoColors.kidPink,
                        "song" to KindoColors.kidGreen,
                        "story" to KindoColors.kidPurple,
                        "lesson" to KindoColors.kidYellow,
                    )
                    IconCardFlow(
                        entries = typeCounts.entries
                            .sortedByDescending { it.value }
                            .mapNotNull { (t, _) ->
                                TYPE_LABELS[t]?.let { lbl -> Triple(t, TYPE_EMOJIS[t] ?: "✨", lbl) }
                            },
                        cardColor = { idx -> typeColors.values.toList()[idx % typeColors.size] },
                    ) { (t, _, lbl) ->
                        viewModel.navigate(Screen.Browse(t, null, lbl))
                    }
                }

                // 按主题挑：大 emoji pastel 卡（与首页主题行同构）
                if (home.explore_themes.isNotEmpty()) {
                    Spacer(Modifier.height(34.dp))
                    SectionIconTitle("✨", "按主题挑")
                    Spacer(Modifier.height(14.dp))
                    val themeColors = listOf(
                        KindoColors.kidBlue, KindoColors.kidGreen, KindoColors.kidYellow,
                        KindoColors.kidPink, KindoColors.kidPurple, KindoColors.accent)
                    val themeEmojis = mapOf(
                        "海洋" to "🐬", "动物" to "🦁", "数字" to "🔢", "英语" to "🔤",
                        "工程" to "🚜", "汽车" to "🚗", "恐龙" to "🦕", "太空" to "🚀",
                        "音乐" to "🎵", "故事" to "📖")
                    IconCardFlow(
                        entries = home.explore_themes.map {
                            Triple(it, themeEmojis[it] ?: "✨", it)
                        },
                        pastel = true,
                        cardColor = { idx -> themeColors[idx % themeColors.size] },
                    ) { (theme, _, _) ->
                        viewModel.navigate(Screen.Browse(null, theme, theme))
                    }
                }
                Spacer(Modifier.height(40.dp))
            }
        }
    }
}

@Composable
private fun SectionIconTitle(icon: String, text: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(icon, fontSize = 24.sp)
        Spacer(Modifier.width(10.dp))
        Text(text, color = KindoColors.textPrimary, fontSize = 26.sp, fontWeight = FontWeight.Bold)
    }
}

/**
 * 图标大卡流（每行 4 张：4×240dp + 3×14dp 间距 ≈ 1002dp < 1280dp 预算）。
 * 类型卡=白底描边 + 彩色大 emoji；主题卡=pastel 底（与首页主题行同构）。
 */
@Composable
private fun IconCardFlow(
    entries: List<Triple<String, String, String>>,
    pastel: Boolean = false,
    cardColor: (Int) -> Color,
    onPick: (Triple<String, String, String>) -> Unit,
) {
    var rowStart = 0
    val rows = mutableListOf<List<Int>>()
    while (rowStart < entries.size) {
        rows.add((rowStart until minOf(rowStart + 4, entries.size)).toList())
        rowStart += 4
    }
    Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
        rows.forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                row.forEach { idx ->
                    val entry = entries[idx]
                    val color = cardColor(idx)
                    Box(
                        modifier = Modifier
                            .size(width = 240.dp, height = 104.dp)
                            .tvClickable { onPick(entry) }
                            .background(
                                if (pastel) color.copy(alpha = 0.28f) else KindoColors.surface,
                                RoundedCornerShape(20.dp),
                            )
                            .border(
                                2.dp,
                                if (pastel) Color.Transparent else KindoColors.outline,
                                RoundedCornerShape(20.dp),
                            ),
                        contentAlignment = Alignment.Center,
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(entry.second, fontSize = 40.sp)
                            Spacer(Modifier.width(12.dp))
                            Text(entry.third, color = KindoColors.textPrimary,
                                 fontSize = 24.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }
        }
    }
}
