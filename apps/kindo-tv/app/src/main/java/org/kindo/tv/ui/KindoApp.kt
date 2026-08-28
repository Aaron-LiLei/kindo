package org.kindo.tv.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import org.kindo.tv.AppViewModel
import org.kindo.tv.Screen

/**
 * TV 端组合入口（交互 §2）：一个主界面 + 内容页 + 播放器 + 全局 AI 对话层。
 * Back 行为（交互 §3）：对话覆盖层 > 播放器 > 详情/浏览 > 首页。
 */
@Composable
fun KindoApp(viewModel: AppViewModel) {
    val stack by viewModel.screenStack.collectAsState()
    val conversation by viewModel.conversation.collectAsState()
    val transition by viewModel.transition.collectAsState()

    // 显式语音会话需要麦克风权限（仅会话期间采集，交互 §5.2）
    var micGranted by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(
                viewModel.getApplication(), Manifest.permission.RECORD_AUDIO,
            ) == PackageManager.PERMISSION_GRANTED,
        )
    }
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> micGranted = granted }

    LaunchedEffect(Unit) {
        if (!micGranted) permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
    }

    // Back：接力层（拒绝即止）> 对话覆盖层 > 播放器 > 详情/浏览 > 首页（交互 §3）
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
    // 首页拦截 BACK（学龄前误触遥控返回即退出 App；退出走系统 HOME 键，
    // TV 审计 P1-4）
    BackHandler(enabled = transition.phase == "idle" && !conversation.active && stack.size <= 1) {
        // 消费掉，不退出
    }

    Box(modifier = Modifier.fillMaxSize().background(org.kindo.tv.ui.theme.KindoColors.background)) {
        when (val screen = current) {
            is Screen.Bootstrap -> BootstrapScreen(viewModel)
            is Screen.Home -> HomeScreen(viewModel, micGranted)
            is Screen.Browse -> BrowseScreen(viewModel, screen)
            is Screen.Detail -> DetailScreen(viewModel, screen)
            is Screen.Search -> SearchScreen(viewModel)
            is Screen.Player -> PlayerScreen(viewModel, micGranted)
        }
        // 全局 AI 对话层：覆盖在任意页面之上，不改变页面上下文（交互 §2）
        ConversationOverlay(viewModel)
    }}
