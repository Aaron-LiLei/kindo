import { useCallback, useEffect, useState } from 'react'
import {
  App as AntApp,
  Badge,
  Button,
  Card,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { DeleteOutlined, EditOutlined, HistoryOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { useApi } from '../../hooks/useApi'
import { useScanJobs } from '../../hooks/useScanJobs'
import { adminApi } from '../../api/admin'
import { formatApiError } from '../../api/client'
import type { Mount, MountCreateBody, MountsPayload, ScanJob } from '../../types/admin'
import { ErrorState } from '../../components/ErrorState'
import { fmtDateTime, fromNow } from '../../utils/format'
import { mountLocation, scanJobMeta } from '../../utils/media'

type MountType = 'local' | 'smb' | 'webdav'

interface AddFormValues {
  type: MountType
  /** 本地=绝对路径；网络=子路径（提交时按类型映射 path/net_path） */
  path?: string
  host?: string
  port?: number
  share?: string
  url?: string
  username?: string
  password?: string
  label?: string
  probe_mode?: 'range' | 'skip' | 'full'
}

const PROBE_MODE_OPTIONS = [
  { value: 'range', label: '范围探测（推荐）——只读取文件的元数据字节（通常 <2MB），能拿到时长/编码/内嵌字幕' },
  { value: 'skip', label: '跳过探测——最快，不下载任何数据；时长未知，不影响播放' },
  { value: 'full', label: '完整探测——下载整个文件分析，最准但对网盘极慢，仅小库使用' },
]

/** 来源行：本地目录 / SMB / WebDAV 统一一张表（2026-08-25 全页面化决策）。 */
interface SourceRow {
  key: string
  name: string
  kind: MountType
  location: string
  storageId: string
  rowId: string
  active?: boolean
  credentialsConfigured?: boolean
  mount: Mount
}

/**
 * 媒体来源与扫描管理（替代旧 MountManager 的两张平铺表）：
 * 统一来源列表 + 网络源健康徽章（异步探测，离线 NAS 不阻塞页面）+ 扫描历史抽屉。
 */
export function SourcesManager({ onScanSettled }: { onScanSettled?: () => void }) {
  const { data, error, loading, reload } = useApi<MountsPayload>('/api/v1/admin/media-mounts')
  const { jobs, watch } = useScanJobs(onScanSettled ? () => onScanSettled() : undefined)
  const [form] = Form.useForm<AddFormValues>()
  const [busy, setBusy] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const { message } = AntApp.useApp()
  const mountType = Form.useWatch('type', form) ?? 'local'

  const onTest = async () => {
    let values: AddFormValues
    try {
      values = await form.validateFields()
    } catch {
      return
    }
    setTesting(true)
    setTestResult(null)
    try {
      const body: MountCreateBody = { mount_type: values.type }
      if (values.type === 'local') {
        body.path = (values.path ?? '').trim()
      } else {
        if (values.type === 'smb') {
          body.host = (values.host ?? '').trim()
          body.port = values.port || 445
          body.share = (values.share ?? '').trim()
        } else {
          body.url = (values.url ?? '').trim()
        }
        if (values.path?.trim()) body.net_path = values.path.trim()
        if (values.username?.trim()) body.username = values.username.trim()
        if (values.password) body.password = values.password
        if (values.probe_mode) body.probe_mode = values.probe_mode
      }
      const r = await adminApi.mountTest(body)
      setTestResult(r)
    } catch (e) {
      setTestResult({ ok: false, message: formatApiError(e) })
    } finally {
      setTesting(false)
    }
  }

  // 网络源健康：独立端点并行短超时探测，慢/离线不拖挂载列表。
  // 初始即 loading：effect 只发起请求，状态在异步回调收尾（react-hooks 规则）
  const [health, setHealth] = useState<Record<string, boolean> | null>(null)
  const [healthLoading, setHealthLoading] = useState(true)
  const loadHealth = useCallback(() => {
    adminApi
      .mountsHealth()
      .then((r) => {
        setHealth(Object.fromEntries(r.mounts.map((m) => [m.mount_id, m.healthy])))
      })
      .catch(() => setHealth(null))
      .finally(() => setHealthLoading(false))
  }, [])
  useEffect(() => {
    loadHealth()
  }, [loadHealth])

  const refreshHealth = () => {
    setHealthLoading(true) // 事件回调中置位，随后静默探测
    loadHealth()
  }

  const [historyOpen, setHistoryOpen] = useState(false)
  const [editing, setEditing] = useState<Mount | null>(null)

  const startScan = async (mountId: string, forceFull = false) => {
    try {
      const r = await adminApi.mountScan(mountId, forceFull)
      watch(mountId, r.job_id)
    } catch (e) {
      message.error(formatApiError(e)) // 含"已有进行中任务"的 409
    }
  }

  const toggle = async (mountId: string, active: boolean) => {
    try {
      await adminApi.mountPatch(mountId, { active })
      message.success(active ? '已启用该来源' : '已停用该来源')
      reload()
    } catch (e) {
      message.error(formatApiError(e))
    }
  }

  const remove = async (mountId: string) => {
    try {
      await adminApi.mountDelete(mountId)
      message.success('已删除：该来源入库的媒体与观看记录已清除（文件保留，已自动备份数据库）')
      reload()
      loadHealth()
      onScanSettled?.() // 联动刷新媒体库页签（否则旧列表残留到下次进页）
    } catch (e) {
      message.error(formatApiError(e))
    }
  }

  const onAdd = async (v: AddFormValues) => {
    setBusy(true)
    try {
      const body: MountCreateBody = { mount_type: v.type, label: v.label?.trim() || undefined }
      if (v.type === 'local') {
        body.path = (v.path ?? '').trim() // 服务器/容器内绝对路径
      } else {
        if (v.type === 'smb') {
          body.host = (v.host ?? '').trim()
          body.port = v.port || 445
          body.share = (v.share ?? '').trim()
        } else {
          body.url = (v.url ?? '').trim()
        }
        if (v.path?.trim()) body.net_path = v.path.trim()
        if (v.username?.trim()) body.username = v.username.trim()
        if (v.password) body.password = v.password // 写-only：仅提交，永不回显
      }
      const r = await adminApi.mountCreate(body)
      setTestResult(null)
      message.success(
        `已添加${r.mount_type === 'local' ? '挂载' : `${r.mount_type.toUpperCase()} 网络源`}「${r.label}」，开始扫描…`,
      )
      form.resetFields()
      reload()
      loadHealth()
      // 添加即扫描（首跑链路少一步：此前要自己在折叠表里找到新行点"扫描"）
      try {
        await adminApi.mountScan(r.mount_id)
      } catch {
        message.info('自动扫描未启动，请在来源行手动点「扫描」')
      }
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setBusy(false)
    }
  }

  if (error && !data) {
    return (
      <Card title="媒体来源" size="small">
        <ErrorState error={error} onRetry={reload} />
      </Card>
    )
  }

  const mounts: Mount[] = (data?.mounts ?? []).filter((m) => !m.deleted)
  const rows: SourceRow[] = mounts.map<SourceRow>((m) => ({
    key: `mount-${m.mount_id}`,
    name: m.label,
    kind: (m.mount_type as MountType) ?? 'local',
    location: mountLocation(m),
    storageId: m.storage_mount_id ?? m.mount_id,
    rowId: m.mount_id,
    active: m.active,
    credentialsConfigured: m.credentials_configured,
    mount: m,
  }))

  const columns: ColumnsType<SourceRow> = [
    {
      title: '来源',
      dataIndex: 'name',
      key: 'name',
      render: (_, r) => (
        <Space size={6} wrap>
          <Typography.Text strong>{r.name}</Typography.Text>
          <Tag color={r.kind === 'local' ? 'default' : 'geekblue'}>
            {r.kind === 'local' ? '本地' : r.kind.toUpperCase()}
          </Tag>
        </Space>
      ),
    },
    {
      title: '位置',
      dataIndex: 'location',
      key: 'location',
      ellipsis: { showTitle: true },
      render: (l: string) => (
        <Typography.Text style={{ fontSize: 12 }} ellipsis={{ tooltip: l }}>
          {l}
        </Typography.Text>
      ),
    },
    {
      title: '健康',
      key: 'health',
      width: 110,
      render: (_, r) => {
        if (r.kind === 'local') {
          return r.active ? <Badge status="success" text="就绪" /> : <Badge status="default" text="—" />
        }
        const h = health?.[r.storageId]
        if (h === undefined)
          return <Badge status="default" text={healthLoading ? '检测中…' : '未知'} />
        return h ? <Badge status="success" text="在线" /> : <Badge status="error" text="离线" />
      },
    },
    {
      title: '状态',
      key: 'active',
      width: 90,
      render: (_, r) => (
        <Badge status={r.active ? 'success' : 'default'} text={r.active ? '启用' : '停用'} />
      ),
    },
    {
      title: '扫描',
      key: 'scan',
      width: 230,
      render: (_, r) =>
        r.active ? (
          <ScanCell mountId={r.storageId} jobs={jobs} onScan={startScan} inline />
        ) : (
          '—'
        ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 210,
      render: (_, r) => (
        <Space size={4}>
          <Button size="small" icon={<EditOutlined />} onClick={() => setEditing(r.mount)}>
            编辑
          </Button>
          <Button size="small" onClick={() => toggle(r.rowId, !r.active)}>
            {r.active ? '停用' : '启用'}
          </Button>
          <Popconfirm
            title="删除该来源？"
            description="将同时删除其入库的全部媒体、观看记录与海报缓存（文件不会被删除；执行前自动备份数据库）。停用可保留资源。"
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => remove(r.rowId)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title={`媒体来源（${rows.length}）`}
        size="small"
        extra={
          <Space size={8}>
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={refreshHealth}
              loading={healthLoading}
            >
              健康检测
            </Button>
            <Button size="small" icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>
              扫描历史
            </Button>
            {loading && <Typography.Text type="secondary">刷新中…</Typography.Text>}
          </Space>
        }
      >
        {rows.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="还没有任何媒体来源：在下方添加本地目录（服务器/容器内路径）或 NAS 网络源"
          />
        ) : (
          <Table
            rowKey="key"
            columns={columns}
            dataSource={rows}
            pagination={false}
            size="small"
            scroll={{ x: 900 }}
          />
        )}
      </Card>

      <Card title="添加媒体来源" size="small">
        <Form
          form={form}
          layout="vertical"
          onFinish={onAdd}
          initialValues={{ type: 'local', port: 445 }}
          style={{ maxWidth: 680 }}
        >
          <Form.Item name="type" label="来源类型" rules={[{ required: true }]}>
            <Select
              style={{ width: 280 }}
              options={[
                { value: 'local', label: '本地目录（Hub 服务器/容器内路径）' },
                { value: 'smb', label: 'SMB / Windows 共享（NAS 局域网共享）' },
                { value: 'webdav', label: 'WebDAV（NAS / OpenList/AList 网盘）' },
              ]}
            />
          </Form.Item>

          {mountType === 'local' && (
            <Form.Item
              name="path"
              label="目录路径（服务器 / 容器内绝对路径）"
              rules={[{ required: true, message: '请输入目录路径' }]}
              extra="Docker 部署时为容器内路径（如 /media），宿主目录经 compose 卷映射进来；路径须已存在"
            >
              <Input style={{ width: 480 }} placeholder="如 /media 或 C:/media" />
            </Form.Item>
          )}

          {mountType === 'smb' && (
            <>
              <Space wrap size="middle">
                <Form.Item
                  name="host"
                  label="服务器主机"
                  rules={[{ required: true, message: '请输入主机' }]}
                >
                  <Input style={{ width: 240 }} placeholder="如 192.168.1.20 或 nas.local" />
                </Form.Item>
                <Form.Item name="port" label="端口" rules={[{ required: true }]}>
                  <InputNumber min={1} max={65535} style={{ width: 100 }} />
                </Form.Item>
              </Space>
              <Space wrap size="middle">
                <Form.Item
                  name="share"
                  label="共享名"
                  rules={[{ required: true, message: '请输入共享名' }]}
                >
                  <Input style={{ width: 200 }} placeholder="如 media" />
                </Form.Item>
                <Form.Item
                  name="path"
                  label="子路径（可选）"
                  extra="共享内的子目录；留空 = 整个共享"
                >
                  <Input style={{ width: 200 }} placeholder="可留空" />
                </Form.Item>
              </Space>
            </>
          )}

          {mountType === 'webdav' && (
            <>
              <Form.Item
                name="url"
                label="WebDAV 地址"
                rules={[
                  { required: true, message: '请输入地址' },
                  { pattern: /^https?:\/\/.+/, message: '需以 http:// 或 https:// 开头' },
                ]}
                extra="WebDAV 服务的地址，不是网页管理页。OpenList/AList 通常在 /dav 路径，如 http://127.0.0.1:15244/dav；群晖是 http://主机:5005"
              >
                <Input style={{ width: 420 }} placeholder="http://127.0.0.1:15244/dav" />
              </Form.Item>
              <Form.Item
                name="path"
                label="子路径（可选）"
                extra="网盘里要作为媒体库的子目录，如 baidu/淘福气成长；想挂整个网盘就留空"
                style={{ maxWidth: 480 }}
              >
                <Input placeholder="留空 = 挂载全部内容" />
              </Form.Item>
            </>
          )}

          {mountType !== 'local' && (
            <Form.Item
              name="probe_mode"
              label="媒体探测策略"
              initialValue="range"
              extra="探测决定能否获得时长/编码/内嵌字幕信息；范围探测只下载文件头尾的元数据字节"
              style={{ maxWidth: 720 }}
            >
              <Select options={PROBE_MODE_OPTIONS} />
            </Form.Item>
          )}

          {mountType !== 'local' && (
            <Space wrap size="middle" align="start">
              <Form.Item
                name="username"
                label="账号"
                extra="OpenList/AList 与 NAS 的 WebDAV 都需要账号"
              >
                <Input style={{ width: 180 }} autoComplete="off" />
              </Form.Item>
              <Form.Item
                name="password"
                label="密码"
                extra="保存后不回显（写-only）"
              >
                <Input.Password style={{ width: 200 }} autoComplete="new-password" />
              </Form.Item>
            </Space>
          )}

          <Form.Item
            name="label"
            label="显示名（可选）"
            extra="在来源列表里显示的名字；留空自动生成"
            style={{ maxWidth: 480 }}
          >
            <Input style={{ width: 240 }} placeholder="如：百度网盘、客厅 NAS" />
          </Form.Item>
          <Form.Item label=" " style={{ marginBottom: 0 }}>
            <Space size={12} align="center" wrap>
              {mountType !== 'local' && (
                <Button onClick={onTest} loading={testing}>
                  测试连接
                </Button>
              )}
              <Button type="primary" htmlType="submit" loading={busy} icon={<PlusOutlined />}>
                {mountType === 'local' ? '添加来源' : '添加网络源'}
              </Button>
              {testResult && (
                <Typography.Text type={testResult.ok ? 'success' : 'danger'}>
                  {testResult.ok ? '✓ ' : '✗ '}
                  {testResult.message}
                </Typography.Text>
              )}
            </Space>
          </Form.Item>
        </Form>
      </Card>

      <ScanHistoryDrawer open={historyOpen} onClose={() => setHistoryOpen(false)} />

      <EditMountModal
        mount={editing}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null)
          reload()
          loadHealth()
        }}
      />

    </Space>
  )
}

interface EditValues {
  label?: string
  /** 本地=绝对路径；网络=子路径（提交时按类型映射） */
  path?: string
  probe_mode?: 'range' | 'skip' | 'full'
  host?: string
  port?: number
  share?: string
  url?: string
  username?: string
  password?: string
}

/** 编辑媒体来源（ADM-002）：预填当前连接信息（密码永不回显，留空=不修改）。
 * 改路径/地址后需重新扫描增量对齐（旧文件标 missing、新文件入库，挂载身份不变）。 */
export function EditMountModal({
  mount,
  onClose,
  onSaved,
}: {
  mount: Mount | null
  onClose: () => void
  onSaved: () => void
}) {
  const [form] = Form.useForm<EditValues>()
  const [busy, setBusy] = useState(false)
  const { message } = AntApp.useApp()
  if (!mount) return null
  const kind = (mount.mount_type as MountType) ?? 'local'
  const cfg = (mount.config ?? {}) as Record<string, string | number | undefined>

  const onSave = async (v: EditValues) => {
    const body: Record<string, unknown> = {}
    const put = (key: keyof EditValues, value: unknown) => {
      if (v[key] !== undefined) body[key] = value
    }
    if ((v.label ?? '').trim() !== mount.label) put('label', (v.label ?? '').trim())
    if (kind === 'local') {
      const np = (v.path ?? '').trim()
      if (np !== (mount.path ?? '')) put('path', np)
    } else {
      const str = (k: string) => String(cfg[k] ?? '')
      if ((v.host ?? '').trim() !== str('host')) put('host', (v.host ?? '').trim())
      if ((v.port ?? 0) !== (Number(cfg.port) || 0)) put('port', v.port)
      if ((v.share ?? '').trim() !== str('share')) put('share', (v.share ?? '').trim())
      if ((v.url ?? '').trim() !== str('url')) put('url', (v.url ?? '').trim())
      if ((v.path ?? '').trim() !== str('path')) put('path', (v.path ?? '').trim())
      if ((v.username ?? '').trim() !== str('username')) put('username', (v.username ?? '').trim())
      if (v.password !== undefined && v.password !== '') put('password', v.password)
      if (v.probe_mode && v.probe_mode !== (mount.probe_mode ?? 'range')) put('probe_mode', v.probe_mode)
    }
    if (Object.keys(body).length === 0) {
      message.info('没有修改')
      onSaved()
      return
    }
    setBusy(true)
    try {
      await adminApi.mountPatch(mount.mount_id, body)
      message.success('已保存；路径或地址变更后请重新扫描以增量对齐')
      onSaved()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Drawer
      open
      onClose={onClose}
      width={520}
      title={`编辑来源 — ${mount.label}`}
      destroyOnHidden
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        {kind === 'local'
          ? '本地目录：可修改路径（服务器/容器内绝对路径，须已存在）与显示名；改动后需重新扫描。'
          : '网络源：修改连接信息后立即生效；密码为写-only（留空=不修改）。改动路径/地址后需重新扫描。'}
      </Typography.Paragraph>
      <Form
        form={form}
        layout="vertical"
        onFinish={onSave}
        initialValues={{
          label: mount.label,
          path: kind === 'local' ? (mount.path ?? '') : String(cfg.path ?? ''),
          probe_mode: (mount.probe_mode ?? 'range') as 'range' | 'skip' | 'full',
          host: cfg.host,
          port: cfg.port,
          share: cfg.share,
          url: cfg.url,
          username: cfg.username,
        }}
      >
        <Form.Item name="label" label="显示名">
          <Input style={{ width: 260 }} placeholder="留空自动生成" />
        </Form.Item>
        {kind === 'local' && (
          <Form.Item
            name="path"
            label="目录路径（服务器 / 容器内绝对路径）"
            rules={[{ required: true, message: '请输入目录路径' }]}
          >
            <Input style={{ width: 440 }} placeholder="如 /media 或 C:/media" />
          </Form.Item>
        )}
        {kind === 'smb' && (
          <>
            <Space wrap size="middle">
              <Form.Item name="host" label="服务器主机" rules={[{ required: true, message: '请输入主机' }]}>
                <Input style={{ width: 220 }} />
              </Form.Item>
              <Form.Item name="port" label="端口" rules={[{ required: true }]}>
                <InputNumber min={1} max={65535} style={{ width: 100 }} />
              </Form.Item>
            </Space>
            <Space wrap size="middle">
              <Form.Item name="share" label="共享名" rules={[{ required: true, message: '请输入共享名' }]}>
                <Input style={{ width: 180 }} />
              </Form.Item>
              <Form.Item name="path" label="子路径">
                <Input style={{ width: 200 }} placeholder="可留空" />
              </Form.Item>
            </Space>
          </>
        )}
        {kind === 'webdav' && (
          <Space wrap size="middle" align="start">
            <Form.Item
              name="url"
              label="WebDAV 地址"
              rules={[
                { required: true, message: '请输入地址' },
                { pattern: /^https?:\/\/.+/, message: '需以 http:// 或 https:// 开头' },
              ]}
            >
              <Input style={{ width: 320 }} />
            </Form.Item>
            <Form.Item name="path" label="子路径">
              <Input style={{ width: 180 }} placeholder="可留空" />
            </Form.Item>
          </Space>
        )}
        {kind !== 'local' && (
          <Space wrap size="middle" align="start">
            <Form.Item name="username" label="账号">
              <Input style={{ width: 180 }} autoComplete="off" placeholder="可留空（匿名）" />
            </Form.Item>
            <Form.Item
              name="password"
              label="密码"
              extra={mount.credentials_configured ? '已配置（写-only，不回显）' : '未配置'}
            >
              <Input.Password style={{ width: 200 }} autoComplete="new-password" placeholder="留空=不修改" />
            </Form.Item>
          </Space>
        )}
        <Space>
          <Button type="primary" htmlType="submit" loading={busy}>
            保存修改
          </Button>
          <Button onClick={onClose}>取消</Button>
        </Space>
      </Form>
    </Drawer>
  )
}

function ScanCell({
  mountId,
  jobs,
  onScan,
  inline,
}: {
  mountId: string
  jobs: Record<string, { state: string; progress: number; errorSummary: string | null }>
  onScan: (mountId: string, forceFull?: boolean) => void
  inline?: boolean
}) {
  const job = jobs[mountId]
  const running = job && (job.state === 'queued' || job.state === 'running')
  return (
    <Space size={8} wrap={false}>
      <Button size="small" onClick={() => onScan(mountId)} loading={!!running}>
        扫描
      </Button>
      <Popconfirm
        title="完整重扫？"
        description="忽略目录变化缓存，遍历整棵目录树（改动没被识别到时使用）"
        onConfirm={() => onScan(mountId, true)}
        disabled={!!running}
      >
        <Button size="small" type="text" disabled={!!running}>
          全量
        </Button>
      </Popconfirm>
      {job && (
        <Space size={6} direction={inline ? 'horizontal' : 'vertical'} style={{ minWidth: 140 }}>
          <Progress
            percent={Math.round(job.progress * 100)}
            size="small"
            status={
              job.state === 'failed'
                ? 'exception'
                : job.state === 'interrupted'
                  ? 'normal'
                  : job.state === 'done'
                    ? 'success'
                    : 'active'
            }
            style={{ width: inline ? 90 : 140, marginBottom: 0 }}
          />
          {job.state === 'failed' && job.errorSummary && (
            <Typography.Text type="danger" style={{ fontSize: 12 }} title={job.errorSummary}>
              失败
            </Typography.Text>
          )}
          {job.state === 'interrupted' && (
            <Typography.Text type="warning" style={{ fontSize: 12 }}>
              已中断
            </Typography.Text>
          )}
          {job.state === 'done' && (
            <Typography.Text type="success" style={{ fontSize: 12 }}>
              完成
            </Typography.Text>
          )}
        </Space>
      )}
    </Space>
  )
}

function ScanHistoryDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [jobs, setJobs] = useState<ScanJob[] | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  // stale-while-revalidate：打开抽屉静默刷新，旧数据先展示；状态在异步回调收尾
  const load = useCallback(() => {
    adminApi
      .scanJobs(20)
      .then((r) => {
        setJobs(r.jobs)
        setError('')
      })
      .catch((e) => setError(formatApiError(e)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  const refresh = () => {
    setLoading(true)
    load()
  }

  const columns: ColumnsType<ScanJob> = [
    {
      title: '来源',
      key: 'mount',
      render: (_, j) => <span title={j.mount_id}>{j.label || j.mount_id}</span>,
    },
    {
      title: '状态',
      dataIndex: 'state',
      key: 'state',
      width: 100,
      render: (s: string) => {
        const m = scanJobMeta(s)
        return <Tag color={m.color}>{m.text}</Tag>
      },
    },
    {
      title: '进度',
      dataIndex: 'progress',
      key: 'progress',
      width: 150,
      render: (p: number, j) => (
        <Progress
          percent={Math.round(p * 100)}
          size="small"
          status={j.state === 'failed' ? 'exception' : j.state === 'done' ? 'success' : 'active'}
        />
      ),
    },
    {
      title: '时间',
      dataIndex: 'finished_at',
      key: 'time',
      width: 200,
      render: (t: string | null, j) => (
        <Typography.Text style={{ fontSize: 12 }}>
          {t ? fromNow(t) : j.started_at ? `始于 ${fromNow(j.started_at)}` : '—'}
          <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 6 }}>
            {t ? fmtDateTime(t) : ''}
          </Typography.Text>
        </Typography.Text>
      ),
    },
    {
      title: '说明',
      dataIndex: 'error_summary',
      key: 'error_summary',
      ellipsis: true,
      render: (s: string | null) =>
        s ? (
          <Typography.Text type="danger" style={{ fontSize: 12 }} title={s}>
            {s}
          </Typography.Text>
        ) : (
          '—'
        ),
    },
  ]

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={680}
      title="扫描历史（最近 20 次）"
      extra={
        <Button size="small" icon={<ReloadOutlined />} onClick={refresh} loading={loading}>
          刷新
        </Button>
      }
    >
      {error && <ErrorState error={error} onRetry={load} />}
      {!error && jobs && jobs.length === 0 && (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有扫描记录" />
      )}
      {jobs && jobs.length > 0 && (
        <Table
          rowKey="id"
          columns={columns}
          dataSource={jobs}
          pagination={{ pageSize: 10, hideOnSinglePage: true, showSizeChanger: false, size: 'small' }}
          size="small"
          loading={loading}
        />
      )}
    </Drawer>
  )
}
