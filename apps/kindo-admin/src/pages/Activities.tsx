import { useState } from 'react'
import {
  App as AntApp,
  Badge,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { DeleteOutlined, EditOutlined, PlusOutlined, CheckOutlined } from '@ant-design/icons'
import { adminApi } from '../api/admin'
import { formatApiError } from '../api/client'
import { useApi } from '../hooks/useApi'
import type { TransitionActivityRow } from '../types/admin'
import { ErrorState } from '../components/ErrorState'

/** 活动库管理（v0.3 ADM-014 / 交互 §8.1）：内置/自建/草稿列表，
 * draft 审核发布入池、家长自建；离屏活动建议的推荐池。 */
const SOURCE_LABEL: Record<string, { label: string; color: string }> = {
  builtin: { label: '内置', color: 'default' },
  parent: { label: '自建', color: 'blue' },
  generated: { label: 'AI 生成', color: 'purple' },
}
const STATUS_LABEL: Record<string, { label: string; status: 'default' | 'processing' | 'warning' | 'success' }> = {
  preset: { label: '预置', status: 'default' },
  published: { label: '已发布', status: 'success' },
  draft: { label: '草稿', status: 'warning' },
}

interface FormValues {
  title: string
  summary: string
  topics: string[]
  age_min: number | null
  age_max: number | null
}

export function ActivitiesPage() {
  const { data, error, loading, reload } = useApi<{ items: TransitionActivityRow[] }>(
    '/api/v1/admin/activities',
  )
  const [editing, setEditing] = useState<TransitionActivityRow | 'new' | null>(null)
  const { message } = AntApp.useApp()

  const onPublish = async (id: string) => {
    try {
      await adminApi.activityPublish(id)
      message.success('已发布进入接力推荐池')
      reload()
    } catch (e) {
      message.error(formatApiError(e))
    }
  }
  const onDelete = async (id: string) => {
    try {
      await adminApi.activityDelete(id)
      message.success('已删除')
      reload()
    } catch (e) {
      message.error(formatApiError(e))
    }
  }

  const columns: ColumnsType<TransitionActivityRow> = [
    {
      title: '活动',
      dataIndex: 'title',
      key: 'title',
      render: (_, r) => (
        <Space direction="vertical" size={2}>
          <Typography.Text strong>{r.title}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12, maxWidth: 420 }} ellipsis>
            {r.summary || '（无说明）'}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '主题',
      dataIndex: 'topics_json',
      key: 'topics',
      render: (topics: string[] | undefined) =>
        (topics ?? []).length === 0 ? (
          <Typography.Text type="secondary">—</Typography.Text>
        ) : (
          <Space wrap size={4}>
            {topics!.map((t) => (
              <Tag key={t} color="green">
                {t}
              </Tag>
            ))}
          </Space>
        ),
    },
    {
      title: '适龄',
      key: 'age',
      render: (_, r) =>
        r.age_min != null || r.age_max != null ? `${r.age_min ?? '?'}-${r.age_max ?? '?'} 岁` : '—',
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      render: (s: string) => {
        const meta = SOURCE_LABEL[s] ?? { label: s, color: 'default' }
        return <Tag color={meta.color}>{meta.label}</Tag>
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (s: string) => {
        const meta = STATUS_LABEL[s] ?? { label: s, status: 'default' as const }
        return <Badge status={meta.status} text={meta.label} />
      },
    },
    {
      title: '操作',
      key: 'ops',
      render: (_, r) => (
        <Space size={4}>
          {r.status === 'draft' && r.id && (
            <Button size="small" icon={<CheckOutlined />} onClick={() => onPublish(r.id!)}>
              发布
            </Button>
          )}
          {r.source !== 'builtin' && r.id && (
            <>
              <Button size="small" icon={<EditOutlined />} onClick={() => setEditing(r)}>
                编辑
              </Button>
              <Popconfirm title="删除该活动？" onConfirm={() => onDelete(r.id!)}>
                <Button size="small" danger icon={<DeleteOutlined />} />
              </Popconfirm>
            </>
          )}
        </Space>
      ),
    },
  ]

  return (
    <Card
      title="活动库（成长接力的离屏活动）"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing('new')}>
          自建活动
        </Button>
      }
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
        孩子选择「离屏活动」时从这里推荐（按主题匹配）；AI 现场生成的活动是草稿，家长确认发布后才进入推荐池；
        内置模板不可改动。
      </Typography.Paragraph>
      {error && !data ? (
        <ErrorState error={error} onRetry={reload} />
      ) : (
        <Table
          rowKey={(r) => r.id ?? r.title}
          columns={columns}
          dataSource={data?.items ?? []}
          loading={loading}
          size="middle"
          pagination={false}
          locale={{ emptyText: '活动库为空' }}
        />
      )}
      {editing && (
        <ActivityEditModal
          row={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            reload()
          }}
        />
      )}
    </Card>
  )
}

function ActivityEditModal({
  row,
  onClose,
  onSaved,
}: {
  row: TransitionActivityRow | null
  onClose: () => void
  onSaved: () => void
}) {
  const [form] = Form.useForm<FormValues>()
  const [busy, setBusy] = useState(false)
  const { message } = AntApp.useApp()

  const onSave = async (v: FormValues) => {
    setBusy(true)
    try {
      const body = {
        title: v.title.trim(),
        summary: v.summary ?? '',
        topics: v.topics ?? [],
        age_min: v.age_min ?? null,
        age_max: v.age_max ?? null,
      }
      if (row?.id) {
        await adminApi.activityPatch(row.id, body)
        message.success('已保存')
      } else {
        await adminApi.activityCreate(body)
        message.success('已创建（自建活动直接进入推荐池）')
      }
      onSaved()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={row ? `编辑活动 — ${row.title}` : '自建活动'}
      open
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="保存"
      cancelText="取消"
      confirmLoading={busy}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        onFinish={onSave}
        initialValues={{
          title: row?.title ?? '',
          summary: row?.summary ?? '',
          topics: row?.topics_json ?? [],
          age_min: row?.age_min ?? null,
          age_max: row?.age_max ?? null,
        }}
      >
        <Form.Item name="title" label="活动名" rules={[{ required: true, message: '必填' }]}>
          <Input placeholder="如：小小海洋学家" />
        </Form.Item>
        <Form.Item name="summary" label="做法（一两句孩子能听懂的话）">
          <Input.TextArea
            rows={3}
            placeholder="在家里找一个圆的东西当“贝壳”，问问爸爸妈妈：海龟为什么要背着重重的壳？"
          />
        </Form.Item>
        <Form.Item name="topics" label="相关主题（用于和刚看内容匹配）">
          <Select mode="tags" tokenSeparators={['、', ',', ' ']} open={false} placeholder="如：海洋、动物" />
        </Form.Item>
        <Space wrap>
          <Form.Item name="age_min" label="适龄下限（岁）">
            <InputNumber min={0} max={18} style={{ width: 100 }} />
          </Form.Item>
          <Form.Item name="age_max" label="适龄上限（岁）">
            <InputNumber min={0} max={18} style={{ width: 100 }} />
          </Form.Item>
        </Space>
      </Form>
    </Modal>
  )
}
