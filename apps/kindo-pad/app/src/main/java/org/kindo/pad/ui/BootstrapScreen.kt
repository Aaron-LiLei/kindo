package org.kindo.pad.ui

import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.kindo.pad.AppViewModel
import org.kindo.pad.PairingUi
import org.kindo.pad.ui.theme.KidBackground
import org.kindo.pad.ui.theme.KindoColors

/**
 * 首次连接 Family Hub（交互 §4.1 同口径 + 学龄前视觉 v2）：优先 mDNS 自动
 * 发现，家长选"连接"后屏幕展示 6 位配对码，等待家长在 Web 后台核对批准；
 * 手动输入仅为兜底。本页是家长操作的，文案保留完整说明。
 */
@Composable
fun BootstrapScreen(viewModel: AppViewModel) {
    val pairing by viewModel.pairing.collectAsState()

    Box(modifier = Modifier.fillMaxSize()) {
        KidBackground()
        Box(contentAlignment = Alignment.Center, modifier = Modifier.fillMaxSize()) {
            when (pairing.stage) {
                "awaiting" -> PairingCodeStage(pairing.displayCode, pairing.statusText)
                // 已绑定但 Hub 暂不可达：自动重试中，不重走配对（家长可显式退出重配对）
                "reconnecting" -> ReconnectStage(pairing.statusText) { viewModel.restartPairing() }
                else -> DiscoveryStage(viewModel, pairing)
            }
        }
    }
}

@Composable
private fun ReconnectStage(statusText: String, onRestartPairing: () -> Unit) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
        modifier = Modifier.padding(48.dp).verticalScroll(rememberScrollState()),
    ) {
        Text("🐻", fontSize = 68.sp)
        Spacer(Modifier.height(16.dp))
        Text("正在连接家庭媒体服务", color = KindoColors.textPrimary, fontSize = 34.sp,
             fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(20.dp))
        CircularProgressIndicator(color = KindoColors.accent, modifier = Modifier.size(38.dp))
        Spacer(Modifier.height(16.dp))
        Text(statusText, color = KindoColors.textSecondary, fontSize = 18.sp)
        Spacer(Modifier.height(30.dp))
        KidSoftButton(emoji = "↻", text = "重新配对", onClick = onRestartPairing, fontSize = 17)
    }
}

@Composable
private fun DiscoveryStage(viewModel: AppViewModel, pairing: PairingUi) {
    var manualAddress by remember { mutableStateOf("http://") }

    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 48.dp, vertical = 40.dp),
    ) {
        Text("🏠", fontSize = 58.sp)
        Spacer(Modifier.height(10.dp))
        Text("连接家庭媒体服务", color = KindoColors.textPrimary, fontSize = 34.sp,
             fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(14.dp))
        if (pairing.foundHubs.isEmpty() && pairing.stage == "discovering") {
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(
                    color = KindoColors.accent,
                    modifier = Modifier.size(26.dp),
                )
                Spacer(Modifier.width(14.dp))
                Text(pairing.statusText, color = KindoColors.textSecondary, fontSize = 18.sp)
            }
        } else {
            Text(pairing.statusText, color = KindoColors.textSecondary, fontSize = 18.sp)
        }

        Spacer(Modifier.height(24.dp))

        if (pairing.foundHubs.isNotEmpty()) {
            Column(
                verticalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.fillMaxWidth().widthIn(max = 560.dp),
            ) {
                pairing.foundHubs.forEach { hub ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padClickable { viewModel.startPairing(hub.baseUrl) }
                            .background(KindoColors.surface, RoundedCornerShape(18.dp))
                            .border(2.dp, KindoColors.outline, RoundedCornerShape(18.dp))
                            .padding(horizontal = 26.dp, vertical = 20.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text(hub.displayName, color = KindoColors.textPrimary, fontSize = 22.sp,
                             fontWeight = FontWeight.Bold)
                        Text("连接 ›", color = KindoColors.accentDeep, fontSize = 19.sp,
                             fontWeight = FontWeight.Bold)
                    }
                }
            }
            Spacer(Modifier.height(18.dp))
        }

        if (pairing.stage == "error" || pairing.stage == "denied" || pairing.stage == "expired") {
            KidSoftButton(emoji = "↻", text = "重新查找", onClick = { viewModel.retryDiscovery() })
            Spacer(Modifier.height(12.dp))
        }

        if (pairing.manualInput) {
            Text("家长操作：输入 Kindo Hub 地址", color = KindoColors.textSecondary, fontSize = 15.sp)
            Spacer(Modifier.height(10.dp))
            Row(verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.fillMaxWidth().widthIn(max = 620.dp)) {
                OutlinedTextField(
                    value = manualAddress,
                    onValueChange = { manualAddress = it },
                    modifier = Modifier.weight(1f).widthIn(max = 430.dp),
                    label = { Text("http://192.168.1.10:8090") },
                    textStyle = androidx.compose.ui.text.TextStyle(color = KindoColors.textPrimary),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedContainerColor = KindoColors.surface,
                        unfocusedContainerColor = KindoColors.surface,
                        focusedBorderColor = KindoColors.accent,
                        unfocusedBorderColor = KindoColors.outline,
                        cursorColor = KindoColors.accent,
                    ),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions.Default,
                    singleLine = true,
                )
                Spacer(Modifier.width(14.dp))
                KidButton(emoji = "🔗", text = "连接并配对", fontSize = 17,
                          onClick = { viewModel.startPairing(manualAddress.trim()) })
            }
        } else {
            Text(
                "没找到？",
                Modifier.padClickable { viewModel.showManualInput() }.padding(10.dp),
                color = KindoColors.textSecondary, fontSize = 16.sp,
            )
        }
    }
}

@Composable
private fun PairingCodeStage(code: String, statusText: String) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()),
    ) {
        Text("🔑", fontSize = 52.sp)
        Spacer(Modifier.height(8.dp))
        Text("请家长核对屏幕上的数字", color = KindoColors.textSecondary, fontSize = 20.sp)
        Spacer(Modifier.height(26.dp))
        // 窄竖屏（~600dp）下 6 位大数字卡可能超出屏宽：行内横向滚动兜底
        Row(
            modifier = Modifier.horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.Center,
        ) {
            code.forEach { ch ->
                Text(
                    ch.toString(),
                    color = KindoColors.textPrimary,
                    fontSize = 84.sp,
                    fontWeight = FontWeight.Black,
                    modifier = Modifier
                        .padding(horizontal = 8.dp)
                        .background(KindoColors.surface, RoundedCornerShape(20.dp))
                        .border(2.dp, KindoColors.outline, RoundedCornerShape(20.dp))
                        .padding(horizontal = 18.dp, vertical = 4.dp),
                )
            }
        }
        Spacer(Modifier.height(30.dp))
        CircularProgressIndicator(color = KindoColors.accent)
        Spacer(Modifier.height(16.dp))
        Text(statusText, color = KindoColors.textSecondary, fontSize = 17.sp)
        Text(
            "在管理后台「设备 / 配对」中输入上面的数字并批准",
            color = KindoColors.textSecondary, fontSize = 15.sp,
        )
    }
}
