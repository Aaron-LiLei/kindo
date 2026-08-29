package org.kindo.tv.tts

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** hub_tts WAV 解析（技术方案 §6.7）：非法/不支持格式必须返回 null（走系统 TTS 回退）。 */
class WavParserTest {

    private fun wavBytes(sampleRate: Int, channels: Int, samples: ShortArray): ByteArray {
        val out = ByteArrayOutputStream()
        val pcm = ByteArray(samples.size * 2)
        val pcmBuf = ByteBuffer.wrap(pcm).order(ByteOrder.LITTLE_ENDIAN)
        samples.forEach { pcmBuf.putShort(it) }
        val fmt = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN)
        fmt.putShort(1) // PCM
        fmt.putShort(channels.toShort())
        fmt.putInt(sampleRate)
        fmt.putInt(sampleRate * channels * 2) // byte rate
        fmt.putShort((channels * 2).toShort()) // block align
        fmt.putShort(16) // bits per sample

        out.write("RIFF".toByteArray())
        val riffSize = 4 + (8 + 16) + (8 + pcm.size)
        out.write(ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(riffSize).array())
        out.write("WAVE".toByteArray())
        out.write("fmt ".toByteArray())
        out.write(ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(16).array())
        out.write(fmt.array())
        out.write("data".toByteArray())
        out.write(ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(pcm.size).array())
        out.write(pcm)
        return out.toByteArray()
    }

    @Test
    fun parsesMonoPcm16() {
        val samples = shortArrayOf(0, 3276, -3276, 1638)
        val parsed = WavParser.parse(wavBytes(24000, 1, samples))
        assertNotNull(parsed)
        val (pcm, rate) = parsed!!
        assertEquals(24000, rate)
        assertEquals(samples.size * 2, pcm.size)
        assertEquals(0, pcm[0].toInt() and 0xFF)
    }

    @Test
    fun rejectsGarbage() {
        assertNull(WavParser.parse(ByteArray(10)))
        assertNull(WavParser.parse("not a wav at all".toByteArray()))
    }

    @Test
    fun rejectsStereo() {
        assertNull(WavParser.parse(wavBytes(24000, 2, shortArrayOf(0, 1))))
    }

    @Test
    fun skipsUnknownChunkBeforeData() {
        val base = wavBytes(24000, 1, shortArrayOf(100, -100))
        // 在 fmt 与 data 之间插一个 LIST 自定义块（真实录音软件常见）
        val list = "LIST".toByteArray() +
            ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(4).array() +
            "INFO".toByteArray()
        val anchor = "data".toByteArray()
        val idx = base.indexOfSlice(anchor)
        val patched = base.copyOfRange(0, idx) + list + base.copyOfRange(idx, base.size)
        // RIFF 大小字段不再准确——解析器按块遍历不依赖总长，应仍成功
        val parsed = WavParser.parse(patched)
        assertNotNull(parsed)
        assertEquals(24000, parsed!!.second)
    }

    private fun ByteArray.indexOfSlice(slice: ByteArray): Int {
        outer@ for (i in 0..this.size - slice.size) {
            for (j in slice.indices) {
                if (this[i + j] != slice[j]) continue@outer
            }
            return i
        }
        error("slice not found")
    }
}
