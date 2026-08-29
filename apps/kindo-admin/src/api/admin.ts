/** 按资源分组的类型化 Admin API（契约见 types/admin.ts 注释）。 */
import { api } from './client'
import type {
  AiApplyResult,
  AiJobRow,
  AiProposalRow,
  AnalyticsData,
  AuthState,
  AutoGroupRebuildResp,
  ScrapeConfigResp,
  ScrapeStatusResp,
  CollectionsResp,
  Device,
  HealthData,
  MediaListParams,
  MediaListResp,
  MediaPatchBody,
  MountCreateBody,
  MountHealth,
  MountsPayload,
  PendingPairing,
  PolicyPutResp,
  PolicyResp,
  PolicyRules,
  Provider,
  ProviderBody,
  ProviderTestResp,
  ScanJob,
  ScanJobsResp,
  VoiceProfileResp,
} from '../types/admin'

function toQuery(params: Record<string, string | number | undefined>): string {
  const q = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
    .join('&')
  return q ? `?${q}` : ''
}

export const adminApi = {
  authState: () => api.get('/api/v1/admin/auth/state') as Promise<AuthState>,
  authStatus: () =>
    api.get('/api/v1/admin/auth/status') as Promise<{
      authenticated: boolean
      username: string | null
    }>,
  changePassword: (body: { current_password: string; new_password: string }) =>
    api.post('/api/v1/admin/auth/password', body) as Promise<{
      ok: boolean
      username: string
      note: string
    }>,

  health: () => api.get('/api/v1/admin/health') as Promise<HealthData>,

  mediaList: (params: MediaListParams) =>
    api.get(
      `/api/v1/admin/media${toQuery({
        type: params.type,
        language: params.language,
        tag: params.tag,
        series_id: params.series_id,
        course_id: params.course_id,
        cursor: params.cursor,
        limit: params.limit,
        sort: params.sort,
      })}`,
    ) as Promise<MediaListResp>,
  mediaPatch: (mediaId: string, body: MediaPatchBody) =>
    api.patch(`/api/v1/admin/media/${mediaId}`, body),
  /** 缩略海报地址（同源 Cookie 认证，img 标签直接可用） */
  posterUrl: (mediaId: string) => `/api/v1/admin/media/${mediaId}/poster`,
  collections: () => api.get('/api/v1/admin/collections') as Promise<CollectionsResp>,
  /** 本地重算自动归组（存量回填，不触发存储访问） */
  autoGroupRebuild: () =>
    api.post('/api/v1/admin/media/auto-group', {}) as Promise<AutoGroupRebuildResp>,

  mounts: () => api.get('/api/v1/admin/media-mounts') as Promise<MountsPayload>,
  mountCreate: (body: MountCreateBody) =>
    api.post('/api/v1/admin/media-mounts', body) as Promise<{
      label: string
      mount_type: string
      mount_id: string
    }>,
  /** 编辑来源：连接字段可选提交（未提交=不变；密码写-only，空串=清除） */
  mountPatch: (mountId: string, body: Partial<{
    label: string
    read_only: boolean
    active: boolean
    sub_path: string
    host: string
    port: number
    share: string
    url: string
    path: string
    username: string
    password: string
    probe_mode: 'range' | 'skip' | 'full'
  }>) => api.patch(`/api/v1/admin/media-mounts/${mountId}`, body),
  mountDelete: (mountId: string) => api.delete(`/api/v1/admin/media-mounts/${mountId}`),
  /** 外层根页面管理（2026-08-25 决策）：显示名/启停/恢复 */
  rootPatch: (rootId: string, body: Partial<{ label: string | null; active: boolean; removed: boolean }>) =>
    api.patch(`/api/v1/admin/media-roots/${rootId}`, body),
  rootDelete: (rootId: string) => api.delete(`/api/v1/admin/media-roots/${rootId}`),
  mountScan: (mountId: string, forceFull = false) =>
    api.post(`/api/v1/admin/media-mounts/${mountId}/scan${forceFull ? '?force_full=true' : ''}`) as Promise<{
      job_id: string
      force_full?: boolean
    }>,
  /** 添加前的连接测试（不落库），返回可执行的修正提示 */
  mountTest: (body: MountCreateBody) =>
    api.post('/api/v1/admin/media-mounts/test', body) as Promise<{ ok: boolean; message: string }>,
  scanJob: (jobId: string) => api.get(`/api/v1/admin/scan-jobs/${jobId}`) as Promise<ScanJob>,
  scanJobs: (limit = 20) =>
    api.get(`/api/v1/admin/scan-jobs?limit=${limit}`) as Promise<ScanJobsResp>,
  mountsHealth: () =>
    api.get('/api/v1/admin/media-mounts/health') as Promise<{ mounts: MountHealth[] }>,

  policyGet: () => api.get('/api/v1/admin/policy') as Promise<PolicyResp>,
  policyPut: (rules: PolicyRules) =>
    api.put('/api/v1/admin/policy', rules) as Promise<PolicyPutResp>,

  providers: () => api.get('/api/v1/admin/providers') as Promise<{ providers: Provider[] }>,
  providerCreate: (body: ProviderBody) => api.post('/api/v1/admin/providers', body),
  providerPatch: (providerId: string, body: ProviderBody) =>
    api.patch(`/api/v1/admin/providers/${providerId}`, body),
  providerDelete: (providerId: string) => api.delete(`/api/v1/admin/providers/${providerId}`),
  providerTest: (providerId: string) =>
    api.post(`/api/v1/admin/providers/${providerId}/test`) as Promise<ProviderTestResp>,
  activeModelSet: (providerId: string) =>
    api.post('/api/v1/admin/active-model', { provider_id: providerId }),

  matchOverview: () =>
    api.get('/api/v1/admin/match/overview') as Promise<import('../types/admin').MatchOverview>,
  matchSearch: (query: string, entityId?: string) =>
    api.post('/api/v1/admin/match/search', {
      query, entity_id: entityId ?? null,
    }) as Promise<{ candidates: import('../types/admin').MatchCandidate[] }>,
  matchConfirm: (entityId: string, body: {
    ref_id?: string; title?: string; no_match?: boolean; apply_details?: boolean;
    first_air_date?: string; poster_path?: string;
  }) =>
    api.post(`/api/v1/admin/content/${entityId}/match`, body) as Promise<{
      entity_id: string; match_status: string;
    }>,
  matchDecisions: (entityId: string) =>
    api.get(`/api/v1/admin/content/${entityId}/match/decisions`) as Promise<{
      decisions: import('../types/admin').MatchDecisionRow[];
    }>,
  matchDecisionsRecent: (limit = 50) =>
    api.get(`/api/v1/admin/match/decisions/recent?limit=${limit}`) as Promise<{
      decisions: import('../types/admin').RecentMatchDecision[];
    }>,
  interestAnalytics: (period: 'day' | 'week') =>
    api.get(`/api/v1/admin/analytics/interest?period=${period}`) as Promise<
      import('../types/admin').InterestAnalytics
    >,
  activities: () =>
    api.get('/api/v1/admin/activities') as Promise<{
      items: import('../types/admin').TransitionActivityRow[]
    }>,
  activityCreate: (body: {
    title: string
    summary?: string
    topics?: string[]
    age_min?: number | null
    age_max?: number | null
  }) => api.post('/api/v1/admin/activities', body) as Promise<{ id: string }>,
  activityPatch: (id: string, body: {
    title?: string
    summary?: string
    topics?: string[]
    age_min?: number | null
    age_max?: number | null
  }) => api.patch(`/api/v1/admin/activities/${id}`, body),
  activityDelete: (id: string) => api.delete(`/api/v1/admin/activities/${id}`),
  activityPublish: (id: string) =>
    api.post(`/api/v1/admin/activities/${id}/publish`, {}) as Promise<unknown>,
  analytics: (period: 'day' | 'week' | 'custom', start?: string, end?: string) => {
    const params = new URLSearchParams({ period })
    if (start) params.set('start', start)
    if (end) params.set('end', end)
    return api.get(`/api/v1/admin/analytics?${params.toString()}`) as Promise<AnalyticsData>
  },
  interestAnalyticsRange: (period: 'day' | 'week' | 'custom', start?: string, end?: string) => {
    const params = new URLSearchParams({ period })
    if (start) params.set('start', start)
    if (end) params.set('end', end)
    return api.get(`/api/v1/admin/analytics/interest?${params.toString()}`) as Promise<
      import('../types/admin').InterestAnalytics
    >
  },
  hotwordsStatus: () =>
    api.get('/api/v1/admin/asr/hotwords') as Promise<{
      path: string
      exists: boolean
      count?: number
      sample?: string[]
      updated_at?: number
      note?: string
    }>,
  hotwordsRebuild: () =>
    api.post('/api/v1/admin/asr/hotwords/rebuild', {}) as Promise<{
      path: string
      count: number
      manual_count: number
      note: string
    }>,
  entityAssets: (entityId: string) =>
    api.get(`/api/v1/admin/content/${entityId}/assets`) as Promise<{
      entity_id: string
      entity_title: string
      assets: import('../types/admin').EntityAssetRow[]
    }>,
  setPreferredAsset: (entityId: string, assetId: string) =>
    api.put(`/api/v1/admin/content/${entityId}/preferred-asset`, {
      asset_id: assetId,
    }) as Promise<{ entity_id: string; preferred_asset_id: string }>,

  /** v0.3 Canonical 元数据（ADM-003） */
  contentByMedia: (mediaId: string) =>
    api.get(`/api/v1/admin/content/by-media/${mediaId}`) as Promise<{
      entity: import('../types/admin').CanonicalEntity | null
    }>,
  contentPatch: (entityId: string, fields: Record<string, {
    value?: unknown
    locked?: boolean
    has_value?: boolean
  }>) =>
    api.patch(`/api/v1/admin/content/${entityId}`, { fields }) as Promise<{
      entity_id: string
      applied: string[]
      fields: import('../types/admin').CanonicalEntity['fields']
    }>,

  /** v0.3 Artwork 管理（ADM-013） */
  artworkList: (entityId: string) =>
    api.get(`/api/v1/admin/content/${entityId}/artwork`) as Promise<{
      items: import('../types/admin').ArtworkItem[]
    }>,
  artworkUpload: (entityId: string, kind: string, file: File, locked = true) => {
    const form = new FormData()
    form.append('kind', kind)
    form.append('locked', String(locked))
    form.append('file', file)
    return api.upload(`/api/v1/admin/content/${entityId}/artwork`, form) as Promise<{
      kind: string
      locked: boolean
    }>
  },
  artworkLock: (entityId: string, kind: string, locked: boolean) =>
    api.patch(`/api/v1/admin/content/${entityId}/artwork/${kind}`, { locked }),
  artworkDelete: (entityId: string, kind: string) =>
    api.delete(`/api/v1/admin/content/${entityId}/artwork/${kind}`),
  artworkImageUrl: (entityId: string, kind: string) =>
    `/api/v1/admin/content/${entityId}/artwork/${kind}/image`,

  /** v0.3 Policy 今日剩余预览 */
  policyUsage: () =>
    api.get('/api/v1/admin/policy/usage') as Promise<import('../types/admin').PolicyUsage>,

  // ---------- 家长声音（PRD TTS-005~007；UI 不出现"克隆/声纹"等内部术语） ----------
  voiceProfile: () => api.get('/api/v1/admin/voice-profile') as Promise<VoiceProfileResp>,
  /** FormData 走 PUT（audio 文件 + prompt_text），浏览器自动设置 multipart boundary */
  voiceProfileUpload: (audio: Blob, promptText: string) => {
    const form = new FormData()
    form.append('audio', audio, 'recording.webm')
    form.append('prompt_text', promptText)
    return api.put('/api/v1/admin/voice-profile', form) as Promise<VoiceProfileResp>
  },
  voiceProfileDelete: () =>
    api.delete('/api/v1/admin/voice-profile') as Promise<{
      deleted: boolean
      clone_ready: boolean
    }>,
  /** 样本回放地址（同源 Cookie 认证，audio 标签直接可用；query 为缓存失效戳） */
  voiceProfileAudioUrl: () => '/api/v1/admin/voice-profile/audio',

  devices: () => api.get('/api/v1/admin/devices') as Promise<{ devices: Device[] }>,
  deviceRevoke: (deviceId: string) => api.post(`/api/v1/admin/devices/${deviceId}/revoke`),
  /** 批量清理已撤销/长期离线设备（在线设备不清理；被清理设备需重新配对） */
  devicesCleanup: (body: { revoked: boolean; offline_days: number }) =>
    api.post('/api/v1/admin/devices/cleanup', body) as Promise<{
      deleted: number
      devices: string[]
      note: string
    }>,

  pairings: () =>
    api.get('/api/v1/admin/pairing/requests') as Promise<{ pending: PendingPairing[] }>,
  pairingApprove: (pairingId: string, confirmCode: string) =>
    api.post(`/api/v1/admin/pairing/requests/${pairingId}/approve`, { confirm_code: confirmCode }),
  pairingDeny: (pairingId: string) => api.post(`/api/v1/admin/pairing/requests/${pairingId}/deny`),

  /** 海报刮削（2026-08-21 PRD 修订）：TMDB 检索配置与批任务 */
  scrapeConfig: () => api.get('/api/v1/admin/scrape/config') as Promise<ScrapeConfigResp>,
  scrapeConfigPut: (body: { base_url?: string; image_base_url?: string; language?: string; api_key?: string }) =>
    api.put('/api/v1/admin/scrape/config', body) as Promise<ScrapeConfigResp>,
  scrapeRun: (force = false) =>
    api.post('/api/v1/admin/scrape/run', { force }) as Promise<ScrapeStatusResp>,
  scrapeStatus: () => api.get('/api/v1/admin/scrape/status') as Promise<ScrapeStatusResp>,

  // ---------- 家长 AI 助手（/admin/ai/*；PRD 8.14 / 技术方案 §19） ----------

  aiJobCreate: (job_type: string) =>
    api.post('/api/v1/admin/ai/jobs', { job_type }) as Promise<{
      job_id: string
      state: string
    }>,
  aiJobs: (params: { job_type?: string; status?: string; limit?: number } = {}) =>
    api.get(`/api/v1/admin/ai/jobs${toQuery(params)}`) as Promise<{ items: AiJobRow[] }>,
  aiJob: (jobId: string) =>
    api.get(`/api/v1/admin/ai/jobs/${jobId}`) as Promise<AiJobRow>,
  aiProposals: (
    params: {
      status?: string
      impact_level?: string
      proposal_type?: string
      job_id?: string
    } = {},
  ) =>
    api.get(`/api/v1/admin/ai/proposals${toQuery(params)}`) as Promise<{
      items: AiProposalRow[]
      /** 当前筛选条件下建议总数（列表仅返回前 limit 条） */
      total: number
    }>,
  aiProposalApply: (id: string) =>
    api.post(`/api/v1/admin/ai/proposals/${id}/apply`, {}) as Promise<AiApplyResult>,
  aiProposalReject: (id: string) =>
    api.post(`/api/v1/admin/ai/proposals/${id}/reject`, {}) as Promise<{
      proposal_id: string
      status: string
    }>,
  aiProposalsBatchApply: (ids: string[], allowHigh = false) =>
    api.post('/api/v1/admin/ai/proposals/batch-apply', { ids, allow_high: allowHigh }) as Promise<{
      results: AiApplyResult[]
      note: string
    }>,
  aiProposalsDismissAll: () =>
    api.post('/api/v1/admin/ai/proposals/dismiss-all', {}) as Promise<{
      cleared: number
      note: string
    }>,
}
