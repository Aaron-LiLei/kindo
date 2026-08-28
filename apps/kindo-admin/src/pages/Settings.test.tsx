import { render, screen } from '@testing-library/react'
import { App as AntApp } from 'antd'
import { describe, expect, it, vi } from 'vitest'
import { SettingsPage } from './Settings'

// useApi 打桩：按 path 分流；数据在工厂内部构造（hoisting TDZ）
vi.mock('../hooks/useApi', () => {
  const authStatus = { authenticated: true, username: 'parent' }
  const health = {
    hub: { version: '0.1.0', time: '2026-08-26T10:00:00Z' },
    database: { ready: true },
    media: { mounts: [], latest_jobs: [] },
    asr: { status: 'ready', ready: true, model: 'paraformer' },
    llm_providers: [],
    active_model: { provider_id: null },
    devices: [],
  }
  const hotwords = { path: '/data/hotwords.txt', exists: true, count: 42,
                     sample: ['汪汪队'], updated_at: 1759200000 }
  return {
    useApi: (path: string | null) => {
      if (!path) return { data: undefined, error: null, loading: true, reload: vi.fn() }
      if (path.includes('auth/status'))
        return { data: authStatus, error: null, loading: false, reload: vi.fn() }
      if (path.includes('admin/health'))
        return { data: health, error: null, loading: false, reload: vi.fn() }
      if (path.includes('asr/hotwords'))
        return { data: hotwords, error: null, loading: false, reload: vi.fn() }
      return { data: undefined, error: null, loading: false, reload: vi.fn() }
    },
  }
})

const changePasswordMock = vi.fn().mockResolvedValue({ ok: true, username: 'parent', note: '' })
vi.mock('../api/admin', () => ({
  adminApi: {
    scrapeConfig: vi.fn().mockResolvedValue({
      api_key_configured: true, base_url: 'https://api.themoviedb.org',
    }),
    scrapeConfigPut: vi.fn(),
    changePassword: (...args: unknown[]) => changePasswordMock(...args),
    hotwordsRebuild: vi.fn(),
  },
}))

const renderUi = () =>
  render(<AntApp><SettingsPage /></AntApp>)

describe('SettingsPage（设置聚合）', () => {
  it('聚合四区块：账号改密 / TMDB / 热词 / 关于', async () => {
    renderUi()
    expect(screen.getByText('管理员账号')).toBeInTheDocument()
    expect(screen.getByText(/parent/)).toBeInTheDocument()
    expect(screen.getByText('TMDB 刮削配置')).toBeInTheDocument()
    expect(await screen.findByText(/API Key 已配置/)).toBeInTheDocument()
    expect(screen.getByText(/语音识别热词/)).toBeInTheDocument()
    expect(screen.getByText(/已生成 42 词/)).toBeInTheDocument()
    expect(screen.getByText('关于')).toBeInTheDocument()
  })

  it('改密表单含当前/新/确认三个字段与提交按钮', () => {
    renderUi()
    expect(screen.getByText('当前密码')).toBeInTheDocument()
    expect(screen.getByText('新密码')).toBeInTheDocument()
    expect(screen.getByText('确认新密码')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /修 改密 码|修改密码/ })).toBeInTheDocument()
  })
})
