package org.kindo.pad.ui.theme

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

/**
 * 童映儿童端色板（学龄前视觉 v2「阳光奶油」，2026-08-27）：
 * 深色影院底是成人的"家庭影院"惯性——白天客厅里的 3~6 岁孩子需要的是
 * 明亮、温暖、"像绘本一样"的环境。整体基调翻转为奶油暖底 + 白卡片 +
 * 糖果药丸按钮；§4.7 已定版原则（主橙 FF7A3D / 焦点金 FFC24B / 五辅助色 /
 * 图标优先 / 巨大主操作 / 呼吸引导）全部保留并强化。
 */
object KindoColors {
    // 亮暖底：奶油白（页面背景）+ 更亮的顶部（渐变用）
    val background = Color(0xFFFFF6E9)
    val backgroundTop = Color(0xFFFFFAF2)
    // 卡片与容器
    val surface = Color(0xFFFFFFFF)
    val surfaceVariant = Color(0xFFF6EBDA)
    val outline = Color(0xFFF0E0C8)
    // 主点缀：暖橙（§4.7 定版，不变）
    val accent = Color(0xFFFF7A3D)
    val accentDeep = Color(0xFFE05A17) // 亮底上的强调文字（警示/拒绝原因）
    // 文字：暖深棕（绘本感，奶油底上对比度 ~9:1）
    val textPrimary = Color(0xFF4A3628)
    val textSecondary = Color(0xFF93796A)
    // 焦点指示（§4.7 定版，不变）：焦点金 + 光晕
    val focusRing = Color(0xFFFFC24B)
    // 辅助色（成长接力选项 / 徽章 / 主题卡的图形化编码，§4.7 定版）
    val kidBlue = Color(0xFF57B8FF)
    val kidGreen = Color(0xFF6ECF94)
    val kidYellow = Color(0xFFFFD25A)
    val kidPink = Color(0xFFFF8FB1)
    val kidPurple = Color(0xFFB69CFF)
    val success = Color(0xFF6ECF94)
    // 播放器深色遮罩上的文字（视频区仍是黑的，遮罩文字用暖白）
    val onDark = Color(0xFFFFF6EA)
}

/** 焦点卡片通用形状。 */
val CardShape = RoundedCornerShape(16.dp)

/** 糖果药丸形状（主按钮/状态 chips）。 */
val PillShape = RoundedCornerShape(999.dp)

/**
 * 学龄前页面背景：奶油渐变 + 四角安静的软色块。
 * 色块是静态的、低透明度的——装饰存在但绝不抢注意力（§4.7 呼吸引导只给
 * 语音入口这类"注意力入口"）。
 */
@Composable
fun KidBackground(modifier: Modifier = Modifier) {
    Box(modifier = modifier.fillMaxSize()) {
        Box(
            Modifier.fillMaxSize().background(
                Brush.verticalGradient(
                    listOf(KindoColors.backgroundTop, KindoColors.background)
                )
            )
        )
        // 大而安静的软色块（绘本封面的"阳光感"）
        Box(
            Modifier.align(Alignment.TopStart).offset(x = (-70).dp, y = (-60).dp)
                .size(300.dp).background(KindoColors.kidYellow.copy(alpha = 0.12f), CircleShape)
        )
        Box(
            Modifier.align(Alignment.TopEnd).offset(x = 80.dp, y = (-90).dp)
                .size(260.dp).background(KindoColors.kidPink.copy(alpha = 0.10f), CircleShape)
        )
        Box(
            Modifier.align(Alignment.BottomStart).offset(x = (-90).dp, y = 90.dp)
                .size(320.dp).background(KindoColors.kidBlue.copy(alpha = 0.10f), CircleShape)
        )
        Box(
            Modifier.align(Alignment.BottomEnd).offset(x = 70.dp, y = 110.dp)
                .size(280.dp).background(KindoColors.kidGreen.copy(alpha = 0.10f), CircleShape)
        )
        Box(
            Modifier.align(Alignment.CenterEnd).offset(x = 40.dp, y = (-160).dp)
                .size(90.dp).background(KindoColors.kidPurple.copy(alpha = 0.12f), CircleShape)
        )
    }
}
