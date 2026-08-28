import { useState } from 'react'
import { AiAdvisorPanel } from '../components/AiAdvisorPanel'
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Col,
  Form,
  InputNumber,
  Popconfirm,
  Row,
  Select,
  Space,
  Switch,
  TimePicker,
  Typography,
} from 'antd'
import { useApi } from '../hooks/useApi'
import { adminApi } from '../api/admin'
import { formatApiError } from '../api/client'
import type { PolicyResp, PolicyRules, PolicyUsage } from '../types/admin'
import { ErrorState } from '../components/ErrorState'
import {
  normalizeLimit,
  rowsToWindows,
  toLimitOrNull,
  validateWindowRow,
  windowsToRows,
  type WindowRow,
} from '../utils/policy'

interface FormValues {
  session_limit_minutes: number | null
  daily_episode_limit: number | null
  windows: WindowRow[]
  autoplay: boolean
  course_counts_as_entertainment: boolean
  blocked_tags: string[]
  screen_total_minutes: number | null
  entertainment_minutes: number | null
  learning_minutes: number | null
  audio_minutes: number | null
  ai_voice_minutes: number | null
  offscreen_allowed: boolean
  transition_enabled: boolean
  transition_types: string[]
  transition_max_minutes: number
  transition_daily_offer_limit: number
}

const TRANSITION_TYPE_OPTIONS = [
  { value: 'knowledge', label: '聊一聊刚看的内容' },
  { value: 'quiz', label: '小问答' },
  { value: 'roleplay', label: '角色扮演' },
  { value: 'vocabulary', label: '学几个新单词' },
  { value: 'song_story', label: '听个故事 / 儿歌' },
  { value: 'offscreen_game', label: '离屏小游戏' },
  { value: 'real_explore', label: '去探索身边的东西' },
]

/** 分钟输入：留空 = 不限（对家长“不限”比"null"好懂；引擎把 0 视为禁止）。 */
function MinuteInput(props: { placeholder?: string }) {
  return (
    <InputNumber
      min={1}
      max={1440}
      precision={0}
      style={{ width: 130 }}
      placeholder="不限"
      {...props}
    />
  )
}

/**
 * 屏幕时间（2026-08-25 产品决策重构：“家庭规则”让家长困惑）。
 * 四个问题分组：①每天能用多久 ②什么时间能看 ③播放与内容 ④动画时间到之后。
 * v1 遗留“每日可观看时长”不再单独暴露（总屏幕时间即其升级形态，保存时同步写入）。
 */
export function PolicyPage() {
  const { data, error, reload } = useApi<PolicyResp>('/api/v1/admin/policy')
  const usage = useApi<PolicyUsage>('/api/v1/admin/policy/usage')

  if (error && !data) {
    return (
      <Card title="屏幕时间">
        <ErrorState error={error} onRetry={reload} />
      </Card>
    )
  }
  if (!data) return <Card loading title="屏幕时间" />

  return (
    <>
      {/* 今日剩余放页顶：家长进页第一眼看到当前状态，再往下调规则（产品决策） */}
      <UsagePreview usage={usage.data ?? undefined} />
      <AiAdvisorPanel
        variant="policy"
        onChanged={() => {
          reload()
          usage.reload()
        }}
      />
      <PolicyForm
        key={data.version}
        resp={data}
        onSaved={() => {
          reload()
          usage.reload()
        }}
      />
    </>
  )
}

function fmtRemaining(seconds: number | undefined | null): string {
  if (seconds === undefined || seconds === null) return '—'
  if (seconds === 0) return '已用完'
  return `${Math.floor(seconds / 60)} 分钟`
}

/** 今日剩余量预览（与 TV 判定同源计量）。 */
function UsagePreview({ usage }: { usage: PolicyUsage | undefined }) {
  if (!usage) return null
  const rows: { key: string; label: string; seconds: number | undefined }[] = [
    { key: 'ent', label: '动画', seconds: usage.video_entertainment.video_class_seconds },
    { key: 'total', label: '总屏幕时间', seconds: usage.video_entertainment.screen_total_seconds },
    { key: 'learn', label: '学习视频', seconds: usage.video_learning.video_class_seconds },
    { key: 'audio', label: '听故事 / 儿歌', seconds: usage.audio.audio_seconds },
    { key: 'ai', label: '和 AI 聊天', seconds: usage.ai_voice.ai_voice_seconds },
  ]
  return (
    <Card size="small" title="今天还剩多少（与电视判定同源，实时）">
      <Row gutter={[16, 8]}>
        {rows.map((r) => (
          <Col key={r.key} span={8}>
            <Typography.Text>
              {r.label}：
              <Typography.Text strong> {fmtRemaining(r.seconds)}</Typography.Text>
            </Typography.Text>
          </Col>
        ))}
        <Col span={24}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            成长接力已发起 {usage.transition_offered_today} / {usage.transition_daily_limit} 次 ·{' '}
            {usage.note}
          </Typography.Text>
        </Col>
      </Row>
    </Card>
  )
}

function PolicyForm({ resp, onSaved }: { resp: PolicyResp; onSaved: () => void }) {
  const [form] = Form.useForm<FormValues>()
  const [base] = useState<PolicyRules>(resp.rules)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const { message } = AntApp.useApp()

  const onSave = async () => {
    let values: FormValues
    try {
      values = await form.validateFields()
    } catch {
      return
    }
    for (const row of values.windows ?? []) {
      const err = validateWindowRow(row)
      if (err) {
        message.warning(err)
        return
      }
    }
    const rules: PolicyRules = {
      ...base,
      // v1 遗留字段随总屏幕时间写入（引擎升维兼容，UI 不再单独暴露）
      daily_limit_minutes: toLimitOrNull(values.screen_total_minutes),
      session_limit_minutes: toLimitOrNull(values.session_limit_minutes),
      daily_episode_limit: toLimitOrNull(values.daily_episode_limit),
      allowed_windows: rowsToWindows(values.windows ?? []),
      autoplay: values.autoplay,
      course_counts_as_entertainment: values.course_counts_as_entertainment,
      content_scope: { ...base.content_scope, blocked_tags: values.blocked_tags ?? [] },
      budgets: {
        screen_total_minutes: toLimitOrNull(values.screen_total_minutes),
        video_by_class: {
          ENTERTAINMENT: toLimitOrNull(values.entertainment_minutes),
          LEARNING: toLimitOrNull(values.learning_minutes),
        },
        audio_minutes: toLimitOrNull(values.audio_minutes),
        ai_voice_minutes: toLimitOrNull(values.ai_voice_minutes),
      },
      offscreen: {
        allowed: values.offscreen_allowed,
        offer_enabled: values.offscreen_allowed,
      },
      transition_policy: {
        ...(typeof base.transition_policy === 'object' && base.transition_policy
          ? (base.transition_policy as Record<string, unknown>)
          : {}),
        enabled: values.transition_enabled,
        types: values.transition_types,
        max_minutes: values.transition_max_minutes,
        daily_offer_limit: values.transition_daily_offer_limit,
      },
    }
    setSaving(true)
    try {
      const r = await adminApi.policyPut(rules)
      message.success(
        r.revoked_playbacks > 0
          ? `已保存并立即生效；${r.revoked_playbacks} 个进行中的播放已停止`
          : '已保存并立即生效',
      )
      onSaved()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card
      style={{ marginTop: 16 }}
      title={`屏幕时间（规则版本 v${resp.version}）`}
      extra={
        <Popconfirm
          title="保存并立即生效？"
          description="收紧时段可能立刻停止孩子正在看的内容；时长类限制不打断当前一集，只拦下一次。"
          okText="保存"
          cancelText="取消"
          onConfirm={onSave}
        >
          <Button type="primary" loading={saving}>
            保存{dirty ? '（有未保存修改）' : ''}
          </Button>
        </Popconfirm>
      }
    >
      <Form
        form={form}
        layout="vertical"
        onValuesChange={() => setDirty(true)}
        initialValues={{
          session_limit_minutes: normalizeLimit(base.session_limit_minutes),
          daily_episode_limit: normalizeLimit(base.daily_episode_limit),
          windows: windowsToRows(base.allowed_windows),
          screen_total_minutes: normalizeLimit(
            (base.budgets as { screen_total_minutes?: number | null })
              ?.screen_total_minutes ?? base.daily_limit_minutes),
          entertainment_minutes: normalizeLimit(
            (base.budgets as { video_by_class?: Record<string, number | null> })
              ?.video_by_class?.ENTERTAINMENT ?? base.daily_limit_minutes),
          learning_minutes: normalizeLimit(
            (base.budgets as { video_by_class?: Record<string, number | null> })
              ?.video_by_class?.LEARNING ?? base.daily_limit_minutes),
          audio_minutes: normalizeLimit(
            (base.budgets as { audio_minutes?: number | null })?.audio_minutes),
          ai_voice_minutes: normalizeLimit(
            (base.budgets as { ai_voice_minutes?: number | null })?.ai_voice_minutes),
          offscreen_allowed: (base.offscreen as { allowed?: boolean })?.allowed ?? true,
          transition_enabled: (base.transition_policy as { enabled?: boolean })?.enabled ?? true,
          transition_types:
            (base.transition_policy as { types?: string[] })?.types ?? [
              'knowledge',
              'quiz',
              'roleplay',
              'vocabulary',
              'song_story',
              'offscreen_game',
              'real_explore',
            ],
          transition_max_minutes:
            (base.transition_policy as { max_minutes?: number })?.max_minutes ?? 4,
          transition_daily_offer_limit:
            (base.transition_policy as { daily_offer_limit?: number })?.daily_offer_limit ?? 3,
          autoplay: base.autoplay,
          course_counts_as_entertainment: base.course_counts_as_entertainment,
          blocked_tags: base.content_scope?.blocked_tags ?? [],
        }}
      >
        <Typography.Title level={5}>① 每天能用多久</Typography.Title>
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="按种类分别计时、互不挤占：动画时间用完不影响听故事；学习视频单独计时。"
        />
        <Row gutter={[24, 0]}>
          <Col>
            <Form.Item
              name="screen_total_minutes"
              label="总屏幕时间（分钟/天）"
              tooltip="所有视频共用的一道总闸"
            >
              <MinuteInput />
            </Form.Item>
          </Col>
          <Col>
            <Form.Item
              name="entertainment_minutes"
              label="动画 / 娱乐视频（分钟/天）"
              tooltip="动画时间用完当天不能再看动画，但不影响学习视频和听故事"
            >
              <MinuteInput placeholder="沿用总屏幕" />
            </Form.Item>
          </Col>
          <Col>
            <Form.Item
              name="learning_minutes"
              label="学习视频（分钟/天）"
              tooltip="科普、课程类视频单独计时"
            >
              <MinuteInput placeholder="沿用总屏幕" />
            </Form.Item>
          </Col>
          <Col>
            <Form.Item
              name="audio_minutes"
              label="听故事 / 儿歌（分钟/天）"
              tooltip="纯音频不占屏幕时间，单独计量"
            >
              <MinuteInput />
            </Form.Item>
          </Col>
          <Col>
            <Form.Item
              name="ai_voice_minutes"
              label="和 AI 聊天（分钟/天）"
              tooltip="语音对话（含成长接力聊天）单独计量"
            >
              <MinuteInput />
            </Form.Item>
          </Col>
        </Row>

        <Typography.Title level={5}>② 什么时间能看</Typography.Title>
        <Form.Item
          label="可观看时间段"
          style={{ marginBottom: 4 }}
          extra="到点会停止播放（硬截止）；不添加时段 = 全天都可以"
        >
          <Form.List name="windows">
            {(fields, { add, remove }) => (
              <>
                {fields.map(({ key, name }) => (
                  <Space
                    key={key}
                    size={8}
                    style={{ display: 'flex', marginBottom: 8 }}
                    align="center"
                  >
                    <Form.Item
                      name={[name, 'start']}
                      noStyle
                      rules={[{ required: true, message: '开始时间' }]}
                    >
                      <TimePicker format="HH:mm" needConfirm={false} minuteStep={5} placeholder="开始" />
                    </Form.Item>
                    <span>至</span>
                    <Form.Item
                      name={[name, 'end']}
                      noStyle
                      rules={[{ required: true, message: '结束时间' }]}
                    >
                      <TimePicker format="HH:mm" needConfirm={false} minuteStep={5} placeholder="结束" />
                    </Form.Item>
                    <Button type="text" danger onClick={() => remove(name)}>
                      删除
                    </Button>
                  </Space>
                ))}
                <Space size={12}>
                  <Button type="dashed" onClick={() => add({ start: null, end: null })}>
                    添加时段（如 16:30–19:30）
                  </Button>
                  {fields.length > 0 && (
                    <Button type="link" danger onClick={() => form.setFieldValue('windows', [])}>
                      清除全部（全天允许）
                    </Button>
                  )}
                </Space>
              </>
            )}
          </Form.List>
        </Form.Item>

        <Typography.Title level={5} style={{ marginTop: 24 }}>
          ③ 播放与内容
        </Typography.Title>
        <Row gutter={[24, 0]}>
          <Col>
            <Form.Item
              name="autoplay"
              label="自动连播"
              valuePropName="checked"
              tooltip="关闭后，每一集播完都停在结束页，需要孩子主动选下一集"
            >
              <Switch checkedChildren="开" unCheckedChildren="关" />
            </Form.Item>
          </Col>
          <Col>
            <Form.Item
              name="session_limit_minutes"
              label="单次最长（分钟）"
              tooltip="一次连续观看的上限"
            >
              <MinuteInput />
            </Form.Item>
          </Col>
          <Col>
            <Form.Item
              name="daily_episode_limit"
              label="每天最多集数"
              tooltip="按看完的集数计"
            >
              <MinuteInput />
            </Form.Item>
          </Col>
          <Col>
            <Form.Item
              name="course_counts_as_entertainment"
              label="课程计入动画时间"
              valuePropName="checked"
              tooltip="关闭后，课程类视频不占动画时间和总屏幕时间"
            >
              <Switch checkedChildren="计入" unCheckedChildren="单独" />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item
          name="blocked_tags"
          label="屏蔽内容标签"
          extra="带这些标签的内容对孩子完全不可见"
          style={{ maxWidth: 420 }}
        >
          <Select
            mode="tags"
            tokenSeparators={['、', ',', ' ']}
            open={false}
            placeholder="如：恐怖、暴力"
          />
        </Form.Item>

        <Typography.Title level={5} style={{ marginTop: 24 }}>
          ④ 动画时间到之后（成长接力）
        </Typography.Title>
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="动画时间用完时，AI 会主动接住孩子的兴趣：聊一聊刚看的内容、听个相关故事、或去做个小活动——孩子可以拒绝，拒绝就结束，不反复劝。"
        />
        <Row gutter={[24, 0]}>
          <Col>
            <Form.Item name="transition_enabled" label="启用成长接力" valuePropName="checked">
              <Switch checkedChildren="开" unCheckedChildren="关" />
            </Form.Item>
          </Col>
          <Col>
            <Form.Item name="transition_max_minutes" label="最长聊几分钟（推荐 3–5）">
              <InputNumber min={1} max={10} style={{ width: 100 }} />
            </Form.Item>
          </Col>
          <Col>
            <Form.Item name="transition_daily_offer_limit" label="每天最多发起几次">
              <InputNumber min={1} max={10} style={{ width: 100 }} />
            </Form.Item>
          </Col>
          <Col>
            <Form.Item
              name="offscreen_allowed"
              label="推荐离屏活动"
              valuePropName="checked"
              tooltip="关闭后接力只提供聊天和听故事，不再推荐离开屏幕的小活动"
            >
              <Switch checkedChildren="推荐" unCheckedChildren="不推荐" />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="transition_types" label="允许的接力方式">
          <Select
            mode="multiple"
            options={TRANSITION_TYPE_OPTIONS}
            placeholder="默认全部允许"
            style={{ maxWidth: 560 }}
          />
        </Form.Item>
      </Form>
    </Card>
  )
}
