import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { EditMountModal } from './SourcesManager'
import type { Mount } from '../../types/admin'

vi.mock('antd', async () => {
  const actual = await vi.importActual<typeof import('antd')>('antd')
  return { ...actual, App: { useApp: () => ({ message: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }) } }
})

const webdavMount = (over: Partial<Mount> = {}): Mount => ({
  mount_id: 'm1',
  root_id: '',
  sub_path: '',
  label: '百度网盘·淘福气成长',
  read_only: true,
  active: false,
  source: 'page',
  mount_type: 'webdav',
  config: { url: 'http://127.0.0.1:15244/dav', path: 'baidu/淘福气成长', username: 'admin' },
  credentials_configured: true,
  ...over,
})

describe('EditMountModal（媒体来源编辑）', () => {
  it('WebDAV 源预填连接信息；密码写-only 不回显（留空=不修改）', () => {
    render(<EditMountModal mount={webdavMount()} onClose={vi.fn()} onSaved={vi.fn()} />)
    expect(screen.getByDisplayValue('百度网盘·淘福气成长')).toBeInTheDocument()
    expect(screen.getByDisplayValue('http://127.0.0.1:15244/dav')).toBeInTheDocument()
    expect(screen.getByDisplayValue('baidu/淘福气成长')).toBeInTheDocument()
    expect(screen.getByDisplayValue('admin')).toBeInTheDocument()
    // 密码不回显 + 提示已配置
    expect(screen.getByPlaceholderText('留空=不修改')).toBeInTheDocument()
    expect(screen.getByText('已配置（写-only，不回显）')).toBeInTheDocument()
    expect(screen.getByText('保存修改')).toBeInTheDocument()
  })

  it('本地来源编辑路径；不出现网络字段', () => {
    render(
      <EditMountModal
        mount={webdavMount({
          mount_type: 'local', label: '动画库', sub_path: '',
          path: 'C:/media/动画', config: undefined, credentials_configured: undefined,
        })}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    )
    expect(screen.getByDisplayValue('C:/media/动画')).toBeInTheDocument()
    expect(screen.getByText('目录路径（服务器 / 容器内绝对路径）')).toBeInTheDocument()
    expect(screen.queryByLabelText('WebDAV 地址')).toBeNull()
    expect(screen.queryByLabelText('服务器主机')).toBeNull()
  })
})
