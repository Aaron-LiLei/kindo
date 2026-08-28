import { useEffect, useState } from 'react'
import {
  App as AntApp,
  Badge,
  Button,
  Card,
  Form,
  Input,
  Space,
  Tag,
  Typography,
} from 'antd'
import { adminApi } from '../api/admin'
import { formatApiError } from '../api/client'
import { useApi } from '../hooks/useApi'
import { ErrorState } from '../components/ErrorState'
import { HotwordsCard } from '../components/HotwordsCard'
import type { HealthData, ScrapeConfigResp } from '../types/admin'

/**
 * 设置页（聚合）：管理员账号 / TMDB 刮削配置 / 语音识别（ASR + 热词）/ 关于。
 * 此前这些散在 登录初始化、刮削页折叠区、AI 模型页、概览页——统一收口
 * （A-12 配置最小化 + 全页面管理的"找得到"原则）。
 */
export function SettingsPage() {
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <AccountCard />
      <TmdbCard />
      <HotwordsCard />
      <AboutCard />
    </Space>
  )
}

// ---------- 管理员账号 ----------

interface PasswordFormValues {
  current_password: string
  new_password: string
  confirm: string
}

function AccountCard() {
  const { data: state, error } = useApi<{ username: string | null }>(
    '/api/v1/admin/auth/status',
  )
  const [form] = Form.useForm<PasswordFormValues>()
  const [busy, setBusy] = useState(false)
  const { message } = AntApp.useApp()

  const onSubmit = async (v: PasswordFormValues) => {
    setBusy(true)
    try {
      await adminApi.changePassword({
        current_password: v.current_password,
        new_password: v.new_password,
      })
      message.success('密码已修改（其他已登录的浏览器需要重新登录）')
      form.resetFields()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="管理员账号" size="small">
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Typography.Text>
          用户名：<Typography.Text strong>{error ? '—' : (state?.username ?? '…')}</Typography.Text>
        </Typography.Text>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0 }}>
          初始化时设定的密码可在此修改；修改后当前浏览器保持登录，其他设备需用新密码重新登录。
        </Typography.Paragraph>
        <Form
          form={form}
          layout="vertical"
          onFinish={onSubmit}
          style={{ maxWidth: 420 }}
        >
          <Form.Item
            name="current_password"
            label="当前密码"
            rules={[{ required: true, message: '请输入当前密码' }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 8, message: '至少 8 位' },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="confirm"
            label="确认新密码"
            dependencies={['new_password']}
            rules={[
              { required: true, message: '请再输入一次新密码' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || value === getFieldValue('new_password')) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error('两次输入的新密码不一致'))
                },
              }),
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={busy}>
            修改密码
          </Button>
        </Form>
      </Space>
    </Card>
  )
}

// ---------- TMDB 刮削配置（自刮削页迁入） ----------

function TmdbCard() {
  const [config, setConfig] = useState<ScrapeConfigResp | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [saving, setSaving] = useState(false)
  const { message } = AntApp.useApp()

  useEffect(() => {
    adminApi.scrapeConfig().then(setConfig).catch(() => {})
  }, [])

  const save = async () => {
    setSaving(true)
    try {
      const body: Record<string, string> = {}
      if (baseUrl.trim()) body.base_url = baseUrl.trim()
      if (apiKey.trim()) body.api_key = apiKey.trim()
      const c = await adminApi.scrapeConfigPut(body)
      setConfig(c)
      setApiKey('')
      setBaseUrl('')
      message.success('TMDB 配置已保存；到「刮削与匹配」页运行')
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card
      title="TMDB 刮削配置"
      size="small"
      extra={
        config?.api_key_configured ? (
          <Tag color="green">API Key 已配置</Tag>
        ) : (
          <Tag color="warning">未配置</Tag>
        )
      }
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        用于系列/电影的身份匹配与海报刮削。API Key 只写不回显；大陆网络可在 API
        地址填代理/镜像。保存后到「刮削与匹配」页运行刮削。
      </Typography.Paragraph>
      <Space direction="vertical" size={8} style={{ maxWidth: 480, width: '100%' }}>
        <Input.Password
          placeholder={
            config?.api_key_configured ? 'API Key 已配置，留空保持不变' : 'TMDB API Key'
          }
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
        <Input
          placeholder={`API 地址（默认 ${config?.base_url || 'https://api.themoviedb.org'}）`}
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
        />
        <Button loading={saving} onClick={save} disabled={!apiKey.trim() && !baseUrl.trim()}>
          保存
        </Button>
      </Space>
    </Card>
  )
}

// ---------- 关于 ----------

function AboutCard() {
  const { data, error, reload } = useApi<HealthData>('/api/v1/admin/health')
  if (error) {
    return (
      <Card title="关于" size="small">
        <ErrorState error={error} onRetry={reload} />
      </Card>
    )
  }
  return (
    <Card title="关于" size="small">
      <Space direction="vertical" size={4}>
        <Typography.Text>
          Kindo Hub 版本：<Typography.Text code>{data?.hub.version ?? '…'}</Typography.Text>
        </Typography.Text>
        <Typography.Text>
          语音识别（ASR）：
          {data?.asr.ready ? (
            <Badge status="success" text={`就绪（${data.asr.model ?? ''}）`} />
          ) : (
            <Badge status="warning" text="未就绪（检查 kindo-asr 容器）" />
          )}
        </Typography.Text>
        <Typography.Text>
          数据库：
          {data?.database.ready ? (
            <Badge status="success" text="正常" />
          ) : (
            <Badge status="error" text="异常" />
          )}
        </Typography.Text>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 4 }}>
          AI 模型（LLM Provider）在「AI 模型」页管理；媒体来源在「媒体库」页管理。
        </Typography.Paragraph>
      </Space>
    </Card>
  )
}
