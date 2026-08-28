import { useState } from 'react'
import {
  App as AntApp,
  Badge,
  Button,
  Card,
  Checkbox,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { ClearOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useApi } from '../hooks/useApi'
import { adminApi } from '../api/admin'
import { formatApiError } from '../api/client'
import type { Device, PendingPairing } from '../types/admin'
import { fmtDateTime, fromNow } from '../utils/format'

export function DevicesPage() {
  const pendingQ = useApi<{ pending: PendingPairing[] }>('/api/v1/admin/pairing/requests', {
    pollMs: 5000,
  })
  const devicesQ = useApi<{ devices: Device[] }>('/api/v1/admin/devices', { pollMs: 5000 })
  const { message } = AntApp.useApp()
  const [busyId, setBusyId] = useState<string | null>(null)
  const [cleanupOpen, setCleanupOpen] = useState(false)
  const [cleanupBusy, setCleanupBusy] = useState(false)
  const [cleanupRevoked, setCleanupRevoked] = useState(true)
  const [cleanupDays, setCleanupDays] = useState(7)

  const pending = pendingQ.data?.pending ?? []
  const devices = devicesQ.data?.devices ?? []

  const cleanup = async () => {
    setCleanupBusy(true)
    try {
      const r = await adminApi.devicesCleanup({
        revoked: cleanupRevoked,
        offline_days: cleanupDays,
      })
      message.success(r.deleted > 0 ? `已清理 ${r.deleted} 台设备` : '没有符合条件的设备')
      setCleanupOpen(false)
      devicesQ.reload()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setCleanupBusy(false)
    }
  }

  const approve = async (p: PendingPairing, code: string) => {
    setBusyId(p.pairing_id)
    try {
      await adminApi.pairingApprove(p.pairing_id, code)
      message.success(`已批准「${p.device_name}」，设备端将自动获得访问授权`)
      pendingQ.reload()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setBusyId(null)
    }
  }

  const deny = async (p: PendingPairing) => {
    setBusyId(p.pairing_id)
    try {
      await adminApi.pairingDeny(p.pairing_id)
      message.success('已拒绝该配对请求')
      pendingQ.reload()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setBusyId(null)
    }
  }

  const revoke = async (d: Device) => {
    setBusyId(d.device_id)
    try {
      await adminApi.deviceRevoke(d.device_id)
      message.success(`已撤销「${d.name}」的访问授权`)
      devicesQ.reload()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setBusyId(null)
    }
  }

  const pendingColumns: ColumnsType<PendingPairing> = [
    { title: '设备名', dataIndex: 'device_name', key: 'device_name' },
    {
      title: '配对码',
      dataIndex: 'display_code',
      key: 'display_code',
      render: (c: string) => (
        <Typography.Text copyable className="pairing-code">
          {c}
        </Typography.Text>
      ),
    },
    { title: '实例', dataIndex: 'app_instance_id', key: 'app_instance_id' },
    {
      title: '状态',
      dataIndex: 'expired',
      key: 'expired',
      render: (expired: boolean) =>
        expired ? <Tag color="default">已过期</Tag> : <Tag color="processing">等待批准</Tag>,
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, p) => (
        <ApproveCell
          pending={p}
          disabled={p.expired || busyId === p.pairing_id}
          onApprove={approve}
          onDeny={deny}
        />
      ),
    },
  ]

  const deviceColumns: ColumnsType<Device> = [
    { title: '设备名', dataIndex: 'name', key: 'name' },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (s: string) =>
        s === 'active' ? (
          <Badge status="success" text="有效" />
        ) : (
          <Badge status="error" text="已撤销" />
        ),
    },
    {
      title: '在线',
      dataIndex: 'online',
      key: 'online',
      render: (o: boolean) => (
        <Badge status={o ? 'success' : 'default'} text={o ? '在线' : '离线'} />
      ),
    },
    {
      title: '配对时间',
      dataIndex: 'paired_at',
      key: 'paired_at',
      render: (s: string) => fmtDateTime(s),
    },
    {
      title: '最近活跃',
      dataIndex: 'last_seen_at',
      key: 'last_seen_at',
      render: (s: string | null) => (
        <Typography.Text title={fmtDateTime(s)}>{fromNow(s)}</Typography.Text>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, d) =>
        d.status === 'active' ? (
          <Popconfirm
            title="撤销该设备？"
            description="撤销将同时断开其实时连接、终止播放并作废媒体访问授权。"
            okText="撤销"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => revoke(d)}
          >
            <Button danger loading={busyId === d.device_id}>
              撤销
            </Button>
          </Popconfirm>
        ) : null,
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card title="待绑定设备" size="small">
        <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
          请核对电视上显示的 6 位配对码与下表一致后再输入批准，防止局域网内其他设备冒名连接。
        </Typography.Paragraph>
        {pendingQ.error && pending.length === 0 && (
          <Typography.Text type="danger">{pendingQ.error}</Typography.Text>
        )}
        <Table
          rowKey="pairing_id"
          columns={pendingColumns}
          dataSource={pending}
          pagination={false}
          size="small"
          loading={pendingQ.loading}
          locale={{ emptyText: '暂无待绑定设备。在电视端首次连接时会出现配对请求。' }}
        />
      </Card>

      <Card
        title="已登记设备"
        size="small"
        extra={
          <Button size="small" icon={<ClearOutlined />} onClick={() => setCleanupOpen(true)}>
            批量清理
          </Button>
        }
      >
        <Table
          rowKey="device_id"
          columns={deviceColumns}
          dataSource={devices}
          pagination={{ pageSize: 10, hideOnSinglePage: true, showSizeChanger: false, size: 'small' }}
          size="small"
          loading={devicesQ.loading}
        />
      </Card>

      <Modal
        title="批量清理设备"
        open={cleanupOpen}
        onCancel={() => setCleanupOpen(false)}
        onOk={cleanup}
        okText="清理"
        cancelText="取消"
        okButtonProps={{ danger: true, disabled: !cleanupRevoked && cleanupDays < 1 }}
        confirmLoading={cleanupBusy}
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
          清理测试残留或废弃的配对记录（硬删除）。在线设备不会被清理；被清理的设备想再次使用需重新配对。
        </Typography.Paragraph>
        <Space direction="vertical" size={10}>
          <Checkbox
            checked={cleanupRevoked}
            onChange={(e) => setCleanupRevoked(e.target.checked)}
          >
            已撤销的设备
          </Checkbox>
          <Space size={8}>
            <span>
              连续
              <InputNumber
                size="small"
                min={1}
                max={365}
                value={cleanupDays}
                onChange={(v) => setCleanupDays(v ?? 7)}
                style={{ width: 64, margin: '0 6px' }}
              />
              天未活跃的设备
            </span>
          </Space>
        </Space>
      </Modal>
    </Space>
  )
}

function ApproveCell({
  pending,
  disabled,
  onApprove,
  onDeny,
}: {
  pending: PendingPairing
  disabled: boolean
  onApprove: (p: PendingPairing, code: string) => void
  onDeny: (p: PendingPairing) => void
}) {
  const [code, setCode] = useState('')
  return (
    <Space>
      <Input
        placeholder="输入配对码"
        style={{ width: 120 }}
        value={code}
        maxLength={6}
        inputMode="numeric"
        disabled={disabled}
        onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
      />
      <Button
        type="primary"
        disabled={disabled || code.length !== 6}
        loading={disabled && !pending.expired}
        onClick={() => onApprove(pending, code)}
      >
        批准
      </Button>
      <Popconfirm
        title="拒绝该配对请求？"
        okText="拒绝"
        cancelText="取消"
        disabled={disabled}
        onConfirm={() => onDeny(pending)}
      >
        <Button danger disabled={disabled}>
          拒绝
        </Button>
      </Popconfirm>
    </Space>
  )
}
