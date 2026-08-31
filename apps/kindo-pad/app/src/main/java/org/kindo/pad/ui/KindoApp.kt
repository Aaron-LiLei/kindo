package org.kindo.pad.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import org.kindo.pad.AppViewModel
import org.kindo.pad.Screen

/**
 * 语音入口统一经权限门（Pad 与 TV 的差异点）：触屏设备 RECORD_AUDIO 是
 * 运行时权限，首次开口时在上下文中请求（主流 Android 实践），批准后自动
 * 开始会话——孩子无感知。ready 供各入口呈现"呼吸/禁用"视觉。
 */
data class VoiceEntry(
    val ready: Boolean,
    val start: (resume: Boolean) -> Unit,
)

/**
 * Pad 端组合入口（与 TV 端交互 §2 同构）：一个主界面 + 内容页 + 播放器 +
 * 全局 AI 对话层。Back 行为（交互 §3 同口径）：
 * 接力层 > 对话覆盖层 > 播放器（控制条由 PlayerScreen 自己先收）> 详情/浏览 > 首页。
 * 首页拦截返回（学龄前误触即退出 App；退出走系统 HOME——TV 审计 P1-4
 * 的儿童保护理由在触屏上同样成立，不做"返回即退出"的成人惯例）。
 */
@Composable
fun KindoApp(viewModel: AppViewModel) {
    val app = viewModel.getApplication<android.app.Application>()
    val stack by viewModel.screenStack.collectAsState()
    val conversation by viewModel.conversation.collectAsState()
    val transition by viewModel.transition.collectAsState()

    var micGranted by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(app, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED,
        )
    }
    // 授权回调后要接续的启动意图；null = 无待启动（普通请求）
    var pendingVoiceResume by remember { mutableStateOf<Boolean?>(null) }
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        micGranted = granted
        val resume = pendingVoiceResume
        pendingVoiceResume = null
        if (granted && resume != null) viewModel.startConversation(resume)
    }
    val caps by viewModel.capabilities.collectAsState()
    val voice = VoiceEntry(
        ready = caps.capabilities.voice_available && caps.capabilities.ai_available && micGranted,
    ) { resume ->
        if (micGranted) viewModel.startConversation(resume)
        else {
            pendingVoiceResume = resume
            permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    // Back：接力层（拒绝即止）> 对话覆盖层 > 播放器 > 详情/浏览 > 首页拦截（交互 §3）
    BackHandler(enabled = transition.phase == "offer" || transition.phase == "interaction" ||
        transition.phase == "offscreen") {
        viewModel.rejectTransition()
    }
    BackHandler(enabled = transition.phase == "ended") {
        // 收尾提示期间消费 BACK，不落栈
    }
    BackHandler(enabled = transition.phase == "idle" && conversation.active) {
        viewModel.endConversation()
    }
    val current = stack.lastOrNull() ?: Screen.Bootstrap
    BackHandler(enabled = transition.phase == "idle" && !conversation.active && stack.size > 1) {
        viewModel.goBack()
    }
    BackHandler(enabled = transition.phase == "idle" && !conversation.active && stack.size <= 1) {
        // 首页拦截 BACK：不退出（儿童防误触，与 TV 端 P1-4 同口径）
    }

    Box(modifier = Modifier.fillMaxSize().background(org.kindo.pad.ui.theme.KindoColors.background)) {
        when (val screen = current) {
            is Screen.Bootstrap -> BootstrapScreen(viewModel)
            is Screen.Home -> HomeScreen(viewModel, voice)
            is Screen.Browse -> BrowseScreen(viewModel, screen, voice)
            is Screen.Detail -> DetailScreen(viewModel, screen, voice)
            is Screen.Search -> SearchScreen(viewModel, voice)
            is Screen.Player -> PlayerScreen(viewModel, voice)
        }
        // 全局 AI 对话层：覆盖在任意页面之上，不改变页面上下文（交互 §2）
        ConversationOverlay(viewModel)
    }
}
