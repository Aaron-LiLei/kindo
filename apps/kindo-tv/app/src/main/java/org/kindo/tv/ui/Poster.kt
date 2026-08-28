package org.kindo.tv.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.LruCache
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.kindo.tv.ui.theme.KindoColors
import java.util.concurrent.TimeUnit

/** 海报加载器：OkHttp + 内存 LruCache（TV 端海报缓存 §13.2 语义）。 */
object PosterCache {
    private val cache = object : LruCache<String, Bitmap>(64) {}
    // 失败负缓存带时间戳：60s 后允许重试（此前本会话永久不重试，网络抖动
    // 一下整屏占位图直到重启）
    private val failedAt = HashMap<String, Long>()
    private const val FAILED_RETRY_MS = 60_000L
    private val http = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    suspend fun load(url: String, token: String): Bitmap? = withContext(Dispatchers.IO) {
        cache.get(url)?.let { return@withContext it }
        synchronized(failedAt) {
            failedAt[url]?.let { ts ->
                if (System.currentTimeMillis() - ts < FAILED_RETRY_MS) return@withContext null
                failedAt.remove(url)
            }
        }
        try {
            http.newCall(
                Request.Builder().url(url).header("Authorization", "Bearer $token").build(),
            ).execute().use { resp ->
                if (!resp.isSuccessful) {
                    synchronized(failedAt) { failedAt[url] = System.currentTimeMillis() }
                    return@withContext null
                }
                val bmp = BitmapFactory.decodeStream(resp.body?.byteStream()) ?: return@withContext null
                cache.put(url, bmp)
                bmp
            }
        } catch (_: Exception) {
            null
        }
    }

    /** 库结构变化（sync.required）：清负缓存与位图缓存，新目录海报可立即加载。 */
    fun invalidateAll() {
        synchronized(failedAt) { failedAt.clear() }
        cache.evictAll()
    }
}

/** 海报图：无真实海报时端点返回中性默认占位图；加载中/失败为安静渐变块。
 * 内容区分由卡片下方标题行承载（主流媒体库惯例，2026-08-24 去叠标题）。 */
@Composable
fun PosterImage(
    url: String?,
    token: String,
    modifier: Modifier = Modifier,
) {
    var bitmap by remember(url) { mutableStateOf<Bitmap?>(null) }
    LaunchedEffect(url) {
        if (url != null && bitmap == null) {
            bitmap = PosterCache.load(url, token)
        }
    }
    Box(
        // 占位与端点默认海报同一中性色系，视觉统一（审计 P3-12）。
        // v2 亮色化：安静浅暖灰（无文字/无图标/无品牌色的原则不变——
        // 亮底下再放"深色块"会变成整个网格最抢眼的洞）
        modifier = modifier.background(
            Brush.verticalGradient(
                listOf(Color(0xFFF1E8D9), Color(0xFFE3D6C0)),
            ),
        ),
    ) {
        val bmp = bitmap
        if (bmp != null) {
            Image(
                bitmap = bmp.asImageBitmap(),
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}

/** “正在听”脉冲点。 */
@Composable
fun PulsingDot(color: Color = KindoColors.accent, sizeDp: Int = 18) {
    val transition = rememberInfiniteTransition(label = "pulse")
    val alpha by transition.animateFloat(
        initialValue = 0.25f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(650), RepeatMode.Reverse),
        label = "pulseAlpha",
    )
    Box(
        modifier = Modifier
            .padding(2.dp)
            .alpha(alpha)
            .size(sizeDp.dp)
            .background(color, androidx.compose.foundation.shape.CircleShape),
    )
}
