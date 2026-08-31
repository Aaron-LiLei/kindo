package org.kindo.pad.net

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** 音频 MIME 判定（PLY-008 音频播放页渲染依据，与 TV 端同一实现口径）。 */
class AudioPlaybackHintTest {

    @Test
    fun audioMimeTypes() {
        assertTrue(AudioPlaybackHint.isAudio("audio/mpeg"))
        assertTrue(AudioPlaybackHint.isAudio("audio/mp4"))
    }

    @Test
    fun videoAndNullAreNotAudio() {
        assertFalse(AudioPlaybackHint.isAudio("video/mp4"))
        assertFalse(AudioPlaybackHint.isAudio(null))
    }
}
