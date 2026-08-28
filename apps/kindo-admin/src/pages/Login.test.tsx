import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { App as AntApp, ConfigProvider } from 'antd'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import { LoginPage, SetupPage } from './Login'

const { loginMock, bootstrapMock } = vi.hoisted(() => ({
  loginMock: vi.fn(),
  bootstrapMock: vi.fn(),
}))

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, login: loginMock, bootstrap: bootstrapMock }
})

function renderEntry(ui: React.ReactNode) {
  return render(
    <ConfigProvider>
      <AntApp>{ui}</AntApp>
    </ConfigProvider>,
  )
}

describe('LoginPage（ready 未登录）', () => {
  it('填写用户名密码提交并回调 onDone', async () => {
    loginMock.mockResolvedValueOnce(undefined)
    const onDone = vi.fn()
    renderEntry(<LoginPage onDone={onDone} />)
    const user = userEvent.setup()

    await user.clear(screen.getByLabelText('用户名'))
    await user.type(screen.getByLabelText('用户名'), 'parent')
    await user.type(screen.getByLabelText('密码'), 'secret-pass')
    await user.click(screen.getByRole('button', { name: /^登\s*录$/ }))

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
    expect(loginMock).toHaveBeenCalledWith('parent', 'secret-pass')
  })

  it('登录失败展示中文错误', async () => {
    loginMock.mockRejectedValueOnce(new ApiError(401, 'unauthorized_admin', 'HTTP 401'))
    const onDone = vi.fn()
    renderEntry(<LoginPage onDone={onDone} />)
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('密码'), 'wrong')
    await user.click(screen.getByRole('button', { name: /^登\s*录$/ }))

    await waitFor(() => expect(screen.getByText('用户名或密码不正确')).toBeInTheDocument())
    expect(onDone).not.toHaveBeenCalled()
  })

  it('登录页不出现任何初始化入口（服务端状态机决定渲染哪页）', () => {
    renderEntry(<LoginPage onDone={() => {}} />)
    expect(screen.queryByText(/初始化/)).not.toBeInTheDocument()
  })
})

describe('SetupPage（setup_required）', () => {
  it('提交初始化并回调 onDone', async () => {
    bootstrapMock.mockResolvedValueOnce(undefined)
    const onDone = vi.fn()
    renderEntry(<SetupPage onDone={onDone} />)
    const user = userEvent.setup()

    await user.clear(screen.getByLabelText('用户名'))
    await user.type(screen.getByLabelText('用户名'), 'admin')
    await user.type(screen.getByLabelText('设置密码'), 'password123')
    await user.type(screen.getByLabelText('Bootstrap Token'), 'tok-123')
    await user.click(screen.getByRole('button', { name: /初始化并进入/ }))

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
    expect(bootstrapMock).toHaveBeenCalledWith('admin', 'password123', 'tok-123')
  })

  it('管理员已被创建（400 竞态）时重取状态切回登录', async () => {
    bootstrapMock.mockRejectedValueOnce(
      new ApiError(400, 'invalid_request', '管理员已初始化，请直接登录'),
    )
    const onDone = vi.fn()
    renderEntry(<SetupPage onDone={onDone} />)
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('设置密码'), 'password123')
    await user.type(screen.getByLabelText('Bootstrap Token'), 'tok-123')
    await user.click(screen.getByRole('button', { name: /初始化并进入/ }))

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
  })
})
