import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HealthPage } from './Health'

const holder = vi.hoisted(() => ({ scenario: 'partial' as 'partial' | 'complete' }))
const navigateMock = vi.fn()
vi.mock('react-router-dom', () => ({
  useNavigate: () => navigateMock,
}))

// useApi 打桩：场景数据在工厂内部构造（hoisting TDZ）；场景经 holder 切换
vi.mock('../hooks/useApi', () => {
  const base = {
    hub: { version: '0.1.0', time: '2026-08-26T10:00:00Z' },
    database: { ready: true },
    media: {
      total: 120,
      match_pending: 3,
      mounts: [{ mount_id: 'm1', label: '家庭NAS', healthy: true, read_only: true }],
      latest_jobs: [],
    },
    asr: { status: 'ready', ready: true, model: 'paraformer' },
    llm_providers: [{ provider_id: 'p1', display_name: '通义', model: 'qwen-max', configured: true }],
    active_model: { provider_id: 'p1' },
    devices: [{ device_id: 'd1', name: '客厅电视', status: 'active', online: true, last_seen_at: null }],
  }
  return {
    useApi: (path: string | null) => {
      const data = holder.scenario === 'complete'
        ? { ...base, media: { ...base.media, match_pending: 0 } }
        : base
      return {
        data: path ? data : undefined,
        error: null,
        loading: !path,
        loadedAt: path ? 1 : null,
        reload: vi.fn(),
      }
    },
  }
})

describe('概览页首跑引导检查清单', () => {
  it('未完成全部步骤时展示清单卡：已完成项给事实摘要、未完成项给下一步指引', () => {
    holder.scenario = 'partial'
    render(<HealthPage />)
    // 1 来源 + 2 入库 + 3 配对 + 4 模型 + 6 ASR = 5 项完成，仅 TMDB 匹配待确认
    expect(screen.getByText(/开始使用 Kindo（已完成 5\/6）/)).toBeInTheDocument()
    // 已完成项显示事实摘要
    expect(screen.getByText(/已添加 1 个来源/)).toBeInTheDocument()
    expect(screen.getByText(/库内已有 120 个内容/)).toBeInTheDocument()
    // 未完成项显示指引（TMDB 匹配待确认）
    expect(screen.getByText(/有 3 个待确认/)).toBeInTheDocument()
  })

  it('全部完成后清单卡隐藏（不再打扰日常使用）', () => {
    holder.scenario = 'complete'
    const { container } = render(<HealthPage />)
    expect(container.textContent).not.toContain('开始使用 Kindo')
    // 主体内容仍在
    expect(container.textContent).toContain('服务状态')
  })
})
