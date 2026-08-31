package org.kindo.tv.ui

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.material3.Text
import androidx.compose.ui.composed
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.kindo.tv.ui.theme.KindoColors
import org.kindo.tv.ui.theme.PillShape

/**
 * TV 焦点视觉（§4.7 定版，学龄前必须一眼看到"现在选的是哪个"）：
 * 聚焦时 1.08x 缩放 + 5dp 焦点金描边 + 双层光晕边（外圈更亮的扩散感）。
 * 亮底上金色光晕依然醒目；描边外再加一圈暖棕柔光兜底对比度。
 */
private fun Modifier.kidFocusVisual(): Modifier = composed {
    var focused by remember { mutableStateOf(false) }
    val scale by androidx.compose.animation.core.animateFloatAsState(
        if (focused) 1.08f else 1f, label = "focusScale")
    val borderColor by animateColorAsState(
        if (focused) KindoColors.focusRing else Color.Transparent, label = "focusBorder")
    val outerColor by animateColorAsState(
        if (focused) KindoColors.focusRing.copy(alpha = 0.45f) else Color.Transparent,
        label = "focusOuter")
    val softShadow by animateColorAsState(
        if (focused) Color(0x33250F00) else Color.Transparent, label = "focusShadow")
    this
        .scale(scale)
        // shadow 默认 clip=elevation>0.dp：会把后面 drawBehind 画在边界外的
        // 金环/光晕裁掉——§4.7 焦点金环自视觉 v2 起实际从未渲染（UX 视觉
        // 审查 2026-08-31 定位：缩放生效但全 App 无一处金环），必须显式关裁切
        .shadow(10.dp, shape = RoundedCornerShape(24.dp), clip = false,
                ambientColor = softShadow, spotColor = softShadow)
        // 焦点环画在边界外侧：Modifier.border 向内绘制，5dp 金环 + 9dp 光晕
        // 会盖住卡片左缘，把标题第一个字压掉（2026-08-27 修复记录（"最前面的
        // 字被切割"）；外移后聚焦不再遮挡任何内容
        .outerStroke(9.dp, outerColor, 26.dp)
        .outerStroke(5.dp, borderColor, 22.dp)
        .onFocusChanged { focused = it.isFocused }
}

/** 贴边界外侧描边（不侵入内容；透明时零开销跳过）。 */
private fun Modifier.outerStroke(width: Dp, color: Color, corner: Dp): Modifier =
    drawBehind {
        if (color.alpha <= 0.01f) return@drawBehind
        val w = width.toPx()
        drawRoundRect(
            color = color,
            topLeft = Offset(-w / 2f, -w / 2f),
            size = Size(size.width + w, size.height + w),
            cornerRadius = CornerRadius(corner.toPx() + w / 2f),
            style = Stroke(width = w),
        )
    }

fun Modifier.tvFocusable(): Modifier = composed {
    kidFocusVisual().focusable()
}

/** 可点击 + 焦点视觉的组合（enabled=false 时不可点也不聚焦）。
 *  onClick 必须是最后一个参数——尾随 lambda 只绑定末位参数。 */
fun Modifier.tvClickable(enabled: Boolean = true, onClick: () -> Unit): Modifier = composed {
    val focusVisual = if (enabled) kidFocusVisual() else Modifier
    focusVisual.clickable(
        interactionSource = remember { MutableInteractionSource() },
        indication = null,
        enabled = enabled,
        onClick = onClick,
    )
}

/**
 * 学龄前主按钮（§4.7：图标优先 + 主操作巨大化）：emoji 图标 + 大字 +
 * 糖果药丸造型（填充色 + 白字 + 轻投影 + 文字描影）。不识字的孩子靠
 * emoji 辨认动作，文字只作辅佐且必须短。
 */
@Composable
fun KidButton(
    emoji: String,
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    container: Color = KindoColors.accent,
    fontSize: Int = 24,
) {
    Box(
        // tvClickable 必须在最外层：Modifier.shadow 默认 clip=elevation>0.dp，
        // 放在焦点视觉之外会把边界外的金环/光晕裁掉（UX 视觉审查 2026-08-31）
        modifier = modifier
            .tvClickable(onClick = onClick)
            .shadow(7.dp, shape = PillShape, ambientColor = Color(0x2E000000),
                    spotColor = Color(0x2E000000))
            .background(container, PillShape)
            .padding(horizontal = 32.dp, vertical = 18.dp),
        contentAlignment = Alignment.Center,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            androidx.compose.material3.Text(emoji, fontSize = (fontSize + 8).sp)
            Spacer(Modifier.width(12.dp))
            androidx.compose.material3.Text(
                text, color = Color.White, fontSize = fontSize.sp,
                fontWeight = FontWeight.Bold,
                // 亮色填充上的白字加淡描影，远距离更易读
                style = androidx.compose.ui.text.TextStyle(
                    shadow = Shadow(Color(0x40000000), offset = Offset(0f, 2f), blurRadius = 6f),
                ),
            )
        }
    }
}

/** 软按钮：白底药丸 + 棕字（次要动作——"不聊了/再试一次"这一级）。
 *  替代 Material3 灰色 OutlinedButton：灰色描边按钮是成人软件的视觉惯性。 */
@Composable
fun KidSoftButton(
    emoji: String,
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    fontSize: Int = 20,
) {
    Box(
        modifier = modifier
            .tvClickable(onClick = onClick)
            .background(KindoColors.surface, PillShape)
            .border(2.dp, KindoColors.outline, PillShape)
            .padding(horizontal = 26.dp, vertical = 12.dp),
        contentAlignment = Alignment.Center,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            androidx.compose.material3.Text(emoji, fontSize = (fontSize + 4).sp)
            Spacer(Modifier.width(8.dp))
            androidx.compose.material3.Text(
                text, color = KindoColors.textPrimary, fontSize = fontSize.sp,
                fontWeight = FontWeight.Bold,
            )
        }
    }
}

/** 大徽章：观看进度/集数等状态的图形化呈现（§4.7 进度图形化）。 */
@Composable
fun KidBadge(
    emoji: String,
    text: String,
    modifier: Modifier = Modifier,
    container: Color = KindoColors.accent,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = modifier
            .background(container, RoundedCornerShape(10.dp))
            .padding(horizontal = 10.dp, vertical = 4.dp),
    ) {
        androidx.compose.material3.Text(emoji, fontSize = 14.sp)
        Spacer(Modifier.width(4.dp))
        androidx.compose.material3.Text(
            text, color = Color(0xFF1A1010), fontSize = 14.sp,
            fontWeight = FontWeight.Bold,
        )
    }
}

/** 呼吸动画修饰（语音入口/正在播放的"活"感，§4.7 呼吸引导）。 */
fun Modifier.kidBreathing(): Modifier = composed {
    val transition = rememberInfiniteTransition(label = "breath")
    val scale by transition.animateFloat(
        initialValue = 1f, targetValue = 1.03f,
        animationSpec = infiniteRepeatable(tween(1200), RepeatMode.Reverse),
        label = "breathScale")
    scale(scale)
}

/** 顶栏：标题 + 右侧单一动作（2026-08-27 修订：移除屏上 ← 返回——
 *  返回一律走遥控器 BACK（TV-002，KindoApp BackHandler 链）。
 *  屏上 ← 的来历是 8-21 鼠标/模拟器测试场景补丁，对 D-pad 反而有害：
 *  角落里可聚焦的小箭头会截获孩子的焦点造成误触返回。
 *  筛选 chips 一律放顶栏下方独占行（BrowseScreen.TypeChips），
 *  塞顶栏会超出 1280dp 屏宽导致末位 chip 折行错位。 */
@Composable
fun TopBar(
    title: String,
    onMic: (() -> Unit)? = null,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 40.dp, vertical = 18.dp),
    ) {
        androidx.compose.material3.Text(
            title,
            color = KindoColors.textPrimary,
            fontSize = 30.sp,
            fontWeight = FontWeight.Bold,
        )
        Spacer(Modifier.weight(1f))
        if (onMic != null) {
            Box(
                modifier = Modifier
                    .tvClickable(onClick = onMic)
                    .shadow(5.dp, shape = PillShape, ambientColor = Color(0x28000000),
                            spotColor = Color(0x28000000))
                    .background(KindoColors.accent, PillShape)
                    .padding(horizontal = 24.dp, vertical = 12.dp),
            ) {
                androidx.compose.material3.Text(
                    "🎤 说话", color = Color.White,
                    fontSize = 20.sp, fontWeight = FontWeight.Bold,
                    style = androidx.compose.ui.text.TextStyle(
                        shadow = Shadow(Color(0x40000000), offset = Offset(0f, 2f), blurRadius = 6f),
                    ),
                )
            }
        }
    }
}

/** 类型 → 儿童端标签/emoji（与 Admin 一致的命名映射；Browse 筛选与
 *  找一找图标卡共用一份，防止两处漂移——§4.7 分类选项同源派生）。 */
internal val TYPE_LABELS = mapOf(
    "episode" to "动画",
    "movie" to "电影",
    "lesson" to "课程",
    "song" to "儿歌",
    "story" to "故事",
)
internal val TYPE_EMOJIS = mapOf(
    "episode" to "🎬",
    "movie" to "🍿",
    "lesson" to "✏️",
    "song" to "🎵",
    "story" to "📖",
)

/**
 * 集数数字卡（§4.7 主操作巨大化：集卡集号用大数字）——Detail 集数行与
 * 系列集网格共用。文件名是家长的归档习惯，不是孩子能读的语言：集网格
 * 一律"第 N 集"大数字卡（2026-08-27 产品决策）。
 */
@Composable
fun EpisodeCard(
    episodeNo: Int,
    lastPositionMs: Long,
    completed: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.tvClickable(onClick = onClick),
    ) {
        Box {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(160.dp)
                    .background(
                        if (completed) KindoColors.success.copy(alpha = 0.24f)
                        else if (lastPositionMs > 0) KindoColors.accent.copy(alpha = 0.20f)
                        else KindoColors.surfaceVariant,
                        RoundedCornerShape(22.dp),
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    "$episodeNo",
                    color = KindoColors.textPrimary,
                    fontSize = 64.sp,
                    fontWeight = FontWeight.ExtraBold,
                )
            }
            if (lastPositionMs > 0 && !completed) {
                KidBadge(
                    emoji = "▶",
                    text = if (lastPositionMs >= 60_000)
                        "${lastPositionMs / 60000} 分钟" else "刚刚",
                    modifier = Modifier.align(Alignment.BottomStart).padding(8.dp),
                )
            }
            if (completed) {
                KidBadge(
                    emoji = "✓", text = "看过啦",
                    modifier = Modifier.align(Alignment.BottomStart).padding(8.dp),
                    container = KindoColors.success,
                )
            }
        }
        Spacer(Modifier.height(8.dp))
        // 标签与数字同轴居中：Column 默认 Start 使"第"字距卡片左缘仅 ~13dp，
        // 半分辨率窗口下观感如首字贴边被切；居中后与数字对齐
        Text("第 $episodeNo 集", color = KindoColors.textPrimary, fontSize = 22.sp,
             fontWeight = FontWeight.Bold, textAlign = TextAlign.Center,
             modifier = Modifier.fillMaxWidth())
    }
}
