package org.kindo.pad.ui

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
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
 *
 * 麦克风权限三态（Pad 端设计决策 §权限）：
 * ① 未请求 → 点击即请求，批准自动开场（ON_RESUME 统一接续）；
 * ② 拒绝未勾"不再询问" → 下次点击再次请求；
 * ③ 永久拒绝（不再询问） → 点击出家长引导弹窗，「去设置」直达应用详情页，
 *    授权返回后自动接续开场——孩子任何点击都不落空。
 */
@Composable
fun KindoApp(viewModel: AppViewModel) {
    val app = viewModel.getApplication<android.app.Application>()
    val activity = LocalContext.current as? Activity
    val stack by viewModel.screenStack.collectAsState()
    val conversation by viewModel.conversation.collectAsState()
    val transition by viewModel.transition.collectAsState()

    fun micGrantedNow(): Boolean =
        ContextCompat.checkSelfPermission(app, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED

    var micGranted by remember { mutableStateOf(micGrantedNow()) }
    // 永久拒绝只在"请求被拒之后"判定（未请求前 rationale 恒 false，不可作为依据）
    var micPermanentlyDenied by remember { mutableStateOf(false) }
    var showMicSettings by remember { mutableStateOf(false) }
    // 两条接续通道分离，避免权限弹窗（onResume 亦触发）与回调双开话术
    var pendingFromLauncher by remember { mutableStateOf<Boolean?>(null) }
    var pendingFromSettings by remember { mutableStateOf<Boolean?>(null) }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        micGranted = granted
        if (!granted && activity != null) {
            micPermanentlyDenied =
                !activity.shouldShowRequestPermissionRationale(Manifest.permission.RECORD_AUDIO)
        }
        val resume = pendingFromLauncher
        pendingFromLauncher = null
        if (granted && resume != null) viewModel.startConversation(resume)
    }

    // 从系统设置返回（家长在设置里授权）：刷新视觉态；有待开场意图则自动接续
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                micGranted = micGrantedNow()
                if (micGranted) micPermanentlyDenied = false
                val resume = pendingFromSettings
                if (resume != null && micGrantedNow()) {
                    pendingFromSettings = null
                    viewModel.startConversation(resume)
                }
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    val caps by viewModel.capabilities.collectAsState()
    val voice = VoiceEntry(
        ready = caps.capabilities.voice_available && caps.capabilities.ai_available && micGranted,
    ) { resume ->
        when {
            micGrantedNow() -> viewModel.startConversation(resume)
            // 永久拒绝：系统不再弹窗，直接引导家长去设置；授权返回自动开场
            micPermanentlyDenied -> {
                pendingFromSettings = resume
                showMicSettings = true
            }
            else -> {
                pendingFromLauncher = resume
                permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
            }
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

        if (showMicSettings) {
            KidDialog(
                emoji = "🎤",
                message = "和小熊说话需要「麦克风」权限\n请爸爸妈妈到系统设置里允许",
                confirmText = "去设置",
                onConfirm = {
                    showMicSettings = false
                    activity?.startActivity(
                        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                            .setData(Uri.parse("package:${app.packageName}")),
                    )
                },
                dismissText = "知道了",
                onDismiss = {
                    showMicSettings = false
                    pendingFromSettings = null
                },
            )
        }
    }
}
