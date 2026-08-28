import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CanonicalPanel } from './CanonicalPanel'
import type { CanonicalEntity } from '../../types/admin'

const makeEntity = (): CanonicalEntity => ({
  entity_id: 'e1',
  entity_type: 'episode',
  parent_id: 'p1',
  parent_title: '汪汪队立大功',
  match_status: 'none',
  ordering: null,
  duration_ms: 0,
  fields: {
    content_class: {
      value: 'ENTERTAINMENT',
      source: 'parser',
      source_label: '路径推断',
      locked: false,
      updated_at: null,
    },
    language: {
      value: 'zh-CN',
      source: 'sidecar',
      source_label: 'Sidecar',
      locked: false,
      updated_at: null,
    },
    age_min: { value: null, source: '', source_label: '未设置', locked: false, updated_at: null },
  },
  provenance_levels: [],
  note: '',
})

// useApi 钩子打桩：直接注入数据（面板数据来自 by-media 查询）。
// 工厂会被 vitest 提升到文件顶部，数据构造必须放进工厂内部（hoisting TDZ）。
vi.mock('../../hooks/useApi', () => ({
  useApi: (path: string | null) => {
    if (!path) return { data: undefined, error: null, loading: true, reload: vi.fn() }
    if (path.includes('/content/by-media/m_none')) {
      return { data: { entity: null }, error: null, loading: false, reload: vi.fn() }
    }
    if (path.includes('/content/by-media/m_err')) {
      return { data: undefined, error: '网络错误', loading: false, reload: vi.fn() }
    }
    return { data: { entity: makeEntity() }, error: null, loading: false, reload: vi.fn() }
  },
}))

describe('CanonicalPanel', () => {
  it('展示字段值、来源标签与编辑入口（ADM-003）', () => {
    render(<CanonicalPanel mediaId="m1" />)
    expect(screen.getByText('内容目录（Canonical）')).toBeInTheDocument()
    expect(screen.getByText(/家长锁定/)).toBeInTheDocument() // 六级优先级说明
    expect(screen.getByText('路径推断')).toBeInTheDocument()
    expect(screen.getByText('Sidecar')).toBeInTheDocument()
    expect(screen.getByText('编辑与锁定')).toBeInTheDocument()
  })

  it('媒体未进内容目录时给出提示', () => {
    render(<CanonicalPanel mediaId="m_none" />)
    expect(screen.getByText(/尚未进入统一内容目录/)).toBeInTheDocument()
  })

  it('加载失败显示错误', () => {
    render(<CanonicalPanel mediaId="m_err" />)
    expect(screen.getByText(/内容目录加载失败/)).toBeInTheDocument()
  })
})
