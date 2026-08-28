import { useState } from 'react'
import {
  App as AntApp,
  Button,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from 'antd'
import { EditOutlined, LockOutlined } from '@ant-design/icons'
import { adminApi } from '../../api/admin'
import { formatApiError } from '../../api/client'
import { useApi } from '../../hooks/useApi'
import type { CanonicalEntity } from '../../types/admin'

/** Canonical 元数据面板（v0.3 ADM-003）：结构化字段值 + 来源 + locked 分离展示；
 * content_class 是规则事实来源（约束 12/15），家长可锁定防 Provider 漂移。 */

const EDITABLE_FIELDS: { key: string; label: string; type: 'text' | 'number' | 'select' }[] = [
  { key: 'content_class', label: '内容分类', type: 'select' },
  { key: 'modality', label: '媒介', type: 'select' },
  { key: 'language', label: '语言', type: 'text' },
  { key: 'age_min', label: '适龄下限', type: 'number' },
  { key: 'age_max', label: '适龄上限', type: 'number' },
  { key: 'difficulty', label: '难度', type: 'text' },
  { key: 'sequence_no', label: '顺序号', type: 'number' },
  { key: 'repeatable', label: '可重复', type: 'select' },
  { key: 'overview', label: '简介', type: 'text' },
  { key: 'release_date', label: '首播/上映', type: 'text' },
  { key: 'topics', label: '主题', type: 'text' },
  { key: 'characters', label: '角色', type: 'text' },
]

const SOURCE_COLOR: Record<string, string> = {
  parent_locked: 'gold',
  parent: 'blue',
  sidecar: 'cyan',
  provider_confirmed: 'green',
  provider: 'green',
  parser: 'default',
}

function fmtValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.join('、')
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

export function CanonicalPanel({ mediaId, onDirty }: { mediaId: string; onDirty?: () => void }) {
  const { data, error, reload } = useApi<{ entity: CanonicalEntity | null }>(
    `/api/v1/admin/content/by-media/${mediaId}`,
  )
  const [editing, setEditing] = useState(false)
  const { message } = AntApp.useApp()

  if (error) {
    return (
      <Typography.Text type="danger" style={{ fontSize: 12 }}>
        内容目录加载失败：{error}
      </Typography.Text>
    )
  }
  if (!data) return null
  const entity = data.entity
  if (!entity) {
    return (
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        该媒体尚未进入统一内容目录（重扫后自动建立）
      </Typography.Text>
    )
  }

  const items = EDITABLE_FIELDS.filter((f) => entity.fields[f.key]).map((f) => ({
    key: f.key,
    label: f.label,
    children: (
      <Space size={6} wrap>
        <span>{fmtValue(entity.fields[f.key].value)}</span>
        {entity.fields[f.key].source && (
          <Tag color={SOURCE_COLOR[entity.fields[f.key].source] ?? 'default'} style={{ fontSize: 11 }}>
            {entity.fields[f.key].source_label}
          </Tag>
        )}
        {entity.fields[f.key].locked && (
          <Tag icon={<LockOutlined />} color="gold" style={{ fontSize: 11 }}>
            锁定
          </Tag>
        )}
      </Space>
    ),
  }))

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Typography.Title level={5} style={{ margin: 0 }}>
          内容目录（Canonical）
        </Typography.Title>
        <Button size="small" icon={<EditOutlined />} onClick={() => setEditing(true)}>
          编辑与锁定
        </Button>
      </div>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 4 }}>
        六级优先级：家长锁定 &gt; 家长 &gt; Sidecar &gt; Provider（已确认） &gt; Provider &gt;
        路径推断；锁定字段永不被刷新覆盖。
      </Typography.Paragraph>
      <Descriptions column={1} size="small" bordered items={items} />
      {editing && (
        <CanonicalEditModal
          entity={entity}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false)
            message.success('已保存（家长级，重扫/刷新不覆盖）')
            reload()
            onDirty?.()
          }}
        />
      )}
    </>
  )
}

interface EditValues {
  content_class?: string
  modality?: string
  language?: string
  age_min?: number | null
  age_max?: number | null
  difficulty?: string
  sequence_no?: number | null
  repeatable?: boolean
  overview?: string
  release_date?: string
  topics?: string[]
  characters?: string[]
  locked_fields?: string[]
}

function CanonicalEditModal({
  entity,
  onClose,
  onSaved,
}: {
  entity: CanonicalEntity
  onClose: () => void
  onSaved: () => void
}) {
  const [form] = Form.useForm<EditValues>()
  const [busy, setBusy] = useState(false)
  const { message } = AntApp.useApp()
  const f = entity.fields

  const onSave = async (v: EditValues) => {
    const fields: Record<string, { value?: unknown; locked?: boolean; has_value?: boolean }> = {}
    const put = (key: string, value: unknown) => {
      fields[key] = { value, has_value: true }
    }
    if (v.content_class !== undefined) put('content_class', v.content_class || null)
    if (v.modality !== undefined) put('modality', v.modality || null)
    if (v.language !== undefined) put('language', v.language || null)
    if (v.age_min !== undefined) put('age_min', v.age_min ?? null)
    if (v.age_max !== undefined) put('age_max', v.age_max ?? null)
    if (v.difficulty !== undefined) put('difficulty', v.difficulty || null)
    if (v.sequence_no !== undefined) put('sequence_no', v.sequence_no ?? null)
    if (v.repeatable !== undefined) put('repeatable', v.repeatable)
    if (v.overview !== undefined) put('overview', v.overview || null)
    if (v.release_date !== undefined) put('release_date', v.release_date || null)
    if (v.topics !== undefined) put('topics', v.topics)
    if (v.characters !== undefined) put('characters', v.characters)
    for (const key of v.locked_fields ?? []) {
      if (fields[key]) fields[key].locked = true
      else fields[key] = { locked: true }
    }
    setBusy(true)
    try {
      await adminApi.contentPatch(entity.entity_id, fields)
      onSaved()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setBusy(false)
    }
  }

  const lockedInitial = EDITABLE_FIELDS.filter(
    (x) => x.key in f && f[x.key]?.locked,
  ).map((x) => x.key)

  return (
    <Modal
      title={`Canonical 编辑 — ${entity.parent_title ?? ''}${entity.parent_title ? ' / ' : ''}${String(f.title?.value ?? '')}`}
      open
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="保存"
      cancelText="取消"
      confirmLoading={busy}
      width={560}
      destroyOnHidden
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        保存即写入家长级来源（PARENT_EXPLICIT）；勾选「锁定」后该字段不再被任何来源覆盖。
        内容分类变更会直接影响 Family Policy 的预算判定。
      </Typography.Paragraph>
      <Form
        form={form}
        layout="vertical"
        onFinish={onSave}
        initialValues={{
          content_class: (f.content_class?.value as string) ?? undefined,
          modality: (f.modality?.value as string) ?? undefined,
          language: (f.language?.value as string) ?? '',
          age_min: (f.age_min?.value as number | null) ?? null,
          age_max: (f.age_max?.value as number | null) ?? null,
          difficulty: (f.difficulty?.value as string) ?? '',
          sequence_no: (f.sequence_no?.value as number | null) ?? null,
          repeatable: (f.repeatable?.value as boolean) ?? false,
          overview: (f.overview?.value as string) ?? '',
          release_date: (f.release_date?.value as string) ?? '',
          topics: (f.topics?.value as string[]) ?? [],
          characters: (f.characters?.value as string[]) ?? [],
          locked_fields: lockedInitial,
        }}
      >
        <Space wrap size="middle" align="start">
          <Form.Item name="content_class" label="内容分类" style={{ minWidth: 150 }}>
            <Select
              allowClear
              placeholder="—"
              options={[
                { value: 'ENTERTAINMENT', label: '娱乐（ENTERTAINMENT）' },
                { value: 'LEARNING', label: '学习（LEARNING）' },
                { value: 'STORY', label: '故事（STORY）' },
                { value: 'MUSIC', label: '音乐（MUSIC）' },
                { value: 'OTHER', label: '其他（OTHER）' },
              ]}
            />
          </Form.Item>
          <Form.Item name="modality" label="媒介" style={{ minWidth: 140 }}>
            <Select
              allowClear
              placeholder="—"
              options={[
                { value: 'VIDEO', label: '视频（VIDEO）' },
                { value: 'AUDIO', label: '音频（AUDIO）' },
                { value: 'OFFSCREEN', label: '离屏（OFFSCREEN）' },
              ]}
            />
          </Form.Item>
          <Form.Item name="language" label="语言">
            <Input placeholder="zh-CN" style={{ width: 120 }} />
          </Form.Item>
        </Space>
        <Space wrap size="middle" align="start">
          <Form.Item name="age_min" label="适龄下限">
            <InputNumber min={0} max={18} style={{ width: 90 }} />
          </Form.Item>
          <Form.Item name="age_max" label="适龄上限">
            <InputNumber min={0} max={18} style={{ width: 90 }} />
          </Form.Item>
          <Form.Item name="difficulty" label="难度">
            <Input placeholder="easy / 基础" style={{ width: 140 }} />
          </Form.Item>
          <Form.Item name="sequence_no" label="顺序号">
            <InputNumber min={1} style={{ width: 90 }} />
          </Form.Item>
          <Form.Item name="repeatable" label="可重复" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Space>
        <Form.Item name="topics" label="主题（兴趣信号与接力的匹配依据）">
          <Select mode="tags" tokenSeparators={['、', ',', ' ']} open={false} placeholder="如：海洋、数字" />
        </Form.Item>
        <Form.Item name="characters" label="角色">
          <Select mode="tags" tokenSeparators={['、', ',', ' ']} open={false} placeholder="如：天天、佩奇" />
        </Form.Item>
        <Form.Item name="overview" label="简介">
          <Input.TextArea rows={2} placeholder="作品简介（Provider 拉取或家长补充）" />
        </Form.Item>
        <Form.Item name="release_date" label="首播/上映日期">
          <Input placeholder="2021-04-02" style={{ width: 180 }} />
        </Form.Item>
        <Form.Item
          name="locked_fields"
          label="锁定的字段（永不被 Provider 刷新 / 重扫覆盖）"
          tooltip="例如锁「内容分类」可防止刮削把娱乐内容改标成学习"
        >
          <Select
            mode="multiple"
            placeholder="选择要锁定的字段"
            options={EDITABLE_FIELDS.map((x) => ({ value: x.key, label: x.label }))}
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}
