/**
 * Admin API 客户端：Cookie 会话 + CSRF 头（技术方案 §14.3）。
 * 统一处理：默认超时、错误规范化、会话过期（401）全局分发。
 */

export const KINDO_UNAUTHORIZED_EVENT = 'kindo:unauthorized'

const csrfKey = 'kindo_csrf_token'
const DEFAULT_TIMEOUT_MS = 15000
/** 这些路径上的 401 属于正常登录失败，不应触发"会话过期"跳转 */
const AUTH_PATHS = ['/auth/login', '/auth/bootstrap', '/auth/state']

export function getCsrfToken(): string | null {
  return localStorage.getItem(csrfKey)
}

export class ApiError extends Error {
  status: number
  code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

export interface RequestOptions {
  signal?: AbortSignal
}

async function request(
  method: string,
  path: string,
  body?: unknown,
  options: RequestOptions = {},
): Promise<unknown> {
  const headers: Record<string, string> = {}
  if (body !== undefined && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }
  const csrf = getCsrfToken()
  if (csrf && method !== 'GET') headers['X-CSRF-Token'] = csrf

  let timeout: ReturnType<typeof setTimeout> | undefined
  let signal = options.signal
  if (!signal) {
    const controller = new AbortController()
    timeout = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS)
    signal = controller.signal
  }

  let resp: Response
  try {
    resp = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
      credentials: 'same-origin',
      signal,
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new ApiError(0, 'timeout', '请求超时，请稍后重试')
    }
    throw new ApiError(0, 'network_error', '无法连接服务，请检查网络或 Hub 是否在运行')
  } finally {
    if (timeout) clearTimeout(timeout)
  }

  let data: unknown
  try {
    data = await resp.json()
  } catch {
    data = null
  }
  if (!resp.ok) {
    if (resp.status === 401 && !AUTH_PATHS.some((p) => path.includes(p))) {
      window.dispatchEvent(new CustomEvent(KINDO_UNAUTHORIZED_EVENT))
    }
    const err = (data as { error?: { code?: string; message?: string } })?.error
    throw new ApiError(resp.status, err?.code ?? 'unknown', err?.message ?? `HTTP ${resp.status}`)
  }
  return data
}

export const api = {
  get: (p: string, o?: RequestOptions) => request('GET', p, undefined, o),
  post: (p: string, b?: unknown, o?: RequestOptions) => request('POST', p, b ?? {}, o),
  put: (p: string, b: unknown, o?: RequestOptions) => request('PUT', p, b, o),
  patch: (p: string, b: unknown, o?: RequestOptions) => request('PATCH', p, b, o),
  delete: (p: string, o?: RequestOptions) => request('DELETE', p, undefined, o),
  /** multipart 上传（FormData 由浏览器设置 boundary；仍带 CSRF 头） */
  upload: (p: string, form: FormData, o?: RequestOptions) =>
    request('POST', p, form, o),
}

const CODE_MESSAGES: Record<string, string> = {
  unauthorized_admin: '登录已过期，请重新登录',
  forbidden_admin: '没有权限或 CSRF 校验失败，请刷新页面重试',
}

/** 把任意异常转成可展示给家长的中文文案 */
export function formatApiError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401) return CODE_MESSAGES.unauthorized_admin
    if (e.status === 0) return e.message
    return CODE_MESSAGES[e.code] ?? e.message
  }
  return e instanceof Error ? e.message : String(e)
}

export async function login(username: string, password: string): Promise<void> {
  const result = (await api.post('/api/v1/admin/auth/login', { username, password })) as {
    csrf_token: string
  }
  localStorage.setItem(csrfKey, result.csrf_token)
}

export async function bootstrap(username: string, password: string, token: string): Promise<void> {
  await api.post('/api/v1/admin/auth/bootstrap', { username, password, bootstrap_token: token })
  await login(username, password)
}

export async function logout(): Promise<void> {
  try {
    await api.post('/api/v1/admin/auth/logout')
  } finally {
    localStorage.removeItem(csrfKey)
  }
}
