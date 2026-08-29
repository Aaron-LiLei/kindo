import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Descriptions,
  Popconfirm,
  Progress,
  Space,
  Tag,
  Typography,
} from 'antd'
import {
  AudioOutlined,
  CheckCircleOutlined,
  DeleteOutlined,
  PauseCircleOutlined,
  ReloadOutlined,
  SoundOutlined,
} from '@ant-design/icons'
import { adminApi } from '../api/admin'
import { formatApiError } from '../api/client'
import { useApi } from '../hooks/useApi'
import { ErrorState } from '../components/ErrorState'
import type { VoiceProfileResp } from '../types/admin'

/**
 * 录音指定文本：与上传的 prompt_text 逐字一致（克隆质量依赖文本-音频严格对应，
 * 技术方案 §6.7）。长度按正常语速朗读约 10 秒设计（样本校验 3~15 秒）。
 */
const READING_TEXT =
  '宝贝你好，我最喜欢陪在你身边。以后我会给你讲好听的故事，放爱看的动画片，' +
  '还会回答你的小问题。不管什么时候，我都在这里陪着你，我们一起慢慢长大。'

const MAX_RECORD_SECONDS = 15
const MIN_RECORD_SECONDS = 3

function ttsServiceTag(data: VoiceProfileResp) {
  const s = data.tts_service
  if (!data.configured) return <Tag>合成服务未启用</Tag>
  if (s.ready && s.voice_loaded) {
    return (
      <Tag icon={<CheckCircleOutlined />} color="success">
        合成服务就绪
      </Tag>
    )
  }
  if (s.ready) {
    return (
      <Tag color="warning">合成服务待同步（保存样本后自动生效）</Tag>
    )
  }
  return <Tag color="error">合成服务不可用（{s.status}）· 回退系统语音</Tag>
}

/**
 * 家长声音页（PRD TTS-005~007）：家长朗读一段固定文本录入声音，
 * 之后 AI 回复用家长的声音播报，孩子听着更熟悉。
 * 隐私：录音只保存在这台主机上，不上网、不进日志，可随时删除（TTS-007）。
 */
export function VoicePage() {
  const { message } = AntApp.useApp()
  const { data, error, loading, reload } = useApi<VoiceProfileResp>(
    '/api/v1/admin/voice-profile',
  )
  const [recording, setRecording] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [draft, setDraft] = useState<Blob | null>(null)
  const [draftUrl, setDraftUrl] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [audioKey, setAudioKey] = useState(0)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const streamRef = useRef<MediaStream | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(
    () => () => {
      if (timerRef.current) clearInterval(timerRef.current)
      streamRef.current?.getTracks().forEach((t) => t.stop())
      if (draftUrl) URL.revokeObjectURL(draftUrl)
    },
    [draftUrl],
  )

  const startRecording = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      message.error('当前浏览器不支持录音，请使用较新版本的 Chrome/Edge')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(
        (m) => MediaRecorder.isTypeSupported(m),
      )
      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined)
      chunksRef.current = []
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType })
        setDraft(blob)
        setDraftUrl(URL.createObjectURL(blob))
        streamRef.current?.getTracks().forEach((t) => t.stop())
        streamRef.current = null
      }
      recorder.start()
      recorderRef.current = recorder
      setDraft(null)
      setElapsed(0)
      setRecording(true)
      timerRef.current = setInterval(() => {
        setElapsed((prev) => {
          const next = prev + 1
          if (next >= MAX_RECORD_SECONDS) {
            if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
            setRecording(false)
            return MAX_RECORD_SECONDS
          }
          return next
        })
      }, 1000)
    } catch {
      message.error('无法访问麦克风：请在浏览器地址栏允许使用麦克风后重试')
    }
  }, [message])

  const stopRecording = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
    recorderRef.current = null
    setRecording(false)
  }, [])

  const upload = useCallback(async () => {
    if (!draft) return
    const seconds = elapsed
    if (seconds < MIN_RECORD_SECONDS) {
      message.error(`录音太短了（${seconds} 秒），请读完文字再停（至少 ${MIN_RECORD_SECONDS} 秒）`)
      return
    }
    setUploading(true)
    try {
      await adminApi.voiceProfileUpload(draft, READING_TEXT)
      message.success('声音已保存，AI 会用你的声音和宝宝说话啦')
      setDraft(null)
      setDraftUrl(null)
      setElapsed(0)
      setAudioKey((k) => k + 1)
      reload()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setUploading(false)
    }
  }, [draft, elapsed, message, reload])

  const remove = useCallback(async () => {
    try {
      await adminApi.voiceProfileDelete()
      message.success('已删除声音，AI 恢复默认语音')
      setAudioKey((k) => k + 1)
      reload()
    } catch (e) {
      message.error(formatApiError(e))
    }
  }, [message, reload])

  if (error) return <ErrorState error={error} onRetry={reload} />

  const profile = data?.voice_profile

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card title="家长声音" extra={data && ttsServiceTag(data)}>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
          在这里录下你的声音后，AI 会用你的声音和宝宝对话，宝宝听着更亲切。录音
          <b>只保存在这台主机上</b>，不会发到互联网，也不会写进日志；随时可以删除，删除后 AI
          恢复默认语音。
        </Typography.Paragraph>
        {data && !data.configured && (
          <Alert
            type="info"
            showIcon
            message="当前未启用声音个性化"
            description="需要在 Hub 配置文件中设置 tts.endpoint 并部署 kindo-tts 组件后，本页录入的声音才会生效（见部署文档 apps/kindo-tts/README）。"
          />
        )}
        {data?.configured && data.clone_ready && (
          <Alert
            type="success"
            showIcon
            message="个性化语音已生效"
            description="AI 正在用你录制的声音说话。"
          />
        )}
      </Card>

      <Card title="录制声音">
        <Typography.Paragraph>
          请用平时和宝宝说话的语气，清楚地朗读下面的文字（约 10 秒）：
        </Typography.Paragraph>
        <Typography.Paragraph
          copyable
          style={{
            background: '#f6f7f9',
            padding: '12px 16px',
            borderRadius: 8,
            fontSize: 16,
            lineHeight: 1.9,
          }}
        >
          {READING_TEXT}
        </Typography.Paragraph>

        {recording ? (
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            <Space>
              <Button danger icon={<PauseCircleOutlined />} onClick={stopRecording}>
                停止（{elapsed} 秒）
              </Button>
              <Typography.Text type="secondary">正在录音，请朗读上面的文字…</Typography.Text>
            </Space>
            <Progress
              percent={Math.min(100, (elapsed / MAX_RECORD_SECONDS) * 100)}
              strokeColor={{ 0: '#1677ff', 100: '#faad14' }}
              showInfo={false}
            />
          </Space>
        ) : (
          <Space wrap>
            <Button type="primary" icon={<AudioOutlined />} onClick={startRecording}>
              {draft || profile?.configured ? '重新录制' : '开始录音'}
            </Button>
            {draft && (
              <>
                <Button
                  type="primary"
                  icon={<CheckCircleOutlined />}
                  loading={uploading}
                  onClick={upload}
                >
                  保存声音
                </Button>
                <Button
                  onClick={() => {
                    setDraft(null)
                    setDraftUrl(null)
                    setElapsed(0)
                  }}
                >
                  弃用重录
                </Button>
              </>
            )}
          </Space>
        )}

        {draftUrl && (
          <div style={{ marginTop: 12 }}>
            <Typography.Text type="secondary">试听刚才的录音：</Typography.Text>
            <audio controls src={draftUrl} style={{ display: 'block', marginTop: 8 }} />
          </div>
        )}
        {uploading && (
          <Progress percent={99} status="active" style={{ marginTop: 12, maxWidth: 360 }} />
        )}
      </Card>

      {profile?.configured && (
        <Card
          title="当前声音"
          extra={
            <Popconfirm
              title="删除后 AI 恢复默认语音，确定删除？"
              onConfirm={remove}
              okText="删除"
              okButtonProps={{ danger: true }}
            >
              <Button danger icon={<DeleteOutlined />}>
                删除声音
              </Button>
            </Popconfirm>
          }
        >
          <Descriptions column={1} size="small">
            <Descriptions.Item label="时长">
              {profile.duration_seconds?.toFixed(1)} 秒
            </Descriptions.Item>
            <Descriptions.Item label="录制文本">{profile.prompt_text}</Descriptions.Item>
            <Descriptions.Item label="回放">
              <audio
                key={audioKey}
                controls
                src={`${adminApi.voiceProfileAudioUrl()}?v=${audioKey}`}
              />
            </Descriptions.Item>
          </Descriptions>
          <Typography.Text type="secondary">
            <SoundOutlined /> 保存后无需重启：下一次 AI 回复自动使用该声音；克隆服务不可用时
            自动回退系统语音，不影响对话。
          </Typography.Text>
        </Card>
      )}

      {!loading && !profile?.configured && (
        <Typography.Text type="secondary">
          <ReloadOutlined /> 还没有录制声音。录好后 AI 的回复就会用你的声音播报。
        </Typography.Text>
      )}
    </Space>
  )
}
