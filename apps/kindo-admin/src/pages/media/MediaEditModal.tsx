import { useState } from 'react'
import {
  App as AntApp,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Typography,
} from 'antd'
import { adminApi } from '../../api/admin'
import { formatApiError } from '../../api/client'
import type { MediaItem } from '../../types/admin'

interface EditValues {
  title: string
  language: string
  age_band: string
  characters: string[]
  themes: string[]
  tags: string[]
  series_name: string
  season_no: number | null
  episode_no: number | null
  clear_series: boolean
  course_name: string
  chapter_no: number | null
  lesson_no: number | null
  clear_course: boolean
}

export function MediaEditModal({
  media,
  onClose,
  onSaved,
}: {
  media: MediaItem | null
  onClose: () => void
  onSaved: () => void
}) {
  const [form] = Form.useForm<EditValues>()
  const [busy, setBusy] = useState(false)
  const { message } = AntApp.useApp()
  if (!media) return null

  const onSave = async (v: EditValues) => {
    const series = v.clear_series
      ? { name: null }
      : v.series_name.trim()
        ? { name: v.series_name.trim() }
        : undefined
    const course = v.clear_course
      ? { name: null }
      : v.course_name.trim()
        ? { name: v.course_name.trim() }
        : undefined
    if (series && course) {
      message.error('系列与课程互斥，一次只能归组一种')
      return
    }
    setBusy(true)
    try {
      await adminApi.mediaPatch(media.media_id, {
        title: v.title || undefined,
        language: v.language || undefined,
        age_band: v.age_band || undefined,
        characters: v.characters,
        themes: v.themes,
        tags: v.tags,
        ...(series && {
          series: {
            name: series.name,
            ...(v.season_no != null && { season_no: v.season_no }),
            ...(v.episode_no != null && { episode_no: v.episode_no }),
          },
        }),
        ...(course && {
          course: {
            name: course.name,
            ...(v.chapter_no != null && { chapter_no: v.chapter_no }),
            ...(v.lesson_no != null && { lesson_no: v.lesson_no }),
          },
        }),
      })
      message.success('已保存，家长修正不会被重新扫描覆盖')
      onSaved()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={`修正元数据 — ${media.title}`}
      open
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="保存"
      cancelText="取消"
      confirmLoading={busy}
      destroyOnHidden
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
        修正后的字段成为事实来源（parent_edited）。角色 / 主题供 AI
        语义检索（如“天天”“海洋”），回车或顿号分隔。
      </Typography.Paragraph>
      <Form
        form={form}
        layout="vertical"
        onFinish={onSave}
        initialValues={{
          title: media.title,
          language: media.language ?? '',
          age_band: media.age_band ?? '',
          characters: media.tags.characters ?? [],
          themes: media.tags.themes ?? [],
          tags: media.tags.tags ?? [],
          series_name: media.series?.title ?? '',
          season_no: media.series?.season_no ?? null,
          episode_no: media.series?.episode_no ?? null,
          clear_series: false,
          course_name: media.course?.title ?? '',
          chapter_no: media.course?.chapter_no ?? null,
          lesson_no: media.course?.lesson_no ?? null,
          clear_course: false,
        }}
      >
        <Form.Item name="title" label="标题">
          <Input />
        </Form.Item>
        <Space wrap size="middle" align="start">
          <Form.Item name="language" label="语言">
            <Input placeholder="zh-CN / en-US" style={{ width: 160 }} />
          </Form.Item>
          <Form.Item name="age_band" label="年龄段">
            <Input placeholder="3-6" style={{ width: 120 }} />
          </Form.Item>
        </Space>
        <Form.Item name="characters" label="角色">
          <Select
            mode="tags"
            tokenSeparators={['、', ',', ' ']}
            open={false}
            placeholder="如：天天、佩奇"
          />
        </Form.Item>
        <Form.Item name="themes" label="主题">
          <Select
            mode="tags"
            tokenSeparators={['、', ',', ' ']}
            open={false}
            placeholder="如：海洋、数字"
          />
        </Form.Item>
        <Form.Item name="tags" label="其他标签">
          <Select
            mode="tags"
            tokenSeparators={['、', ',', ' ']}
            open={false}
            placeholder="任意自定义标签"
          />
        </Form.Item>
        <Typography.Paragraph type="secondary" style={{ fontSize: 13, marginBottom: 8 }}>
          归组到系列或课程（两者互斥）；留空不修改，勾选“解除”则移出。网盘媒体也可在此归组，同样不会被重扫覆盖。
        </Typography.Paragraph>
        <Space wrap size="middle" align="start">
          <Form.Item name="series_name" label="系列名">
            <Input placeholder="如：汪汪队立大功" style={{ width: 200 }} />
          </Form.Item>
          <Form.Item name="season_no" label="季">
            <InputNumber min={1} style={{ width: 72 }} />
          </Form.Item>
          <Form.Item name="episode_no" label="集">
            <InputNumber min={1} style={{ width: 72 }} />
          </Form.Item>
          <Form.Item name="clear_series" valuePropName="checked" style={{ paddingTop: 30 }}>
            <Checkbox>解除系列归组</Checkbox>
          </Form.Item>
        </Space>
        <Space wrap size="middle" align="start">
          <Form.Item name="course_name" label="课程名">
            <Input placeholder="如：英语启蒙" style={{ width: 200 }} />
          </Form.Item>
          <Form.Item name="chapter_no" label="章">
            <InputNumber min={1} style={{ width: 72 }} />
          </Form.Item>
          <Form.Item name="lesson_no" label="课">
            <InputNumber min={1} style={{ width: 72 }} />
          </Form.Item>
          <Form.Item name="clear_course" valuePropName="checked" style={{ paddingTop: 30 }}>
            <Checkbox>解除课程归组</Checkbox>
          </Form.Item>
        </Space>
      </Form>
    </Modal>
  )
}
