package org.kindo.pad.ui

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.kindo.pad.ui.theme.KindoColors
import org.kindo.pad.ui.theme.PillShape

/**
 * 触摸按压反馈（Pad 端替代 TV 的 D-pad 焦点视觉 §4.7）：按下轻微缩小回弹。
 * 手指需要"按到了"的物理确认；indication=null 去掉 Material 灰 ripple，
 * 与学龄前糖果视觉一致（没有系统控件灰味）。
 */
fun Modifier.padClickable(enabled: Boolean = true, onClick: () -> Unit): Modifier = composed {
    val interaction = remember { MutableInteractionSource() }
    val pressed by interaction.collectIsPressedAsState()
    val scale by animateFloatAsState(if (pressed) 0.96f else 1f, label = "pressScale")
    this
        .scale(scale)
        .clickable(
            interactionSource = interaction,
            indication = null,
            enabled = enabled,
            onClick = onClick,
        )
}

/**
 * 学龄前主按钮（§4.7：图标优先 + 主操作巨大化；视觉与 TV 端同一份定版）：
 * emoji 图标 + 大字 + 糖果药丸造型（填充色 + 白字 + 轻投影 + 文字描影）。
 * 不识字的孩子靠 emoji 辨认动作，文字只作辅佐且必须短。
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
        modifier = modifier
            .padClickable(onClick = onClick)
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
                // 亮色填充上的白字加淡描影，更易读
                style = androidx.compose.ui.text.TextStyle(
                    shadow = Shadow(Color(0x40000000), offset = androidx.compose.ui.geometry.Offset(0f, 2f), blurRadius = 6f),
                ),
            )
        }
    }
}

/** 软按钮：白底药丸 + 棕字（次要动作——"不聊了/再试一次"这一级）。 */
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
            .padClickable(onClick = onClick)
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

/**
 * 顶栏：屏上返回 + 标题 + 右侧可选语音按钮。
 * 与 TV 端的差异（Pad 端设计决策 2026-08-31）：TV 移除屏上 ← 是因为角落
 * 可聚焦箭头会截获 D-pad 焦点造成误触返回（交互 §3/TV-002）；触屏没有
 * 焦点陷阱问题，而学龄前孩子不认识手势导航——非首页一律提供大号 ← 按钮。
 * onMic 只给浏览类页面（与 TV 同口径）；找一找页不传——页内主角卡即语音入口。
 */
@Composable
fun TopBar(
    title: String,
    showBack: Boolean = false,
    onBack: (() -> Unit)? = null,
    onMic: (() -> Unit)? = null,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 32.dp, vertical = 14.dp),
    ) {
        if (showBack && onBack != null) {
            Box(
                modifier = Modifier
                    .padClickable(onClick = onBack)
                    .background(KindoColors.surface, RoundedCornerShape(999.dp))
                    .border(2.dp, KindoColors.outline, RoundedCornerShape(999.dp))
                    .padding(horizontal = 22.dp, vertical = 10.dp),
            ) {
                androidx.compose.material3.Text(
                    "←", color = KindoColors.textPrimary,
                    fontSize = 26.sp, fontWeight = FontWeight.Black,
                )
            }
            Spacer(Modifier.width(16.dp))
        }
        androidx.compose.material3.Text(
            title,
            color = KindoColors.textPrimary,
            fontSize = 28.sp,
            fontWeight = FontWeight.Bold,
        )
        Spacer(Modifier.weight(1f))
        if (onMic != null) {
            Box(
                modifier = Modifier
                    .padClickable(onClick = onMic)
                    .shadow(5.dp, shape = PillShape, ambientColor = Color(0x28000000),
                            spotColor = Color(0x28000000))
                    .background(KindoColors.accent, PillShape)
                    .padding(horizontal = 24.dp, vertical = 12.dp),
            ) {
                androidx.compose.material3.Text(
                    "🎤 说话", color = Color.White,
                    fontSize = 20.sp, fontWeight = FontWeight.Bold,
                    style = androidx.compose.ui.text.TextStyle(
                        shadow = Shadow(Color(0x40000000),
                                        offset = androidx.compose.ui.geometry.Offset(0f, 2f),
                                        blurRadius = 6f),
                    ),
                )
            }
        }
    }
}

/**
 * 儿童风格双按钮弹窗（与 DenyDialog 同一视觉语言：白卡 + 暖棕大字 + 糖果
 * 按钮）——用于麦克风权限永久拒绝后的家长引导（去设置/知道了）。
 */
@Composable
fun KidDialog(
    emoji: String,
    message: String,
    confirmText: String,
    onConfirm: () -> Unit,
    dismissText: String,
    onDismiss: () -> Unit,
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0x59000000))
            .clickable(onClick = onDismiss),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier
                .background(KindoColors.surface, RoundedCornerShape(32.dp))
                .padding(horizontal = 44.dp, vertical = 36.dp),
        ) {
            androidx.compose.material3.Text(emoji, fontSize = 48.sp)
            Spacer(Modifier.height(12.dp))
            androidx.compose.material3.Text(
                message, color = KindoColors.textPrimary, fontSize = 22.sp,
                fontWeight = FontWeight.Bold,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(24.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                KidButton(emoji = "⚙️", text = confirmText, onClick = onConfirm, fontSize = 20,
                          container = KindoColors.kidBlue)
                KidSoftButton(emoji = "👍", text = dismissText, onClick = onDismiss, fontSize = 19)
            }
        }
    }
}

/** 类型 → 儿童端标签/emoji（与 TV/Admin 同一份命名映射，Browse 筛选与
 *  找一找图标卡共用，防止两处漂移——§4.7 分类选项同源派生）。 */
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
 * 一律"第 N 集"大数字卡（2026-08-27 产品决策，与 TV 同口径）。
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
        modifier = modifier.padClickable(onClick = onClick),
    ) {
        Box {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(150.dp)
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
                    fontSize = 60.sp,
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
        Text("第 $episodeNo 集", color = KindoColors.textPrimary, fontSize = 20.sp,
             fontWeight = FontWeight.Bold, textAlign = TextAlign.Center,
             modifier = Modifier.fillMaxWidth())
    }
}
