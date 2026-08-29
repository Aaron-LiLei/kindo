package org.kindo.tv

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import org.kindo.tv.core.BootstrapInfo
import org.kindo.tv.core.DiscoveredHub
import org.kindo.tv.core.Discovery
import org.kindo.tv.core.EpisodeInfo
import org.kindo.tv.core.HomeData
import org.kindo.tv.core.HubClient
import org.kindo.tv.core.MediaDetail
import org.kindo.tv.core.MediaPage
import org.kindo.tv.core.MediaSummary
import org.kindo.tv.core.PlayResult
import org.kindo.tv.net.DeviceStore
import org.kindo.tv.net.RealtimeClient
import org.kindo.tv.net.VoiceClient
import org.kindo.tv.playback.PlaybackController
import org.kindo.tv.playback.StreamDescriptor
import org.kindo.tv.playback.TrackRef
import org.kindo.tv.tts.HubTtsPlayer
import org.kindo.tv.tts.KindoTts
import java.util.UUID

/** 页面栈（交互 §2：首页 / 浏览 / 详情 / 播放器 + 全局对话覆盖层）。 */
sealed class Screen {
    object Bootstrap : Screen()
    object Home : Screen()
    data class Browse(
        val type: String?,
        val tag: String?,
        val title: String,
        val query: String? = null,
        val seriesId: String? = null,
    ) : Screen()
    data class Detail(val mediaId: String) : Screen()
    object Search : Screen()
    object Player : Screen()
}

/** 配对流程 UI 状态（交互 §4.1：发现 → 连接 → 6 位码 → 家长批准）。 */
data class PairingUi(
    val stage: String = "discovering",  // discovering / awaiting / approved / denied / expired / error
    val foundHubs: List<DiscoveredHub> = emptyList(),
    val manualInput: Boolean = false,
    val displayCode: String = "",
    val statusText: String = "正在查找家庭媒体服务…",
)

data class OptionItem(val id: String, val label: String)

/** 成长接力 UI 状态（交互 v0.3 §5.2）：由 Conversation Overlay 承接。
 * 时间盒带客户端兜底倒计时（交互 §10：到点强制收尾，宁可提前不无限延长）。 */
data class TransitionUi(
    val transitionId: String = "",
    val phase: String = "idle",  // idle / offer / interaction / offscreen / ended
    val openingText: String = "",
    val options: List<TransitionOption> = emptyList(),
    val activity: TransitionActivity? = null,
    val deadlineMs: Long = 0,
    val remainingSeconds: Int = -1,  // -1 = 无倒计时
    val closingText: String = "",
)

data class TransitionOption(val type: String, val label: String)

data class TransitionActivity(val title: String, val summary: String)

data class ConversationState(
    val active: Boolean = false,
    val phase: String = "idle",
    // listening / transcribing / thinking / tool_running / speaking / follow_up / error
    val asrText: String = "",
    val aiText: String = "",
    val toolStatus: String = "",
    val options: List<OptionItem> = emptyList(),
    val retryHint: String = "",
)

/**
 * TV 端全局状态：绑定、页面栈、浏览数据、会话与播放（架构 A-05：TV 只持有
 * 播放执行状态与 UI 镜像）。一切可能增加/继续观看时长的动作（含 D-pad 播放）
 * 统一走服务端 POST /playbacks 的 Policy 校验（AI-007 / A-06）。
 */
class AppViewModel(app: Application) : AndroidViewModel(app) {
    /** 设备身份：随绑定持久化（每进程随机 → Hub 设备列表随重启膨胀，ADM-015 语义受损）。 */
    private var deviceId: String = UUID.randomUUID().toString()

    private val deviceStore = DeviceStore(app)
    val hub = HubClient()
    val discovery = Discovery(app)
    val realtime = RealtimeClient(app, ::onRealtimeEvent)
    val voice = VoiceClient(app).also {
        // 麦克风初始化失败 → 会话层提示（不再静默停留在"正在听"）
        it.onMicError = { _ ->
            conversation.value = conversation.value.copy(
                phase = "error",
                retryHint = "麦克风还没准备好，请爸爸妈妈检查一下电视的麦克风权限",
            )
        }
        // 语音 WS 断线：听/转写中自动重连（≤2 次），超限转错误态（不再卡"正在听"）
        it.onOpen = { voiceReconnectAttempts = 0 }
        it.onDropped = { onVoiceDropped() }
    }
    val tts = KindoTts(app)
    // hub_tts（家长声音克隆，技术方案 §6.7）：tts.request 带 audio_path 时走此播放器，
    // 取音频失败/解码失败回退系统 TTS 读同句文本（事件语义一致）
    val hubTts = HubTtsPlayer(hub, fallback = { id, text, onEvent -> tts.speak(id, text, onEvent) })
    val playbackController = PlaybackController(app)

    private val _screenStack = MutableStateFlow<List<Screen>>(listOf(Screen.Bootstrap))
    val screenStack: StateFlow<List<Screen>> = _screenStack

    val pairing = MutableStateFlow(PairingUi())

    val capabilities = MutableStateFlow(BootstrapInfo())

    private val _home = MutableStateFlow(HomeData())
    val home: StateFlow<HomeData> = _home

    /** 浏览页（Browse）分页数据，key = type|tag|query|series。 */
    val browsePages = MutableStateFlow<Map<String, MediaPage>>(emptyMap())

    /** 网格滚动位置（key 同 browseKey + "home"）：返回本页时恢复，不从头翻起。 */
    private val _gridScroll = MutableStateFlow<Map<String, Int>>(emptyMap())

    fun saveGridScroll(key: String, index: Int) {
        _gridScroll.value = _gridScroll.value + (key to index)
    }

    fun gridScrollOf(key: String): Int = _gridScroll.value[key] ?: 0

    /** 首载/分页/详情加载失败标记（UI 呈现错误态 + 重试，不再无限转圈）。 */
    val browseErrors = MutableStateFlow<Set<String>>(emptySet())
    private val browseLoading = MutableStateFlow<Set<String>>(emptySet())
    val browseMoreFailed = MutableStateFlow<Set<String>>(emptySet())
    val detailError = MutableStateFlow(false)

    /** 系列聚合（TV 按合集浏览）。 */
    private val _collections =
        MutableStateFlow<org.kindo.tv.core.CollectionsResp?>(null)
    val collections: StateFlow<org.kindo.tv.core.CollectionsResp?> = _collections

    private val _detail = MutableStateFlow<MediaDetail?>(null)
    val detail: StateFlow<MediaDetail?> = _detail

    /** D-pad 播放被 Policy 拒绝时的儿童提示（无绕过按钮，POL-009）。 */
    val denyMessage = MutableStateFlow<String?>(null)

    val conversation = MutableStateFlow(ConversationState())
    val transition = MutableStateFlow(TransitionUi())

    /** 自然播完后的下一集（显式按钮触发，同 D-pad 点播走 play 校验；PLY-007）。 */
    val nextEpisode = MutableStateFlow<EpisodeInfo?>(null)

    var conversationSessionId: String? = null
        private set
    private var followUpSeconds = 6
    private var followUpJob: Job? = null
    private var pairingJob: Job? = null
    private var transitionTickJob: Job? = null
    private var boundRetryJob: Job? = null
    private var boundRetryAttempt = 0
    private var capabilitiesJob: Job? = null
    private var voiceReconnectAttempts = 0
    private var duckedByConversation = false

    /** 播放失败可重试（网络/IO）；Policy 拒绝与解码失败不提供重试入口。 */
    val denyRetryable = MutableStateFlow(false)
    private var lastPlayRequest: Pair<String, Long>? = null

    /** 首页加载失败标记（首载/静默刷新失败时 UI 呈现错误态 + 重试）。 */
    val homeError = MutableStateFlow(false)

    val currentScreen: Screen get() = _screenStack.value.last()

    init {
        viewModelScope.launch {
            val binding = deviceStore.load()
            binding.deviceId?.let { deviceId = it }
            if (binding.baseUrl != null && binding.token != null) {
                if (binding.deviceId == null) deviceStore.updateDeviceId(deviceId)
                enterBoundState(binding.baseUrl, binding.token)
            } else {
                startDiscovery()
            }
        }
        wirePlaybackReporting()
    }

    private fun wirePlaybackReporting() {
        playbackController.eventSender = { eventId, playbackId, kind, positionMs ->
            realtime.sendPlaybackEvent(eventId, playbackId, kind, positionMs)
        }
        playbackController.trackChangedSender = { playbackId, audioId, subtitleId ->
            realtime.sendTrackChanged(playbackId, audioId, subtitleId)
        }
        playbackController.onPlayerError = { code ->
            // §1.2 direct play 兜底：解码/封装不支持 → 儿童可懂的"换一个"文案
            val decoderFailure = code.startsWith("ERROR_CODE_DECODER") ||
                code.contains("DECODING_") || code.contains("PARSING_") ||
                code.contains("FORMAT_EXCEEDS_CAPABILITIES")
            // 网络/IO 类失败提供"再试一次"；解码失败重试无意义
            denyRetryable.value = !decoderFailure
            denyMessage.value = if (decoderFailure) {
                "这台电视放不了这个视频，我们换个内容吧"
            } else {
                "播放出了点问题（$code），稍后再试试"
            }
        }
        // 自然播完 → 计算下一集（显式按钮，Policy 由服务端权威判定；PLY-007）
        viewModelScope.launch {
            playbackController.playbackEnded.collect { ended ->
                if (ended) computeNextEpisode() else nextEpisode.value = null
            }
        }
    }

    private fun computeNextEpisode() {
        val mediaId = playbackController.nowPlaying.value.mediaId
        if (mediaId.isEmpty()) return
        viewModelScope.launch {
            val detail = hub.mediaDetail(mediaId) ?: return@launch
            val eps = detail.series?.episodes ?: return@launch
            val idx = eps.indexOfFirst { it.media_id == mediaId }
            nextEpisode.value = if (idx in 0 until eps.size - 1) eps[idx + 1] else null
        }
    }

    /** 播完页面点“下一集”：显式选择，同 D-pad 点播走 POST /playbacks 校验。 */
    fun playNextEpisode() {
        val ep = nextEpisode.value ?: return
        playFromUi(ep.media_id, 0L)
    }

    // ---------- 绑定与配对（§3.2 / 交互 §4.1） ----------

    private fun startDiscovery() {
        viewModelScope.launch {
            discovery.discover().collect { found ->
                val cur = pairing.value
                if (cur.stage == "discovering") {
                    pairing.value = cur.copy(
                        foundHubs = (cur.foundHubs + found).distinctBy { it.baseUrl },
                        statusText = if (cur.foundHubs.isEmpty()) "找到家庭媒体服务了！" else cur.statusText,
                    )
                }
            }
        }
    }

    /** 连接选中的 Hub（发现列表或手动地址），发起配对并展示 6 位码。 */
    fun startPairing(baseUrl: String) {
        pairingJob?.cancel()
        pairingJob = viewModelScope.launch {
            pairing.value = pairing.value.copy(stage = "connecting", statusText = "正在连接…")
            val pr = hub.createPairingRequest(baseUrl, deviceName(), deviceId) ?: run {
                pairing.value = pairing.value.copy(
                    stage = "error", statusText = "连接失败，请确认地址后重试",
                )
                return@launch
            }
            pairing.value = pairing.value.copy(
                stage = "awaiting", displayCode = pr.display_code,
                statusText = "请家长在管理后台「设备 / 配对」中核对屏幕上的数字并批准",
            )
            for (i in 1..75) {
                delay(4000)
                val status = hub.pollPairingStatus(baseUrl, pr.pairing_id, pr.pairing_secret) ?: continue
                when (status.state) {
                    "approved" -> {
                        val token = status.device_token
                        if (token.isNullOrEmpty()) continue
                        deviceStore.save(baseUrl, token, deviceId)
                        pairing.value = pairing.value.copy(stage = "approved", statusText = "连接成功！")
                        enterBoundState(baseUrl, token)
                        return@launch
                    }
                    "denied" -> {
                        pairing.value = pairing.value.copy(stage = "denied", statusText = "家长拒绝了这次连接")
                        return@launch
                    }
                    "expired" -> {
                        pairing.value = pairing.value.copy(stage = "expired", statusText = "配对码已过期，请重新连接")
                        return@launch
                    }
                }
            }
            pairing.value = pairing.value.copy(stage = "expired", statusText = "等待超时，请重新连接")
        }
    }

    fun showManualInput() {
        pairing.value = pairing.value.copy(manualInput = true)
    }

    fun retryDiscovery() {
        pairing.value = PairingUi()
        startDiscovery()
    }

    private fun deviceName(): String =
        "${android.os.Build.BRAND ?: "Kindo"} ${android.os.Build.MODEL ?: "TV"}"

    private suspend fun enterBoundState(base: String, token: String) {
        hub.configure(base, token)
        val info = hub.bootstrap()
        if (info == null) {
            // 已保存绑定但 Hub 不可达：静默自动重试（家庭网络抖动/NAS 重启
            // 不应把孩子打回配对流程）；多次失败后家长可显式选择重新配对
            scheduleBoundRetry(base, token)
            return
        }
        boundRetryJob?.cancel()
        boundRetryAttempt = 0
        capabilities.value = info
        _screenStack.value = listOf(Screen.Home)
        realtime.connect(base, token)
        playbackController.ensureStarted()
        loadHome()
        startCapabilitiesRefresh()
    }

    /** 绑定有效但 Hub 暂不可达：5s 间隔自动重试，停留在重试屏（交互 §10 不伪装为 AI 问题）。 */
    private fun scheduleBoundRetry(base: String, token: String) {
        boundRetryAttempt += 1
        pairing.value = PairingUi(
            stage = "reconnecting",
            statusText = "家庭媒体服务暂时连不上（重试第 $boundRetryAttempt 次），正在自动重连…",
        )
        _screenStack.value = listOf(Screen.Bootstrap)
        boundRetryJob?.cancel()
        boundRetryJob = viewModelScope.launch {
            delay(5000)
            enterBoundState(base, token)
        }
    }

    /** 重试无果时的家长出口：清除绑定，重走发现/配对流程。 */
    fun restartPairing() {
        boundRetryJob?.cancel()
        boundRetryAttempt = 0
        deviceStore.clear()
        pairing.value = PairingUi()
        _screenStack.value = listOf(Screen.Bootstrap)
        startDiscovery()
    }

    /** 能力状态周期刷新：LLM/ASR 中途宕机或恢复，语音入口状态实时跟随
     *  （此前仅启动拉取一次，"AI 睡觉啦"可能整晚陈旧）。 */
    private fun startCapabilitiesRefresh() {
        capabilitiesJob?.cancel()
        capabilitiesJob = viewModelScope.launch {
            while (true) {
                delay(30_000)
                hub.bootstrap()?.let { capabilities.value = it }
            }
        }
    }

    // ---------- 导航 ----------

    fun navigate(screen: Screen) {
        _screenStack.value = _screenStack.value + screen
    }

    /** 原地切换栈顶视图（筛选 chip 等）：返回栈不增长，← 直接回上一级。 */
    fun replace(screen: Screen) {
        val stack = _screenStack.value
        if (stack.isEmpty()) {
            _screenStack.value = listOf(screen)
            return
        }
        _screenStack.value = stack.dropLast(1) + screen
    }

    fun goBack(): Boolean {
        val stack = _screenStack.value
        if (stack.size <= 1) return false
        if (stack.last() is Screen.Player) {
            playbackController.stop()
        }
        _screenStack.value = stack.dropLast(1)
        return true
    }

    fun backToHome() {
        playbackController.stop()
        _screenStack.value = listOf(Screen.Home)
        loadHome()
    }

    // ---------- 首页 / 浏览 / 详情 ----------

    fun loadHome() {
        viewModelScope.launch {
            val h = hub.home()
            if (h != null) {
                _home.value = h
                homeError.value = false
            } else {
                // 首载失败完全静默是断头路：标记错误态供 UI 呈现重试
                // （已有数据时 UI 保留旧内容，只在空首页时显示错误卡）
                homeError.value = true
            }
        }
    }

    fun retryHome() {
        homeError.value = false
        loadHome()
    }

    fun loadCollections() {
        viewModelScope.launch {
            _collections.value = hub.collections() ?: _collections.value
        }
    }

    fun loadBrowse(type: String?, tag: String?, query: String? = null, seriesId: String? = null) {
        val key = browseKey(type, tag, query, seriesId)
        if (browsePages.value.containsKey(key)) return
        if (key in browseLoading.value) return
        browseLoading.value = browseLoading.value + key
        browseErrors.value = browseErrors.value - key
        viewModelScope.launch {
            val page = hub.mediaPage(type = type, tag = tag, query = query, seriesId = seriesId, limit = 30)
            browseLoading.value = browseLoading.value - key
            if (page != null) {
                browsePages.value = browsePages.value + (key to page)
            } else {
                browseErrors.value = browseErrors.value + key
            }
        }
    }

    /** 首载失败后的重试（清错误标记后重新加载）。 */
    fun retryBrowse(type: String?, tag: String?, query: String? = null, seriesId: String? = null) {
        val key = browseKey(type, tag, query, seriesId)
        browseErrors.value = browseErrors.value - key
        browseLoading.value = browseLoading.value - key
        loadBrowse(type, tag, query, seriesId)
    }

    fun loadMoreBrowse(
        type: String?,
        tag: String?,
        query: String? = null,
        seriesId: String? = null,
    ) {
        val key = browseKey(type, tag, query, seriesId)
        val cur = browsePages.value[key] ?: return
        val cursor = cur.next_cursor ?: return
        if (key in browseLoading.value) return
        browseLoading.value = browseLoading.value + key
        viewModelScope.launch {
            val page = hub.mediaPage(
                type = type, tag = tag, query = query, seriesId = seriesId,
                cursor = cursor, limit = 30,
            )
            browseLoading.value = browseLoading.value - key
            if (page != null) {
                browsePages.value = browsePages.value +
                    (key to MediaPage(cur.items + page.items, page.next_cursor))
                browseMoreFailed.value = browseMoreFailed.value - key
            } else {
                browseMoreFailed.value = browseMoreFailed.value + key
            }
        }
    }

    fun browseKey(
        type: String?,
        tag: String?,
        query: String? = null,
        seriesId: String? = null,
    ): String = "${type ?: "-"}|${tag ?: "-"}|${query ?: "-"}|${seriesId ?: "-"}"

    /** inPlace=true：详情页内切换集数/课时，原地换栈顶（返回栈不增长）。 */
    fun openDetail(mediaId: String, inPlace: Boolean = false) {
        _detail.value = null
        detailError.value = false
        if (inPlace) replace(Screen.Detail(mediaId)) else navigate(Screen.Detail(mediaId))
        loadDetail(mediaId)
    }

    /** 详情页重新激活时兜底加载（共享 _detail 可能已被其他集覆盖，否则永久转圈）。 */
    fun ensureDetail(mediaId: String) {
        if (_detail.value?.media_id == mediaId) return
        detailError.value = false
        loadDetail(mediaId)
    }

    /** 详情失败后的重试（不改变导航栈）。 */
    fun retryDetail(mediaId: String) {
        detailError.value = false
        loadDetail(mediaId)
    }

    private var detailRequestId = 0

    private fun loadDetail(mediaId: String) {
        val req = ++detailRequestId
        viewModelScope.launch {
            val d = hub.mediaDetail(mediaId)
            if (req != detailRequestId) return@launch // 已被更新的请求取代
            if (d != null) {
                _detail.value = d
            } else if (_detail.value?.media_id != mediaId) {
                detailError.value = true
            }
        }
    }

    // ---------- 播放（D-pad 与 AI 同一入口，统一过 Policy） ----------

    fun playFromUi(mediaId: String, startPositionMs: Long? = null) {
        lastPlayRequest = mediaId to (startPositionMs ?: 0L)
        viewModelScope.launch {
            when (val r = hub.requestPlayback(mediaId, "play", startPositionMs, source = "ui")) {
                // 断点续播契约（技术方案 v0.3 §3.4）：descriptor 不回传 start_position_ms，
                // seek 用发起请求时的值（修复历史断裂：此前恒从头播放）
                is PlayResult.Allow -> {
                    denyRetryable.value = false
                    startPlayback(r.playbackId, r.descriptor, startPositionMs ?: 0L)
                }
                is PlayResult.Deny -> {
                    denyRetryable.value = false
                    denyMessage.value = deniedText(r.reasonCode, r.constraints)
                }
                is PlayResult.Failure -> {
                    // 格式不兼容重试无意义；网络/服务类失败给"再试一次"
                    denyRetryable.value = r.code != "media_not_playable"
                    denyMessage.value = if (r.code == "media_not_playable")
                        "这台电视放不了这个视频，我们换个内容吧"
                    else "暂时播不了，稍后再试试"
                }
            }
        }
    }

    /** 播放失败弹窗的"再试一次"：按最后一次请求重走服务端校验。 */
    fun retryLastPlay() {
        dismissDeny()
        lastPlayRequest?.let { (mediaId, startMs) -> playFromUi(mediaId, startMs) }
    }

    private fun startPlayback(
        playbackId: String,
        dto: org.kindo.tv.core.StreamDescriptorDto,
        requestedStartMs: Long = 0L,
    ) {
        val descriptor = StreamDescriptor(
            playbackId = playbackId,
            mediaId = dto.media_id,
            url = dto.url,
            mimeType = dto.mime_type ?: "video/mp4",
            grant = dto.grant,
            durationMs = dto.duration_ms,
            startPositionMs = requestedStartMs,
        )
        val audioRefs = dto.audio_tracks.map { TrackRef(it.id, it.label ?: it.language ?: "音轨") }
        val subRefs = dto.subtitle_tracks.map { TrackRef(it.id, it.label ?: it.language ?: "字幕") }
        // 连续点播（ended 面板下一集 / AI playback.command）：已在播放器时原地
        // 替换栈顶，返回栈不叠加（此前每条命令压一层 Player，返回需逐层退）
        val stack = _screenStack.value
        _screenStack.value =
            if (stack.isNotEmpty() && stack.last() is Screen.Player) stack.dropLast(1) + Screen.Player
            else stack + Screen.Player
        val title = titleOf(dto.media_id)
        playbackController.play(
            descriptor, title, hub.deviceToken, hub.baseUrl, audioRefs, subRefs,
        )
        // 语音/接力发起的播放没有详情上下文，标题为空——异步补齐（否则播放器顶栏空白）
        if (title.isEmpty()) {
            viewModelScope.launch {
                hub.mediaDetail(dto.media_id)?.title?.takeIf { it.isNotEmpty() }
                    ?.let { playbackController.updateTitle(it) }
            }
        }
        conversation.value = conversation.value.copy(active = false)
    }

    private fun titleOf(mediaId: String): String =
        _detail.value?.takeIf { it.media_id == mediaId }?.title ?: ""

    // ---------- Conversation Overlay（交互 §5 状态机） ----------

    fun startConversation(resume: Boolean = false) {
        val caps = capabilities.value.capabilities
        if (!caps.voice_available || !caps.ai_available) {
            // 交互 §7.5：AI 不可用时入口给出反馈而非静默（首页按钮已禁用，
            // Detail/Player 的问AI入口由此兜底；语音链路需要 ASR 与 LLM 同时可用）
            conversation.value = ConversationState(
                active = true, phase = "error",
                retryHint = "AI 暂时休息，先自己挑着看吧",
            )
            return
        }
        viewModelScope.launch {
            // 接力互动内继续对话 → 恢复既有会话（AI-010/AI-011：上下文不重置）
            val resumeId = if (resume) conversationSessionId else null
            val created = hub.createConversation(resumeId, uiContextJson()) ?: run {
                conversation.value = ConversationState(active = true, phase = "error")
                return@launch
            }
            conversationSessionId = created.session_id
            followUpSeconds = created.follow_up_seconds
            duckedByConversation = currentScreen is Screen.Player && playbackController.isPlaying.value
            if (duckedByConversation) playbackController.duck()
            conversation.value = ConversationState(active = true, phase = "listening")
            startListening()
        }
    }

    private fun startListening() {
        val sid = conversationSessionId ?: return
        voice.captureAndSend(hub.baseUrl, hub.deviceToken, sid) { }
    }

    /** 会话上下文随实际页面（此前恒 home——LLM 不知道孩子在哪个页面）。 */
    private fun uiContextJson(): String = when (val s = currentScreen) {
        is Screen.Home -> """{"screen":"home"}"""
        is Screen.Browse -> """{"screen":"browse","title":${jsonStr(s.title)}}"""
        is Screen.Detail -> """{"screen":"detail","media_id":${jsonStr(s.mediaId)}}"""
        is Screen.Search -> """{"screen":"search"}"""
        is Screen.Player -> """{"screen":"player"}"""
        Screen.Bootstrap -> """{"screen":"bootstrap"}"""
    }

    private fun jsonStr(raw: String): String =
        "\"" + raw.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

    /** 语音 WS 意外断开：会话仍在听/转写则自动重连（≤2 次），超限转错误态。
     *  回调来自 OkHttp 线程，StateFlow 线程安全，launch 切主线程协程。 */
    private fun onVoiceDropped() {
        val conv = conversation.value
        if (!conv.active) return
        if (conv.phase != "listening" && conv.phase != "transcribing") return
        if (voiceReconnectAttempts < 2) {
            voiceReconnectAttempts += 1
            viewModelScope.launch {
                delay(1200)
                if (conversation.value.active && conversationSessionId != null &&
                    conversation.value.phase == "listening"
                ) {
                    startListening()
                }
            }
        } else {
            conversation.value = conv.copy(
                phase = "error",
                retryHint = "网络断了一下，等会儿再和我说吧",
            )
        }
    }

    /** SPEAKING 期间按 AI 键：中断 TTS 并立即回到 LISTENING（交互 §5）。 */
    fun interruptSpeaking() {
        tts.stop()
        hubTts.stop() // hub_tts（家长声音克隆，§6.7）与系统 TTS 同打断语义
        conversationSessionId?.let { currentTtsId?.let { tid -> realtime.sendTts(tid, "interrupted") } }
        conversation.value = conversation.value.copy(phase = "listening", options = emptyList())
        voice.openStream()
    }

    private var currentTtsId: String? = null

    // 分句流式播报（技术方案 §11.4）：一回合并发多条 tts.request 逐句排队；
    // 仅当"回合文本已完结（assistant.text.final）+ 排队全部播完"才进入追问窗口
    private val pendingTtsIds = mutableSetOf<String>()
    private var turnTextDone = false

    fun endConversation() {
        voice.close()
        tts.stop()
        hubTts.stop()
        followUpJob?.cancel()
        pendingTtsIds.clear()
        turnTextDone = false
        val sid = conversationSessionId
        conversationSessionId = null
        currentTtsId = null
        conversation.value = ConversationState()
        if (duckedByConversation) {
            playbackController.unduck()
            duckedByConversation = false
        }
        if (sid != null) viewModelScope.launch { hub.endConversation(sid) }
    }

    fun selectOption(optionId: String) {
        val sid = conversationSessionId ?: return
        realtime.sendSelection(sid, optionId)
        conversation.value = conversation.value.copy(options = emptyList())
    }

    private fun onRealtimeEvent(event: Map<String, Any?>) {
        android.util.Log.i("KindoVM", "realtime event: ${event["type"]}")
        when (event["type"]) {
            "conversation.state" -> {
                val state = (event["payload"] as? Map<*, *>)?.get("state") as? String ?: return
                if (!conversation.value.active) return
                if (state == "thinking") {
                    // 新回合开始：清空上一回合的播报收尾状态
                    pendingTtsIds.clear()
                    turnTextDone = false
                }
                conversation.value = conversation.value.copy(phase = state)
                if (state == "listening") voice.openStream()
            }
            "asr.final" -> {
                val payload = event["payload"] as? Map<*, *> ?: return
                val text = payload["text"] as? String ?: ""
                val retry = payload["retry_hint"] as? String ?: ""
                if (text.isNotBlank()) {
                    conversation.value = conversation.value.copy(asrText = text)
                } else if (retry.isNotBlank()) {
                    conversation.value = conversation.value.copy(retryHint = retry)
                }
            }
            "assistant.text.delta", "assistant.text.final" -> {
                val payload = event["payload"] as? Map<*, *> ?: return
                val delta = payload["delta"] as? String
                val text = payload["text"] as? String
                val cur = conversation.value
                conversation.value = when {
                    // 分句流式期间 delta 与 tts.request 交错到达，不把 speaking 打回 thinking
                    delta != null -> cur.copy(
                        aiText = cur.aiText + delta,
                        phase = if (cur.phase == "speaking") "speaking" else "thinking",
                    )
                    text != null -> cur.copy(aiText = text)
                    else -> return
                }
                if (text != null) {
                    // final 是"本回合 tts.request 已发完"的信号（Hub 保证顺序）
                    turnTextDone = true
                    maybeFinishSpeaking()
                }
            }
            "tool.started" -> {
                val payload = event["payload"] as? Map<*, *> ?: return
                val status = payload["child_friendly_status"] as? String ?: "正在处理…"
                conversation.value = conversation.value.copy(phase = "tool_running", toolStatus = status)
            }
            "tts.request" -> {
                val payload = event["payload"] as? Map<*, *> ?: return
                val ttsId = payload["tts_id"] as? String ?: return
                val text = payload["text"] as? String ?: return
                // 会话已被用户结束（不聊了）：迟到的响应不再出声，避免盖在已开始的播放上。
                // 语音点播的确认播报（startPlayback 关对话但保留 sessionId，配合 duck）不受影响
                if (!conversation.value.active && conversationSessionId == null) return
                currentTtsId = ttsId
                pendingTtsIds.add(ttsId)
                conversation.value = conversation.value.copy(phase = "speaking")
                if (duckedByConversation) playbackController.duck()
                // 事件回报两条路径完全一致（started/finished/interrupted）；
                // finished 仅末句（last_tts_id）驱动追问窗口，来源无关
                val onTtsEvent: (String) -> Unit = { kind ->
                    when (kind) {
                        "started" -> Unit
                        "finished" -> {
                            realtime.sendTts(ttsId, "finished")
                            pendingTtsIds.remove(ttsId)
                            maybeFinishSpeaking()
                        }
                        "interrupted" -> {
                            realtime.sendTts(ttsId, "interrupted")
                            pendingTtsIds.clear()
                            conversation.value = conversation.value.copy(phase = "listening")
                        }
                    }
                }
                val audioPath = payload["audio_path"] as? String
                if (audioPath != null) {
                    hubTts.speak(ttsId, audioPath, text, onTtsEvent)
                } else {
                    tts.speak(ttsId, text, onTtsEvent)
                }
            }
            "clarification.options" -> {
                val payload = event["payload"] as? Map<*, *> ?: return
                val options = (payload["options"] as? List<*>).orEmpty().mapNotNull { o ->
                    (o as? Map<*, *>)?.let {
                        OptionItem(
                            id = it["option_id"] as? String ?: return@mapNotNull null,
                            label = it["label"] as? String ?: "",
                        )
                    }
                }
                conversation.value = conversation.value.copy(options = options)
            }
            "playback.command" -> {
                val payload = event["payload"] as? Map<*, *> ?: return
                when (payload["action"]) {
                    "start" -> {
                        val playbackId = payload["playback_id"] as? String ?: return
                        val raw = payload["stream_descriptor"] as? String ?: return
                        val dto = parseDescriptor(raw) ?: return
                        startPlayback(playbackId, dto)
                    }
                    "stop" -> playbackController.stop()
                }
            }
            "policy.denied" -> {
                val payload = event["payload"] as? Map<*, *> ?: return
                val reason = payload["reason_code"] as? String ?: "policy_denied"
                // constraints 嵌套对象被 RealtimeClient toString 透传 → 字符串解析
                val constraints = (payload["constraints"] as? String)?.let { parseConstraints(it) }
                conversation.value = conversation.value.copy(
                    aiText = deniedText(reason, constraints), phase = "follow_up",
                )
            }
            "transition.offer" -> {
                val payload = event["payload"] as? Map<*, *> ?: return
                val tid = payload["transition_id"] as? String ?: return
                val opening = payload["opening_text"] as? String ?: ""
                // RealtimeClient 将嵌套 JSONArray 统一 toString() 透传（与
                // stream_descriptor 同一机制），此处兼容字符串与列表两种形态
                val rawOptions: List<*> = when (val o = payload["options"]) {
                    is List<*> -> o
                    is String -> runCatching {
                        org.json.JSONArray(o).let { arr ->
                            (0 until arr.length()).map { arr.getJSONObject(it) }
                        }
                    }.getOrDefault(emptyList<Any>())
                    else -> emptyList<Any>()
                }
                val opts = rawOptions.mapNotNull { el ->
                    val o = el as? org.json.JSONObject ?: return@mapNotNull null
                    val t = o.optString("type")
                    if (t.isEmpty()) null else TransitionOption(t, o.optString("label", t))
                }
                val deadlineMs = parseIsoMs(payload["deadline_ts"] as? String)
                transition.value = TransitionUi(
                    transitionId = tid, phase = "offer",
                    openingText = opening, options = opts,
                    deadlineMs = deadlineMs,
                    remainingSeconds = if (deadlineMs > 0)
                        ((deadlineMs - System.currentTimeMillis()) / 1000).toInt() else -1)
                denyMessage.value = null // offer 已承接"时间到"语义，不叠加拒绝弹窗
                // AI 主动开口的语音形态（交互 §5.2：开场白朗读；本地 TTS，离线安全）
                if (opening.isNotBlank() && !conversation.value.active) {
                    tts.speak("transition-$tid", opening) { }
                }
                startTransitionCountdown(deadlineMs)
            }
            "transition.state" -> {
                val payload = event["payload"] as? Map<*, *> ?: return
                when (payload["state"]) {
                    "interaction" -> {
                        transition.value = transition.value.copy(phase = "interaction")
                        // 接力互动 = 与普通对话一致的听/想/说循环：自动开麦
                        //（此前仅静态提示，孩子不按 🎤 就没人听）
                        if (!conversation.value.active) startConversation(resume = true)
                    }
                    "offscreen" -> transition.value = transition.value.copy(phase = "offscreen")
                    "ended" -> {
                        val reason = payload["ended_reason"] as? String
                        onTransitionEnded(reason)
                    }
                }
            }
            "transition.activity" -> {
                val payload = event["payload"] as? Map<*, *> ?: return
                val act: org.json.JSONObject? = when (val a = payload["activity"]) {
                    is org.json.JSONObject -> a
                    is String -> runCatching { org.json.JSONObject(a) }.getOrNull()
                    else -> null
                }
                if (act == null) return
                transition.value = transition.value.copy(
                    phase = "offscreen",
                    activity = TransitionActivity(
                        act.optString("title", ""),
                        act.optString("summary", "")))
            }
            "transition.ended" -> {
                val payload = event["payload"] as? Map<*, *> ?: return
                onTransitionEnded(payload["ended_reason"] as? String)
            }
            "sync.required" -> {
                // 库结构变化（来源删除等）：清空已缓存的浏览/合集/详情数据，
                // 重新拉首页（否则已加载的海报墙残留到 App 重启）
                browsePages.value = emptyMap()
                browseErrors.value = emptySet()
                browseMoreFailed.value = emptySet()
                _collections.value = null
                _detail.value = null
                nextEpisode.value = null
                org.kindo.tv.ui.PosterCache.invalidateAll()
                loadHome()
            }
        }
    }

    /** ISO8601（含/不含偏移）→ epoch ms；解析失败返回 0。 */
    private fun parseIsoMs(raw: String?): Long {
        if (raw.isNullOrEmpty()) return 0L
        return runCatching {
            java.time.OffsetDateTime.parse(raw).toInstant().toEpochMilli()
        }.recoverCatching {
            java.time.Instant.parse(raw).toEpochMilli()
        }.getOrDefault(0L)
    }

    /** 时间盒客户端兜底倒计时（交互 §10）：到点本地收尾，不依赖 Hub tick 送达。 */
    private fun startTransitionCountdown(deadlineMs: Long) {
        transitionTickJob?.cancel()
        if (deadlineMs <= 0) return
        transitionTickJob = viewModelScope.launch {
            while (true) {
                val remain = ((deadlineMs - System.currentTimeMillis()) / 1000).toInt()
                val t = transition.value
                if (t.phase == "idle" || t.phase == "ended") return@launch
                transition.value = t.copy(remainingSeconds = remain.coerceAtLeast(0))
                if (remain <= 0) {
                    // 客户端兜底收尾：宁可提前结束不可无限延长（Hub tick 15s 粒度）
                    if (t.phase == "offer" || t.phase == "interaction") {
                        endConversation()
                        endTransitionUi("timeout")
                    }
                    return@launch
                }
                delay(1000)
            }
        }
    }

    private fun onTransitionEnded(reason: String?) {
        val wasAudioHandoff = reason == "audio_handoff"
        endTransitionUi(reason)
        if (wasAudioHandoff) scheduleAudioHandoffFallback()
    }

    /** 接力 audio handoff 的 REST 兜底：WS command 丢失时对齐 Hub 侧会话。 */
    private fun scheduleAudioHandoffFallback() {
        viewModelScope.launch {
            delay(3500)
            val local = playbackController.nowPlaying.value.playbackId
            val current = hub.currentPlayback() ?: return@launch
            if (current.playback_id.isNotEmpty() && current.playback_id != local) {
                val re = hub.regrantPlayback(current.playback_id) ?: return@launch
                startPlayback(re.playback_id, re.stream_descriptor)
            }
        }
    }

    /** 接力结束：一句温和收尾短暂呈现（交互 §5.2 TRANSITION_ENDED），audio_handoff 无感切换。 */
    private fun endTransitionUi(reason: String? = null) {
        transitionTickJob?.cancel()
        // 本地已结束（拒绝/时间盒兜底）后 Hub 回声不再重复呈现
        if (transition.value.phase == "ended") return
        val closing = when (reason) {
            "rejected" -> "好吧，那我们下次再聊！"
            "timeout" -> "今天到这里，明天见！"
            "accepted_completed" -> "太棒啦！去玩吧！"
            "audio_budget_denied" -> "今天听的时间也到啦，明天再听吧"
            "audio_handoff" -> ""  // 无感切到音频播放页
            else -> "今天到这里，明天见！"
        }
        if (closing.isEmpty()) {
            transition.value = TransitionUi()
            return
        }
        transition.value = transition.value.copy(phase = "ended", closingText = closing)
        viewModelScope.launch {
            delay(4200)
            if (transition.value.phase == "ended") {
                transition.value = TransitionUi()
            }
        }
    }

    private fun parseDescriptor(raw: String): org.kindo.tv.core.StreamDescriptorDto? =
        org.kindo.tv.core.ModelsJson.parseDescriptor(raw)

    private fun parseConstraints(raw: String): org.kindo.tv.core.PlayConstraints? =
        org.kindo.tv.core.ModelsJson.parseConstraints(raw)

    /** 分句流式播报收尾门控：回合文本已完结（assistant.text.final 已达，Hub 保证
     *  此时本回合 tts.request 全部下发）且排队语句全部播完，才进入追问窗口。
     *  中间句完成、或 final 先于尾句到达时，都不触发。 */
    private fun maybeFinishSpeaking() {
        if (!turnTextDone) return
        if (pendingTtsIds.isNotEmpty()) return
        if (conversation.value.phase != "speaking") return
        // 接力互动内：TTS 完继续听（时间盒内保持听/想/说循环，不进追问关闭窗口）
        if (transition.value.phase == "interaction") {
            conversation.value = conversation.value.copy(phase = "listening")
            voice.openStream()
            return
        }
        conversation.value = conversation.value.copy(phase = "follow_up")
        followUpJob?.cancel()
        followUpJob = viewModelScope.launch {
            delay(followUpSeconds * 1000L)
            if (conversation.value.phase == "follow_up") {
                conversation.value = conversation.value.copy(active = false)
                // 追问窗口结束：UI 收起即停麦（硬性约束 5：仅 LISTENING/FOLLOW_UP
                // 期间采集；此前不关麦不关 WS，音频持续上传到服务端空闲超时）
                voice.close()
                if (duckedByConversation) {
                    playbackController.unduck()
                    duckedByConversation = false
                }
            }
        }
    }

    /** 维度化拒绝文案（交互 v0.3 §6/§7.3）：按媒介/分类/剩余量区分，
     * 拒绝反馈不依赖 LLM。 */
    private fun deniedText(
        reasonCode: String,
        constraints: org.kindo.tv.core.PlayConstraints? = null,
    ): String {
        val allowed = constraints?.allowed_modalities ?: emptyList()
        val modality = constraints?.modality
        val remaining = constraints?.remaining
        return when (reasonCode) {
            "daily_limit_reached" -> when {
                // 音频预算尽：听觉也到界（§6 音频文案）
                modality == "AUDIO" -> "今天听的时间到啦，明天再听吧"
                // 总屏耗尽：一切视频停止（§7.3 总屏幕文案）
                remaining?.screen_total_seconds == 0L ->
                    if ("audio" in allowed) "今天屏幕时间用完啦，还可以听故事和儿歌哦！"
                    else "今天屏幕时间用完啦，我们明天再看好不好？"
                // 娱乐子预算尽而学习视频仍允许（§7.3 分类文案）
                remaining?.video_class_seconds == 0L &&
                    constraints?.content_class == "ENTERTAINMENT" ->
                    "今天的动画时间看完啦，想看科普的还能再看一集哦"
                "audio" in allowed -> "今天的动画时间用完啦，还可以听故事和儿歌哦！"
                else -> "今天的观看时间用完啦，我们明天再看好不好？"
            }
            "outside_allowed_window" -> "到睡觉时间啦，明天见！"
            "session_limit_reached" -> "这次看了很久啦，先休息一下吧。"
            "episode_limit_reached" -> "今天的集数看完啦。"
            "autoplay_disabled" -> "想看下一集的话，跟我说一声就好。"
            else -> "现在不能播放这个内容。"
        }
    }

    /** 详情页 Policy 预检不允许时的本地儿童提示（LLM 不参与，交互 §10；维度化 §7.3）。 */
    fun deniedReasonText(
        reasonCode: String?,
        constraints: org.kindo.tv.core.PlayConstraints? = null,
    ): String = deniedText(reasonCode ?: "policy_denied", constraints)

    fun dismissDeny() {
        denyMessage.value = null
    }

    fun selectTransitionOption(optionType: String) {
        val tid = transition.value.transitionId
        if (tid.isNotEmpty()) {
            realtime.sendTransitionSelect(tid, optionType)
            transition.value = transition.value.copy(phase = "interaction")
        }
    }

    fun rejectTransition() {
        val tid = transition.value.transitionId
        if (tid.isNotEmpty()) realtime.sendTransitionReject(tid)
        endTransitionUi("rejected")
    }

    fun finishTransitionActivity() {
        val tid = transition.value.transitionId
        if (tid.isNotEmpty()) realtime.sendTransitionActivityDone(tid)
        endTransitionUi("accepted_completed")
    }

    private fun endTransitionUi() {
        transition.value = TransitionUi()
    }

    fun shutdown() {
        boundRetryJob?.cancel()
        capabilitiesJob?.cancel()
        voice.close()
        tts.shutdown()
        hubTts.release()
        playbackController.release()
        realtime.close()
    }

    companion object {
        fun factory(context: android.content.Context): ViewModelProvider.Factory =
            object : ViewModelProvider.Factory {
                @Suppress("UNCHECKED_CAST")
                override fun <T : ViewModel> create(modelClass: Class<T>): T {
                    return AppViewModel(context.applicationContext as Application) as T
                }
            }
    }
}
