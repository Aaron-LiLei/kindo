package org.kindo.pad.net

/** 音频 MIME 判定（PLY-008 音频播放页渲染依据）。 */
object AudioPlaybackHint {
    fun isAudio(mimeType: String?): Boolean =
        mimeType != null && mimeType.startsWith("audio/")
}
