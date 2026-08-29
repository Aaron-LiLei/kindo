import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { App as AntApp } from 'antd'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { VoicePage } from './Voice'

const holder = vi.hoisted(() => ({
  scenario: 'empty' as 'empty' | 'ready' | 'serviceDown',
  profileMock: vi.fn(),
  uploadMock: vi.fn(),
  deleteMock: vi.fn(),
}))

vi.mock('../api/admin', () => ({
  adminApi: {
    voiceProfile: (...args: unknown[]) => holder.profileMock(...args),
    voiceProfileUpload: (...args: unknown[]) => holder.uploadMock(...args),
    voiceProfileDelete: (...args: unknown[]) => holder.deleteMock(...args),
    voiceProfileAudioUrl: () => '/api/v1/admin/voice-profile/audio',
  },
}))

vi.mock('../api/client', () => ({
  formatApiError: (e: unknown) => String(e),
}))

vi.mock('../hooks/useApi', () => ({
  useApi: () => {
    // empty：已启用合成服务但尚未录入样本（GET 的常规返回）
    const data = holder.scenario === 'empty' ? {
      configured: true,
      voice_profile: { configured: false },
      clone_ready: false,
      in_cooldown: false,
      tts_service: { status: 'ok', ready: true, voice_loaded: true },
    } : {
      configured: true,
      voice_profile:
        holder.scenario === 'ready'
          ? {
              configured: true,
              duration_seconds: 10.2,
              sample_rate: 24000,
              prompt_text: '宝贝你好，我最喜欢陪在你身边。',
            }
          : { configured: false },
      clone_ready: holder.scenario === 'ready',
      in_cooldown: false,
      tts_service:
        holder.scenario === 'serviceDown'
          ? { status: 'unreachable', ready: false, voice_loaded: false }
          : { status: 'ok', ready: true, voice_loaded: true },
    }
    return { data, error: null, loading: false, loadedAt: new Date(), reload: vi.fn() }
  },
}))

function renderPage() {
  return render(
    <AntApp>
      <VoicePage />
    </AntApp>,
  )
}

describe('VoicePage', () => {
  beforeEach(() => {
    holder.profileMock.mockReset()
    holder.uploadMock.mockReset()
    holder.deleteMock.mockReset()
    holder.scenario = 'empty'
  })

  it('未录入时显示开始录音入口与朗读文本', () => {
    renderPage()
    expect(screen.getByText('开始录音')).toBeInTheDocument()
    expect(screen.getByText(/宝贝你好，我最喜欢陪在你身边/)).toBeInTheDocument()
    expect(screen.getByText('合成服务就绪')).toBeInTheDocument()
  })

  it('未启用合成服务时给出配置提示', () => {
    holder.scenario = 'serviceDown'
    renderPage()
    expect(screen.getByText(/合成服务不可用/)).toBeInTheDocument()
  })

  it('已录入时显示当前声音与删除入口，删除需确认', async () => {
    holder.scenario = 'ready'
    holder.deleteMock.mockResolvedValue({ deleted: true, clone_ready: false })
    const user = userEvent.setup()
    renderPage()
    expect(screen.getByText(/10\.2 秒/)).toBeInTheDocument()
    expect(screen.getByText('当前声音')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /删除声音/ }))
    // Popconfirm 确认（antd 对两字按钮自动插空格）
    await user.click(await screen.findByRole('button', { name: '删 除' }))
    await waitFor(() => expect(holder.deleteMock).toHaveBeenCalledTimes(1))
  })
})
