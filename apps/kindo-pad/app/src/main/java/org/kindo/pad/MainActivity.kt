package org.kindo.pad

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import org.kindo.pad.ui.KindoApp

/**
 * 童映 Kindo Pad 端唯一入口：启动直接进入儿童界面（与 TV 端 TV-001 同口径），
 * 不提供模式选择；未绑定时先进入一次性家长初始化配对页（交互 §4.1）。
 * 横竖屏与多尺寸由 Compose 自适应布局承载（configChanges 声明在 Manifest，
 * 旋转不重建 Activity，ViewModel 持有的播放器不中断）。
 */
class MainActivity : ComponentActivity() {
    private val appViewModel: AppViewModel by viewModels { AppViewModel.factory(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            KindoApp(viewModel = appViewModel)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        if (isFinishing) {
            appViewModel.shutdown()
        }
    }
}
