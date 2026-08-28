import { useState } from 'react'
import {
  App as AntApp,
  Badge,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'
import { useApi } from '../hooks/useApi'
import { adminApi } from '../api/admin'
import { formatApiError } from '../api/client'
import type { Provider } from '../types/admin'
import { ErrorState } from '../components/ErrorState'

const TEST_RESULT_TEXT: Record<string, { text: string; ok: boolean }> = {
  ok: { text: '连通正常', ok: true },
  auth_failed: { text: 'API Key 无效或无权限', ok: false },
  unreachable: { text: '无法连接', ok: false },
  error: { text: '服务返回错误', ok: false },
}

interface ProviderFormValues {
  display_name: string
  base_url: string
  model: string
  api_key?: string
}

export function ModelsPage() {
  const { data, error, loading, reload } = useApi<{ providers: Provider[] }>(
    '/api/v1/admin/providers',
  )
  const [dialog, setDialog] = useState<
    { mode: 'create' } | { mode: 'edit'; provider: Provider } | null
  >(null)
  const [testing, setTesting] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, { text: string; ok: boolean }>>({})
  const { message } = AntApp.useApp()

  const providers = data?.providers ?? []

  const activate = async (p: Provider) => {
    try {
      await adminApi.activeModelSet(p.provider_id)
      message.success(`已切换为「${p.display_name}」（仅影响新会话）`)
      reload()
    } catch (e) {
      message.error(formatApiError(e))
    }
  }

  const test = async (id: string) => {
    setTesting(id)
    try {
      const r = await adminApi.providerTest(id)
      const mapped = TEST_RESULT_TEXT[r.result]
      const result = mapped
        ? r.result === 'error' && r.detail
          ? { text: `${mapped.text}（${r.detail}）`, ok: false }
          : mapped
        : { text: r.result, ok: false }
      setTestResults((prev) => ({ ...prev, [id]: result }))
      if (result.ok) message.success('连通性测试通过')
    } catch (e) {
      setTestResults((prev) => ({ ...prev, [id]: { text: formatApiError(e), ok: false } }))
    } finally {
      setTesting(null)
    }
  }

  const remove = async (id: string) => {
    try {
      await adminApi.providerDelete(id)
      message.success('已删除该 Provider')
      reload()
    } catch (e) {
      message.error(formatApiError(e))
    }
  }

  /** 停用开关：停用=不参与会话解析（密钥保留）；停用"当前使用"会自动清空激活 */
  const toggleEnabled = async (p: Provider, enabled: boolean) => {
    try {
      await adminApi.providerPatch(p.provider_id, {
        display_name: p.display_name,
        protocol: p.protocol,
        base_url: p.base_url,
        model: p.model,
        enabled,
      })
      message.success(enabled ? `已启用「${p.display_name}」` : `已停用「${p.display_name}」（密钥已保留）`)
      reload()
    } catch (e) {
      message.error(formatApiError(e))
    }
  }

  const columns: ColumnsType<Provider> = [
    { title: '名称', dataIndex: 'display_name', key: 'display_name' },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      render: (s: string) => (
        <Tag color={s === 'page' ? 'green' : 'default'}>{s === 'page' ? '页面' : '配置'}</Tag>
      ),
    },
    { title: '模型', dataIndex: 'model', key: 'model' },
    {
      title: 'Base URL',
      dataIndex: 'base_url',
      key: 'base_url',
      render: (u: string) => (
        <Typography.Text style={{ fontSize: 12 }} copyable={{ text: u }} ellipsis={{ tooltip: u }}>
          {u}
        </Typography.Text>
      ),
    },
    {
      title: 'API Key',
      dataIndex: 'api_key_configured',
      key: 'api_key_configured',
      render: (c: boolean, p) =>
        c ? `已配置（${p.api_key_hint ?? ''}）` : <Tag color="warning">未配置</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'active',
      key: 'active',
      render: (a: boolean, p) =>
        p.enabled === false ? (
          <Badge status="error" text="已停用" />
        ) : a ? (
          <Badge status="success" text="当前使用" />
        ) : (
          <Badge status="default" text="备用" />
        ),
    },
    {
      title: '启用',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 90,
      render: (_: unknown, p) => (
        <Switch
          size="small"
          checked={p.enabled !== false}
          onChange={(checked) => toggleEnabled(p, checked)}
        />
      ),
    },
    {
      title: '连通性',
      key: 'test',
      render: (_, p) => (
        <Space size={8}>
          <Button
            size="small"
            loading={testing === p.provider_id}
            onClick={() => test(p.provider_id)}
          >
            测试
          </Button>
          {testResults[p.provider_id] && (
            <Typography.Text
              type={testResults[p.provider_id].ok ? 'success' : 'danger'}
              style={{ fontSize: 12 }}
            >
              {testResults[p.provider_id].ok ? '✓ ' : '✗ '}
              {testResults[p.provider_id].text}
            </Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, p) => (
        <Space size={8}>
          {!p.active && p.enabled !== false && (
            <Popconfirm
              title={`切换为「${p.display_name}」？`}
              description="切换只影响新会话，进行中的会话不受影响。"
              okText="切换"
              cancelText="取消"
              onConfirm={() => activate(p)}
            >
              <Button type="primary" size="small">
                设为当前
              </Button>
            </Popconfirm>
          )}
          {p.source === 'page' && (
            <>
              <Button size="small" onClick={() => setDialog({ mode: 'edit', provider: p })}>
                编辑
              </Button>
              <Popconfirm
                title="删除该 Provider？"
                description="删除后使用它的会话将失败，请先切换其他模型。"
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={() => remove(p.provider_id)}
              >
                <Button size="small" danger>
                  删除
                </Button>
              </Popconfirm>
            </>
          )}
        </Space>
      ),
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title="AI 模型"
        size="small"
        extra={
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setDialog({ mode: 'create' })}
          >
            添加 Provider
          </Button>
        }
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
          Provider
          可由服务端配置文件声明（来源"配置"），也可在此页面添加（来源"页面"，优先于配置文件同名项）。API
          Key 为写-only：可录入、永不回显；TV 与前端永远接触不到 Key。
        </Typography.Paragraph>
        {error && providers.length === 0 ? (
          <ErrorState error={error} onRetry={reload} />
        ) : (
          <Table
            rowKey="provider_id"
            columns={columns}
            dataSource={providers}
            loading={loading}
            pagination={false}
            size="small"
            scroll={{ x: 1000 }}
            locale={{ emptyText: '尚无任何 Provider。可由配置文件声明，或点"添加 Provider"。' }}
          />
        )}
      </Card>

      {dialog && (
        <ProviderDialog
          provider={dialog.mode === 'edit' ? dialog.provider : null}
          onClose={() => setDialog(null)}
          onSaved={() => {
            setDialog(null)
            reload()
          }}
        />
      )}

    </Space>
  )
}



function ProviderDialog({
  provider,
  onClose,
  onSaved,
}: {
  provider: Provider | null
  onClose: () => void
  onSaved: () => void
}) {
  const [form] = Form.useForm<ProviderFormValues>()
  const [busy, setBusy] = useState(false)
  const isEdit = provider !== null
  const { message } = AntApp.useApp()

  const onSave = async (v: ProviderFormValues) => {
    setBusy(true)
    try {
      const body = {
        display_name: v.display_name,
        protocol: 'openai_chat_completions',
        base_url: v.base_url,
        model: v.model,
        ...(v.api_key?.trim() ? { api_key: v.api_key.trim() } : {}), // 空 = 不修改（写-only）
      }
      if (isEdit && provider) {
        await adminApi.providerPatch(provider.provider_id, body)
      } else {
        await adminApi.providerCreate(body)
      }
      message.success('已保存')
      onSaved()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={isEdit && provider ? `编辑 Provider — ${provider.display_name}` : '添加 LLM Provider'}
      open
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="保存"
      cancelText="取消"
      confirmLoading={busy}
      destroyOnHidden
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
        协议为 OpenAI-compatible chat/completions（V0.1）。API Key
        只在填写时提交/更换，保存后永不回显（写-only）。
      </Typography.Paragraph>
      <Form
        form={form}
        layout="vertical"
        onFinish={onSave}
        initialValues={
          isEdit && provider
            ? {
                display_name: provider.display_name,
                base_url: provider.base_url,
                model: provider.model,
              }
            : {}
        }
      >
        <Form.Item
          name="display_name"
          label="显示名"
          rules={[{ required: true, message: '请输入显示名' }]}
        >
          <Input />
        </Form.Item>
        <Form.Item name="protocol" label="协议" initialValue="openai_chat_completions">
          <Select
            disabled
            options={[{ value: 'openai_chat_completions', label: 'OpenAI Chat Completions' }]}
          />
        </Form.Item>
        <Form.Item
          name="base_url"
          label="Base URL"
          rules={[
            { required: true, message: '请输入 Base URL' },
            { pattern: /^https?:\/\/.+/, message: '需以 http:// 或 https:// 开头' },
          ]}
        >
          <Input placeholder="https://api.example.com/v1" />
        </Form.Item>
        <Form.Item
          name="model"
          label="模型名"
          rules={[{ required: true, message: '请输入模型名' }]}
        >
          <Input placeholder="如 qwen-max / gpt-4o-mini" />
        </Form.Item>
        <Form.Item name="api_key" label="API Key（写-only）">
          <Input.Password
            autoComplete="new-password"
            placeholder={
              isEdit && provider?.api_key_configured
                ? `已配置（${provider.api_key_hint}），留空保持不变`
                : 'sk-…'
            }
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}
