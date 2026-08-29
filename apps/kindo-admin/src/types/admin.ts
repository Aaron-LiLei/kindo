/**
 * Admin API 契约类型。字段名与结构以《技术方案设计》§3.3 与 Hub `kindo/api/admin.py` 实现为准，
 * 一律 snake_case，前端不得改名或"优化"协议字段。
 */

/** 认证入口状态机（GET /auth/state，免认证只读）：前端渲染初始化/登录/应用的唯一依据 */
export interface AuthState {
  phase: 'setup_required' | 'ready'
  authenticated: boolean
  username?: string | null
}

/** 媒体所属系列（admin 列表批量序列化；episode 类型媒体才有） */
export interface MediaSeries {
  series_id: string
  title: string
  season_no: number
  episode_no: number
}

/** 媒体所属课程（lesson 类型媒体才有） */
export interface MediaCourse {
  course_id: string
  title: string
  chapter_no: number
  lesson_no: number
}

export interface MediaItem {
  media_id: string
  title: string
  media_type: string
  mount_id: string
  path_key: string
  duration_ms: number
  language: string | null
  age_band: string | null
  tags: { characters?: string[]; themes?: string[]; tags?: string[] }
  playable: boolean
  missing: boolean
  metadata_version: number
  parent_edited: boolean
  /** 扫描期缩略海报是否就绪（GET /admin/media/{id}/poster） */
  has_poster: boolean
  size_bytes: number
  /** 归组由目录结构自动推断（sidecar/家长修正声明后自动让位） */
  auto_grouped?: boolean
  series: MediaSeries | null
  course: MediaCourse | null
  /** v0.3 内容目录（Canonical 面板入口 / 维度徽章） */
  entity_id?: string | null
  content_class?: string | null
  modality?: string | null
  /** 来源显示名（无匹配回退 mount_id） */
  mount_label?: string
}

export interface MediaListParams {
  type?: string
  language?: string
  tag?: string
  series_id?: string
  course_id?: string
  cursor?: string
  limit?: number
  sort?: 'added' | 'title'
}

export interface MediaListResp {
  items: MediaItem[]
  next_cursor: string | null
  /** 库内实际存在的类型分布（筛选项按内容派生） */
  type_counts?: Record<string, number>
}

/** 合集聚合的补充信息（网络源跳过探测时 duration 全 0，需要其他信息维度） */
interface CollectionExtras {
  /** 成员 age_band 众数（无则 null） */
  age_band: string | null
  /** 成员角色/主题出现频次 top4 */
  tags: string[]
  /** 成员文件体积合计（字节） */
  size_bytes: number
  /** 来源挂载（按成员数降序） */
  mounts: { mount_id: string; label: string }[]
}

/** 系列/课程聚合（GET /admin/collections，媒体库"按合集浏览"） */
export interface SeriesCollection extends CollectionExtras {
  series_id: string
  title: string
  language: string | null
  count: number
  duration_ms: number
  cover_media_id: string | null
  cover_has_poster: boolean
  /** v0.3：系列实体锚点 / Series poster / 身份匹配状态（MED-013/ADM-012） */
  entity_id?: string
  match_status?: string
  entity_poster?: boolean
  matched_title?: string | null
}

export interface CourseCollection extends CollectionExtras {
  course_id: string
  title: string
  language: string | null
  count: number
  duration_ms: number
  cover_media_id: string | null
  cover_has_poster: boolean
}

export interface CollectionsResp {
  series: SeriesCollection[]
  courses: CourseCollection[]
}

/** POST /admin/media/auto-group：本地重算自动归组的统计结果（零值键可能缺失） */
export interface AutoGroupRebuildResp {
  processed?: number
  grouped?: number
  rebound?: number
  released?: number
  cleared?: number
  kept?: number
  note?: string
}

export interface MediaPatchBody {
  title?: string
  language?: string
  age_band?: string
  characters?: string[]
  themes?: string[]
  tags?: string[]
  /** 归组（家长修正通道，重扫不覆盖）；省略 = 不修改，name 为 null = 解除归组 */
  series?: { name?: string | null; season_no?: number; episode_no?: number } | null
  course?: { name?: string | null; chapter_no?: number; lesson_no?: number } | null
}

export interface MountRoot {
  root_id: string
  path: string
  read_only: boolean
  subdirectories: string[]
  /** 2026-08-25 决策：外层根纳入页面管理（显示名/启停/移除引用可恢复） */
  label?: string | null
  active?: boolean
  removed?: boolean
}

export interface Mount {
  mount_id: string
  root_id: string
  sub_path: string
  /** 本地来源：服务器/容器内绝对路径（2026-08-25 全页面化） */
  path?: string | null
  /** 探测策略（网络源） */
  probe_mode?: ProbeMode
  label: string
  read_only: boolean
  active: boolean
  source: string
  mount_type: 'local' | 'smb' | 'webdav' | string
  deleted?: boolean
  storage_mount_id?: string
  config?: Record<string, unknown>
  credentials_configured?: boolean
}

export interface MountsPayload {
  /** 2026-08-25 全页面化：不再有部署配置根；字段保留为可选（旧后端兼容） */
  roots?: MountRoot[]
  mounts: Mount[]
  scan_targets: string[]
  note: string
}

export type ProbeMode = 'range' | 'skip' | 'full'

export interface MountCreateBody {
  mount_type: 'local' | 'smb' | 'webdav'
  label?: string
  /** 本地：服务器/容器内绝对路径 */
  path?: string
  /** 媒体探测策略（网络源）：range=只取元数据字节（推荐）/skip/full */
  probe_mode?: ProbeMode
  host?: string
  port?: number
  share?: string
  url?: string
  /** 网络源子路径（本地源的 path 语义不同，拆开字段） */
  net_path?: string
  username?: string
  password?: string
}

export interface ScanJob {
  id: string
  mount_id: string
  /** 挂载显示名（无匹配时回退 mount_id） */
  label?: string
  /** 统一状态机：queued | running | done | failed | interrupted（Hub 重启标记） */
  state: 'queued' | 'running' | 'done' | 'failed' | 'interrupted' | (string & {})
  progress: number
  error_summary: string | null
  started_at: string | null
  finished_at: string | null
}

/** GET /admin/scan-jobs（扫描历史）与 GET /admin/scan-jobs/{id} 同构 */
export interface ScanJobsResp {
  jobs: ScanJob[]
}

/** GET /admin/media-mounts/health：并行短超时的健康探测结果 */
export interface MountHealth {
  mount_id: string
  healthy: boolean
  error?: string
  [key: string]: unknown
}

export interface HealthData {
  hub: { version: string; time: string }
  database: { ready: boolean }
  media: {
    /** 首跑引导检查清单数据（可选字段：旧 Hub 不返回） */
    total?: number
    match_pending?: number
    mounts: { mount_id: string; label?: string; healthy: boolean; read_only: boolean }[]
    latest_jobs: {
      id: string
      mount_id: string
      label?: string
      state: string
      progress: number
      error_summary: string | null
      finished_at: string | null
    }[]
  }
  asr: { status: string; ready: boolean; model: string | null }
  llm_providers: { provider_id: string; display_name: string; model: string; configured: boolean }[]
  active_model: { provider_id: string | null }
  devices: {
    device_id: string
    name: string
    status: string
    online: boolean
    last_seen_at: string | null
  }[]
}

/** 服务端可能携带并要求整体回传的未知字段一并保留（PUT /policy 的 Body 即规则 JSON 全量） */
export interface PolicyRules {
  daily_limit_minutes: number | null
  session_limit_minutes: number | null
  daily_episode_limit: number | null
  allowed_windows: { start: string; end: string }[]
  content_scope: {
    allowed_media_types?: string[] | null
    allowed_mount_ids?: string[] | null
    blocked_tags?: string[] | null
  }
  autoplay: boolean
  course_counts_as_entertainment: boolean
  [key: string]: unknown
}

export interface PolicyResp {
  version: number
  rules: PolicyRules
}

export interface PolicyPutResp extends PolicyResp {
  revoked_playbacks: number
  note: string
}

export interface Provider {
  provider_id: string
  display_name: string
  protocol: string
  model: string
  base_url: string
  /** config = 配置文件声明 | page = 后台页面添加（优先于同名配置项） */
  source: 'config' | 'page' | string
  /** 停用开关：停用=不参与会话解析（密钥保留）；旧 Hub 不返回时按 true 处理 */
  enabled?: boolean
  api_key_configured: boolean
  api_key_hint: string | null
  base_url_configured: boolean
  active: boolean
}

export interface ProviderBody {
  display_name: string
  protocol: string
  base_url: string
  model: string
  api_key?: string
  /** 停用开关：缺省=不修改（停用保留密钥，区别于删除） */
  enabled?: boolean
}

export interface ProviderTestResp {
  result: 'ok' | 'auth_failed' | 'unreachable' | 'error' | (string & {})
  detail?: string
}

export interface AnalyticsData {
  period: string
  total_watched_seconds: number
  by_media_type: Record<string, number>
  by_language: Record<string, number>
  /** v0.3 正交维度（ANA-002 按媒介/分类） */
  by_modality?: Record<string, number>
  by_content_class?: Record<string, number>
  top_media: { title: string; media_type: string; watched_seconds: number }[]
  top_series: { title: string; watched_seconds: number }[]
  /** 观看记录明细（C-2）：最近 20 条播放会话 */
  recent_records?: {
    title: string
    media_type: string
    modality: string | null
    content_class: string | null
    started_at: string
    watched_seconds: number
    completed: boolean
  }[]
  note: string
}

/** Canonical 实体（ADM-003）：字段值 + 来源 + locked 分离展示 */
export interface CanonicalEntity {
  entity_id: string
  entity_type: string
  parent_id: string | null
  parent_title: string | null
  match_status: string
  ordering: string | null
  duration_ms: number
  fields: Record<
    string,
    {
      value: unknown
      source: string
      source_label: string
      locked: boolean
      updated_at: string | null
    }
  >
  provenance_levels: string[]
  note: string
}

/** Artwork（ADM-013）：poster / backdrop / thumbnail / logo */
export interface ArtworkItem {
  kind: string
  exists: boolean
  source: string | null
  locked: boolean
  updated_at: string | null
}

/** Policy 今日剩余预览（交互 §8.1） */
export interface PolicyUsage {
  policy_version: number
  video_entertainment: Record<string, number | undefined>
  video_learning: Record<string, number | undefined>
  audio: Record<string, number | undefined>
  ai_voice: Record<string, number | undefined>
  transition_offered_today: number
  transition_daily_limit: number
  note: string
}

export interface Device {
  device_id: string
  name: string
  status: string
  online: boolean
  paired_at: string
  last_seen_at: string | null
}

export interface PendingPairing {
  pairing_id: string
  device_name: string
  app_instance_id: string
  display_code: string
  expires_at: string
  expired: boolean
  capabilities: Record<string, unknown>
}

export interface ScrapeConfigResp {
  provider: string
  base_url: string
  image_base_url: string
  language: string
  api_key_configured: boolean
}

export interface ScrapeStatusResp {
  state: 'idle' | 'running' | 'done' | 'failed'
  total: number
  done: number
  matched: number
  no_hit: number
  failed: number
  current: string
  started_at: string
  finished_at: string
  log_tail: string[]
}

// ---------- v0.3：身份匹配 / 兴趣信号 / 活动库 ----------

export interface MatchCandidate {
  ref_id: string
  title: string
  original_title?: string
  first_air_date?: string
  poster_path?: string
  popularity?: number
  confidence?: string
}

export interface PendingMatch {
  entity_id: string
  entity_type: string
  title: string
  match_status: string
  candidates: MatchCandidate[]
}

export interface NoCandidateItem {
  entity_id: string
  entity_type: string
  title: string
}

export interface MatchOverview {
  /** 已检索但 TMDB 无候选（待家长标记无匹配） */
  no_candidates?: NoCandidateItem[]
  counts: Record<string, number>
  pending: PendingMatch[]
}

export interface MatchDecisionRow {
  provider: string
  candidate: MatchCandidate | null
  confidence: string
  decision: string
  decided_by: string
  created_at: string
}

/** 全局决策时间线（GET /admin/match/decisions/recent，ADM-012 审计视图）。 */
export interface RecentMatchDecision extends MatchDecisionRow {
  entity_id: string
  entity_title: string
  entity_type: string
}

/** 实体文件版本（GET /admin/content/{id}/assets，PLY-009 多版本管理）。 */
export interface EntityAssetRow {
  asset_id: string
  media_id: string
  role: string // PRIMARY_VIDEO | ALTERNATE_VIDEO
  title: string
  path_key: string
  size_bytes: number
  duration_ms: number
  playable: boolean
  missing: boolean
}

export interface InterestAnalytics {
  period: string
  signal_counts_by_type: Record<string, number>
  signal_counts_by_source: Record<string, number>
  top_topics: { topic: string; count: number; last_at: string }[]
  top_entities: { title: string; count: number; last_at: string }[]
  transition: {
    total: number
    accepted: number
    rejected: number
    avg_ai_voice_seconds: number
    ended_reasons: Record<string, number>
  }
}

export interface TransitionActivityRow {
  id?: string
  title: string
  summary: string
  topics_json?: string[]
  age_min?: number | null
  age_max?: number | null
  source: string
  status: string
}

/** 家长 AI 分析任务（/admin/ai/jobs；技术方案 §19.5，状态沿用 scan_job 风格）。 */
export interface AiJobRow {
  job_id: string
  job_type: 'CATALOG_AUDIT' | 'USAGE_SUMMARY' | 'CONTENT_COVERAGE'
  state: 'queued' | 'running' | 'done' | 'failed' | 'interrupted'
  progress: number // 0~1
  result_summary: {
    headlines?: string[]
    summary_text?: string[]
    counts?: Record<string, number>
    /** 运行中的过程快照（CATALOG_AUDIT 逐批刷新 / Advisor 初始阶段说明） */
    stage_note?: string
    processed?: number
    total?: number
  } | null
  error_summary: string | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

/** 家长 AI 建议（/admin/ai/proposals；UI 一律显示"AI 建议"，不出现内部术语）。 */
export interface AiProposalRow {
  proposal_id: string
  proposal_type: 'METADATA' | 'POLICY' | 'ARTWORK' | 'CONTENT_GAP' | 'ACTIVITY'
  impact_level: 'LOW' | 'HIGH'
  status: 'PENDING' | 'APPLIED' | 'REJECTED' | 'EXPIRED'
  profile: string
  job_id: string | null
  summary: string
  summary_parts: { why?: string; what?: string; impact?: string }
  changes: Record<string, unknown>
  /** POLICY 建议的服务端事实核对变更行（如"动画（娱乐）时间：40 → 35 分钟"） */
  policy_diff: string[] | null
  entity_id: string | null
  entity_title: string | null
  created_at: string | null
  applied_at: string | null
}

export interface AiApplyResult {
  proposal_id: string
  status: 'applied' | 'expired' | 'failed'
  reason?: string
  note?: string
  /** POLICY 应用返回：新规则版本与被撤销的播放数（AC-18） */
  policy_version?: number
  revoked_playbacks?: number
}

/** 家长声音样本（/admin/voice-profile；PRD TTS-005~007，UI 不出现"克隆/声纹"等内部术语）。 */
export interface VoiceProfileState {
  configured: boolean
  duration_seconds?: number
  sample_rate?: number
  prompt_text?: string
}

export interface VoiceProfileResp {
  configured: boolean
  voice_profile: VoiceProfileState
  clone_ready: boolean
  in_cooldown: boolean
  tts_service: {
    status: string
    ready: boolean
    voice_loaded: boolean
    error?: string | null
  }
  note?: string
}
