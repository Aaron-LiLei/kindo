package org.kindo.tv

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import org.kindo.tv.ui.KindoApp

/**
 * 童映 Kindo TV 端唯一入口：启动直接进入儿童界面（TV-001），
 * 不提供模式选择；未绑定时先进入一次性家长初始化配对页（交互 §4.1）。
 */
class MainActivity : ComponentActivity() {
    private val appViewModel: AppViewModel by viewModels { AppViewModel.factory(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            KindoApp(viewModel = appViewModel)
        }
    }

    override fun onKeyDown(keyCode: Int, event: android.view.KeyEvent?): Boolean {
        // 遥控器语音/助手键（KEYCODE_ASSIST，部分厂商语音键映射为此键）
        // → 直接启动 AI 对话（交互 §5 显式入口；厂商私有键见实机适配清单）
        if (keyCode == android.view.KeyEvent.KEYCODE_ASSIST && appViewModel.screenStack.value.lastOrNull()
                !is org.kindo.tv.Screen.Bootstrap) {
            if (!appViewModel.conversation.value.active) {
                appViewModel.startConversation()
            }
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun onDestroy() {
        super.onDestroy()
        if (isFinishing) {
            appViewModel.shutdown()
        }
    }
}
