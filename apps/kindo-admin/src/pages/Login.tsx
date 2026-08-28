import { useState } from 'react'
import { Alert, App as AntApp, Button, Card, Form, Input, Typography } from 'antd'
import { ApiError, bootstrap, formatApiError, login } from '../api/client'

/**
 * 认证入口两页，由服务端 /auth/state 状态机决定渲染哪页（App.tsx 门卫）：
 * - SetupPage：Hub 未初始化（AdminUser 为空），唯一合法动作是带一次性 Token 初始化
 * - LoginPage：Hub 已初始化，正常登录
 * 两者不再共存于一个界面，也不存在客户端模式猜测。
 */

const entryWrapStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'center',
  alignItems: 'flex-start',
  padding: '8vh 16px 16px',
}

function LoginShell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={entryWrapStyle}>
      <Card style={{ width: 400, maxWidth: '100%' }}>
        <Typography.Title level={4} style={{ textAlign: 'center', marginTop: 0 }}>
          {title}
        </Typography.Title>
        {children}
      </Card>
    </div>
  )
}

export function SetupPage({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [form] = Form.useForm<{ username: string; password: string; token: string }>()
  const { message } = AntApp.useApp()

  const onFinish = async (values: { username: string; password: string; token: string }) => {
    setBusy(true)
    setError('')
    try {
      await bootstrap(values.username, values.password, values.token)
      message.success('初始化完成，已进入管理后台')
      onDone()
    } catch (e) {
      // 竞态：初始化期间管理员已被其他浏览器创建 → 服务端 400，重取状态切回登录页
      if (e instanceof ApiError && e.status === 400) {
        message.info('管理员已初始化，请直接登录')
        onDone()
        return
      }
      setError(formatApiError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <LoginShell title="初始化管理员">
      <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
        首次使用请输入服务端生成的一次性 Bootstrap Token（位于 Hub 数据目录{' '}
        <Typography.Text code>data/bootstrap/ADMIN_BOOTSTRAP_TOKEN</Typography.Text>，或环境变量{' '}
        <Typography.Text code>KINDO_ADMIN_BOOTSTRAP_TOKEN</Typography.Text>）。初始化完成后 Token
        立即作废。
      </Typography.Paragraph>
      {error && (
        <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} closable />
      )}
      <Form form={form} layout="vertical" onFinish={onFinish} initialValues={{ username: 'admin' }}>
        <Form.Item
          name="username"
          label="用户名"
          rules={[{ required: true, message: '请输入用户名' }]}
        >
          <Input autoComplete="username" />
        </Form.Item>
        <Form.Item
          name="password"
          label="设置密码"
          rules={[
            { required: true, message: '请设置密码' },
            { min: 8, message: '密码至少 8 位' },
          ]}
        >
          <Input.Password autoComplete="new-password" onPressEnter={() => form.submit()} />
        </Form.Item>
        <Form.Item
          name="token"
          label="Bootstrap Token"
          rules={[{ required: true, message: '请输入一次性初始化 Token' }]}
        >
          <Input.Password autoComplete="off" onPressEnter={() => form.submit()} />
        </Form.Item>
        <Button type="primary" htmlType="submit" block loading={busy}>
          初始化并进入
        </Button>
      </Form>
    </LoginShell>
  )
}

export function LoginPage({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [form] = Form.useForm<{ username: string; password: string }>()
  const { message } = AntApp.useApp()

  const onFinish = async (values: { username: string; password: string }) => {
    setBusy(true)
    setError('')
    try {
      await login(values.username, values.password)
      message.success('登录成功')
      onDone()
    } catch (e) {
      setError(e instanceof ApiError && e.status === 401 ? '用户名或密码不正确' : formatApiError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <LoginShell title="登录 Kindo 管理后台">
      {error && (
        <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} closable />
      )}
      <Form form={form} layout="vertical" onFinish={onFinish} initialValues={{ username: 'admin' }}>
        <Form.Item
          name="username"
          label="用户名"
          rules={[{ required: true, message: '请输入用户名' }]}
        >
          <Input autoComplete="username" />
        </Form.Item>
        <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
          <Input.Password autoComplete="current-password" onPressEnter={() => form.submit()} />
        </Form.Item>
        <Button type="primary" htmlType="submit" block loading={busy}>
          登录
        </Button>
      </Form>
    </LoginShell>
  )
}
