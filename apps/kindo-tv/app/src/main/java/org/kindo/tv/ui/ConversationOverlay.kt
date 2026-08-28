package org.kindo.tv.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.kindo.tv.AppViewModel
import org.kindo.tv.ui.theme.KindoColors
import org.kindo.tv.ui.theme.PillShape

/**
 * AI Conversation Overlay（交互 §4.5 / §5 状态机 + 学龄前视觉 v2）：覆盖在
 * 任意页面/播放器之上，同一 Session 跨页面连续；澄清候选可说话也可 D-pad 选择。
 * v2：暖奶油遮罩（对话是"跟朋友聊天"，不是系统弹窗）；状态行 emoji 领衔放大；
 * 全部灰色 Material 按钮换糖果药丸。
 */
@Composable
fun ConversationOverlay(viewModel: AppViewModel) {
    val state by viewModel.conversation.collectAsState()
    val transition by viewModel.transition.collectAsState()
    // 成长接力由本 Overlay 承接（交互 v0.3 §5.2，不进独立页面）
    if (transition.phase != "idle") {
        TransitionOverlayContent(viewModel, transition)
        return
    }
    if (!state.active) return

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xF2FFF7EC)),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp),
            modifier = Modifier.padding(60.dp).width(800.dp),
        ) {
            // 状态行（交互 §5）：大 emoji 是状态的第一信道的，文字只是辅佐
            PhaseBadge(state.phase, state.toolStatus)

            if (state.retryHint.isNotEmpty()) {
                Text(state.retryHint, color = KindoColors.accentDeep, fontSize = 20.sp,
                     fontWeight = FontWeight.Bold, textAlign = TextAlign.Center)
            }

            // 儿童的转写文本（确认系统听对，交互 §4.5）
            if (state.asrText.isNotEmpty()) {
                Text(
                    "“${state.asrText}”",
                    color = KindoColors.textSecondary,
                    fontSize = 21.sp,
                    textAlign = TextAlign.Center,
                )
            }

            // AI 流式文本
            if (state.aiText.isNotEmpty()) {
                Text(
                    state.aiText,
                    color = KindoColors.textPrimary,
                    fontSize = 26.sp,
                    lineHeight = 37.sp,
                    textAlign = TextAlign.Center,
                )
            }

            // 澄清候选：可 D-pad 选择（ui.selection），也可说话回答；
            // 横向滚动（长标题选项不再溢出截断，审计 P3-13）
            if (state.options.isNotEmpty()) {
                Spacer(Modifier.height(4.dp))
                LazyRow(
                    horizontalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    itemsIndexed(state.options.take(4)) { _, opt ->
                        Box(
                            modifier = Modifier
                                .tvClickable { viewModel.selectOption(opt.id) }
                                .background(KindoColors.surface, PillShape)
                                .border(2.dp, KindoColors.outline, PillShape)
                                .padding(horizontal = 28.dp, vertical = 14.dp),
                        ) {
                            Text(opt.label, color = KindoColors.textPrimary, fontSize = 20.sp,
                                 fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }

            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                // SPEAKING 期间 = 打断并重新说（交互 §5）；其他期间 = 结束
                if (state.phase == "speaking") {
                    KidButton(emoji = "🎤", text = "我要说话", fontSize = 22,
                              onClick = { viewModel.interruptSpeaking() })
                }
                KidSoftButton(emoji = "🙅", text = "不聊了", fontSize = 20,
                              onClick = { viewModel.endConversation() })
            }
        }
    }
}

/** 对话状态行：emoji 领衔 + 短句（听=呼吸麦克风；想=思考泡泡）。 */
@Composable
private fun PhaseBadge(phase: String, toolStatus: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        when (phase) {
            "listening" -> Box(Modifier.kidBreathing()) {
                Text("🎤", fontSize = 72.sp)
            }
            "transcribing" -> Text("👂", fontSize = 60.sp)
            "thinking" -> Text("💭", fontSize = 60.sp)
            "tool_running" -> Text("🔍", fontSize = 60.sp)
            "speaking" -> Text("🐻", fontSize = 60.sp)
            "follow_up" -> Text("🐻", fontSize = 60.sp)
            "error" -> Text("😵", fontSize = 60.sp)
            else -> {}
        }
        Spacer(Modifier.height(4.dp))
        val label = when (phase) {
            "listening" -> "正在听你说…"
            "transcribing" -> "我想想刚才听到的话…"
            "thinking" -> "让我想想…"
            "tool_running" -> toolStatus.ifEmpty { "正在找…" }
            "speaking" -> "AI 说："
            "follow_up" -> "还想问什么？"
            "error" -> "出了点小问题，稍后再试试"
            else -> ""
        }
        if (label.isNotEmpty()) {
            Text(
                label,
                color = if (phase == "speaking") KindoColors.accentDeep else KindoColors.textSecondary,
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
            )
        }
    }
}

/** 成长接力呈现：offer 选项 / 互动（听/想/说循环+剩余时间）/ 离屏活动卡 / 温和收尾。 */
@Composable
private fun TransitionOverlayContent(viewModel: AppViewModel, t: org.kindo.tv.TransitionUi) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xF2FFF7EC)),
    ) {
        // 右上角柔和的剩余时间提示（交互 §5.2 TRANSITION_INTERACTION）
        if (t.remainingSeconds >= 0 && (t.phase == "interaction" || t.phase == "offer")) {
            Text(
                if (t.remainingSeconds > 0)
                    "⏳ 还能聊 ${t.remainingSeconds / 60}:${"%02d".format(t.remainingSeconds % 60)}"
                else "⏳ 时间到啦",
                color = KindoColors.textSecondary, fontSize = 18.sp,
                modifier = Modifier
                    .align(Alignment.TopEnd)
                    .padding(horizontal = 40.dp, vertical = 26.dp),
            )
        }
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(20.dp),
            modifier = Modifier.align(Alignment.Center).padding(60.dp).width(860.dp),
        ) {
            when (t.phase) {
                "offer" -> {
                    Text(
                        t.openingText,
                        color = KindoColors.textPrimary, fontSize = 30.sp,
                        fontWeight = FontWeight.Bold, textAlign = TextAlign.Center,
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                        val optColors = listOf(
                            KindoColors.accent, KindoColors.kidBlue, KindoColors.kidGreen)
                        t.options.forEachIndexed { i, opt ->
                            KidButton(
                                emoji = transitionEmoji(opt.type),
                                text = opt.label,
                                onClick = { viewModel.selectTransitionOption(opt.type) },
                                container = optColors[i % optColors.size],
                                fontSize = 22,
                            )
                        }
                    }
                }
                "interaction" -> TransitionInteractionBody(viewModel)
                "offscreen" -> {
                    val act = t.activity
                    if (act != null) {
                        Text("🎲 ${act.title}", color = KindoColors.accentDeep,
                             fontSize = 34.sp, fontWeight = FontWeight.Bold,
                             textAlign = TextAlign.Center)
                        Text(
                            act.summary,
                            color = KindoColors.textPrimary, fontSize = 25.sp,
                            lineHeight = 36.sp,
                            textAlign = TextAlign.Center,
                        )
                        KidButton(
                            emoji = "✅", text = "做好啦",
                            onClick = { viewModel.finishTransitionActivity() },
                            container = KindoColors.success, fontSize = 24,
                        )
                    } else {
                        Text(
                            "🎮 去玩一个不看屏幕的小游戏吧！",
                            color = KindoColors.textPrimary, fontSize = 27.sp,
                            textAlign = TextAlign.Center,
                        )
                        KidButton(
                            emoji = "✅", text = "做好啦",
                            onClick = { viewModel.finishTransitionActivity() },
                            container = KindoColors.success, fontSize = 24,
                        )
                    }
                }
                "ended" -> {
                    // 一句温和收尾（交互 §5.2 TRANSITION_ENDED；拒绝即止不挽留）
                    Text(
                        "🌙",
                        fontSize = 64.sp,
                        textAlign = TextAlign.Center,
                    )
                    Text(
                        t.closingText,
                        color = KindoColors.textPrimary, fontSize = 30.sp,
                        fontWeight = FontWeight.Bold, textAlign = TextAlign.Center,
                    )
                }
            }
            if (t.phase != "ended") {
                KidSoftButton(emoji = "🙅", text = "不聊了", fontSize = 20,
                              onClick = { viewModel.rejectTransition() })
            }
        }
    }
}

/** 接力互动 = 与普通对话一致的听/想/说循环（交互 §5.2）：自动开麦后呈现会话状态。 */
@Composable
private fun TransitionInteractionBody(viewModel: AppViewModel) {
    val conv by viewModel.conversation.collectAsState()
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        if (!conv.active) {
            // 会话空闲窗口过后：孩子想继续就按这里（时间盒内可反复进入）
            Text(
                "我们聊两句吧，说完就休息",
                color = KindoColors.textPrimary, fontSize = 28.sp,
                fontWeight = FontWeight.Bold,
            )
            KidButton(
                emoji = "🎤", text = "我想说",
                onClick = { viewModel.startConversation(resume = true) },
                fontSize = 24,
            )
            return@Column
        }
        PhaseBadge(conv.phase, conv.toolStatus)
        if (conv.asrText.isNotEmpty()) {
            Text(
                "“${conv.asrText}”",
                color = KindoColors.textSecondary, fontSize = 20.sp,
                textAlign = TextAlign.Center,
            )
        }
        if (conv.aiText.isNotEmpty()) {
            Text(
                conv.aiText,
                color = KindoColors.textPrimary, fontSize = 24.sp,
                lineHeight = 34.sp, textAlign = TextAlign.Center,
                maxLines = 8,
                overflow = TextOverflow.Ellipsis,
            )
        }
        if (conv.phase == "speaking") {
            KidButton(emoji = "🎤", text = "我要说话", fontSize = 20,
                      onClick = { viewModel.interruptSpeaking() })
        }
    }
}

/** 接力选项的图形编码（不识字的孩子靠 emoji 辨认活动类型）。 */
private fun transitionEmoji(type: String): String = when (type) {
    "knowledge" -> "💡"
    "quiz" -> "❓"
    "roleplay" -> "🎭"
    "vocabulary" -> "🔤"
    "song_story" -> "🎵"
    "offscreen_game" -> "🎮"
    "real_explore" -> "🔍"
    else -> "✨"
}
