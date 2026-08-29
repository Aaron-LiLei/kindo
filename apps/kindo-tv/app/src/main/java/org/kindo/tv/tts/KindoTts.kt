package org.kindo.tv.tts

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap

/**
 * Android 系统 TTS（V0.1 默认，技术方案 §1/§11.4）。
 * 必须回报 tts_started / tts_finished / tts_interrupted（架构 §7 第 8 步）。
 */
class KindoTts(context: Context) {
    private var tts: TextToSpeech? = null
    private var ready = false
    private val callbacks = ConcurrentHashMap<String, (String) -> Unit>()
    // UtteranceProgressListener 回调在 binder 线程；回跳主线程后再分发
    // （回调链触及 ExoPlayer duck/unduck，Media3 要求主线程访问）
    private val mainHandler = android.os.Handler(android.os.Looper.getMainLooper())

    init {
        tts = TextToSpeech(context) { status ->
            ready = status == TextToSpeech.SUCCESS
            if (ready) {
                // 中文语音数据校验：盒子无 zh 语音包时 setLanguage 返回缺失码，
                // 静默"有字无声"——按不可用降级（仅显文本，会话可继续，交互 §10）
                val langResult = tts?.setLanguage(Locale.SIMPLIFIED_CHINESE)
                    ?: TextToSpeech.LANG_NOT_SUPPORTED
                if (langResult == TextToSpeech.LANG_MISSING_DATA ||
                    langResult == TextToSpeech.LANG_NOT_SUPPORTED
                ) {
                    android.util.Log.w("KindoTts", "中文语音数据不可用（$langResult），降级为仅文本")
                    ready = false
                    return@TextToSpeech
                }
                tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(utteranceId: String?) {
                        utteranceId?.let { id -> mainHandler.post { callbacks[id]?.invoke("started") } }
                    }

                    override fun onDone(utteranceId: String?) {
                        utteranceId?.let { cb ->
                            mainHandler.post { callbacks.remove(cb)?.invoke("finished") }
                        }
                    }

                    @Deprecated("Deprecated in Java")
                    override fun onError(utteranceId: String?) {
                        utteranceId?.let { cb ->
                            mainHandler.post { callbacks.remove(cb)?.invoke("interrupted") }
                        }
                    }

                    override fun onStop(utteranceId: String?, interrupted: Boolean) {
                        // stop() 会清空排队中的分句：逐句回收回调，避免泄漏与幽灵回调
                        utteranceId?.let { cb ->
                            mainHandler.post { callbacks.remove(cb)?.invoke("interrupted") }
                        }
                    }
                })
            }
        }
    }

    val available: Boolean get() = ready

    fun speak(ttsId: String, text: String, onEvent: (String) -> Unit) {
        if (!ready) {
            // TTS 不可用降级：按 finished 处理（仅显示文本，会话可继续，交互 §10）
            onEvent("finished")
            return
        }
        callbacks[ttsId] = onEvent
        // 分句流式（技术方案 §11.4）：一回合并发多条 tts.request，逐句排队播报；
        // 打断用 stop() 显式清空队列，不能在此 FLUSH（会掐掉上一句）
        tts?.speak(text, TextToSpeech.QUEUE_ADD, null, ttsId)
    }

    fun stop() {
        tts?.stop()
    }

    fun shutdown() {
        tts?.shutdown()
        tts = null
    }
}
