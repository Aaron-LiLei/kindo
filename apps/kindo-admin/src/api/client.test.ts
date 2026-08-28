import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, formatApiError, KINDO_UNAUTHORIZED_EVENT } from './client'

const fetchMock = vi.fn()
vi.stubGlobal('fetch', fetchMock)

const okJson = (body: unknown) => ({
  ok: true,
  status: 200,
  json: async () => body,
})
const errJson = (status: number, body: unknown = null) => ({
  ok: false,
  status,
  json: async () => body,
})

afterEach(() => {
  fetchMock.mockReset()
  localStorage.clear()
})

describe('CSRF 头', () => {
  it('写请求携带 X-CSRF-Token', async () => {
    localStorage.setItem('kindo_csrf_token', 'tok-1')
    fetchMock.mockResolvedValueOnce(okJson({}))
    await api.post('/api/v1/admin/policy', {})
    const init = fetchMock.mock.calls[0][1] as RequestInit
    const headers = init.headers as Record<string, string>
    expect(headers['X-CSRF-Token']).toBe('tok-1')
    expect(headers['Content-Type']).toBe('application/json')
  })

  it('GET 不携带', async () => {
    localStorage.setItem('kindo_csrf_token', 'tok-1')
    fetchMock.mockResolvedValueOnce(okJson({}))
    await api.get('/api/v1/admin/health')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    const headers = init.headers as Record<string, string>
    expect(headers['X-CSRF-Token']).toBeUndefined()
  })
})

describe('401 会话过期分发', () => {
  it('非认证路径的 401 派发全局事件并抛 ApiError', async () => {
    const listener = vi.fn()
    window.addEventListener(KINDO_UNAUTHORIZED_EVENT, listener)
    fetchMock.mockResolvedValueOnce(
      errJson(401, { error: { code: 'unauthorized_admin', message: '未认证' } }),
    )
    await expect(api.get('/api/v1/admin/media')).rejects.toBeInstanceOf(ApiError)
    expect(listener).toHaveBeenCalledTimes(1)
    window.removeEventListener(KINDO_UNAUTHORIZED_EVENT, listener)
  })

  it('登录路径的 401 不派发（属正常登录失败）', async () => {
    const listener = vi.fn()
    window.addEventListener(KINDO_UNAUTHORIZED_EVENT, listener)
    fetchMock.mockResolvedValueOnce(errJson(401))
    await expect(api.post('/api/v1/admin/auth/login', {})).rejects.toBeInstanceOf(ApiError)
    expect(listener).not.toHaveBeenCalled()
    window.removeEventListener(KINDO_UNAUTHORIZED_EVENT, listener)
  })
})

describe('错误解析与文案', () => {
  it('服务端 error.code/message 透传', async () => {
    fetchMock.mockResolvedValueOnce(
      errJson(400, { error: { code: 'invalid_request', message: '规则不合法' } }),
    )
    const err = await api.put('/api/v1/admin/policy', {}).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).code).toBe('invalid_request')
    expect((err as ApiError).message).toBe('规则不合法')
  })

  it('无 JSON body 时回落 HTTP 状态', async () => {
    fetchMock.mockResolvedValueOnce(errJson(500))
    const err = await api.get('/api/v1/admin/health').catch((e) => e)
    expect((err as ApiError).status).toBe(500)
    expect((err as ApiError).message).toBe('HTTP 500')
  })

  it('网络失败转为中文文案', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const err = await api.get('/api/v1/admin/health').catch((e) => e)
    expect((err as ApiError).status).toBe(0)
    expect((err as ApiError).message).toContain('无法连接')
  })
})

describe('formatApiError', () => {
  it('401 映射为登录过期文案', () => {
    expect(formatApiError(new ApiError(401, 'unauthorized_admin', 'HTTP 401'))).toContain(
      '登录已过期',
    )
  })
  it('网络错误透传中文消息', () => {
    expect(formatApiError(new ApiError(0, 'network_error', '无法连接服务'))).toBe('无法连接服务')
  })
  it('未知异常转字符串', () => {
    expect(formatApiError('boom')).toBe('boom')
  })
})
