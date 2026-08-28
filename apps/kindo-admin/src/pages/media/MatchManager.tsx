import { useState } from 'react'
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Checkbox,
  Collapse,
  Input,
  List,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Tag,
  Timeline,
  Typography,
} from 'antd'
import { useApi } from '../../hooks/useApi'
import { adminApi } from '../../api/admin'
import { formatApiError } from '../../api/client'
import type { MatchCandidate, MatchOverview, PendingMatch } from '../../types/admin'

const CONF_TAG: Record<string, { color: string; text: string }> = {
  exact: { color: 'green', text: '精确' },
  likely: { color: 'orange', text: '疑似' },
  fuzzy: { color: 'default', text: '模糊' },
}
const DECISION_TAG: Record<string, { color: string; text: string }> = {
  auto_apply: { color: 'blue', text: '自动绑定' },
  parent_confirm: { color: 'green', text: '家长确认' },
  parent_no_match: { color: 'default', text: '家长标记无匹配' },
  pending_saved: { color: 'orange', text: '缓存候选' },
}

/** 身份匹配管理（v0.3 决策三，ADM-012）：待确认候选直选/批量 + 手动搜索 +
 * 无匹配 + 决策时间线（confirmed/no_match 永不被刷新覆盖的审计依据）。 */
export function MatchManager() {
  // 轮询自愈：刮削在独立卡片/独立页运行时，本组件无需人工刷新即可跟进结果
  const { data, error, reload } = useApi<MatchOverview>('/api/v1/admin/match/overview', {
    pollMs: 10000,
  })
  const [searching, setSearching] = useState<PendingMatch | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const { message } = AntApp.useApp()

  const pending = data?.pending ?? []
  const selectedItems = pending.filter((it) => selected.has(it.entity_id))

  const toggleSelect = (entityId: string, checked: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (checked) next.add(entityId)
      else next.delete(entityId)
      return next
    })
  }

  const markNoMatch = async (entityId: string) => {
    setBusyId(entityId)
    try {
      await adminApi.matchConfirm(entityId, { no_match: true })
      message.success('已标记无匹配（永不被刷新覆盖）')
      reload()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setBusyId(null)
    }
  }

  const markAllNoMatch = async () => {
    if (!data?.no_candidates?.length) return
    setBulkBusy(true)
    let ok = 0
    let fail = 0
    try {
      for (const it of data.no_candidates) {
        try {
          await adminApi.matchConfirm(it.entity_id, { no_match: true })
          ok++
        } catch {
          fail++
        }
      }
      if (fail) message.warning(`成功 ${ok} 个，失败 ${fail} 个`)
      else message.success(`已标记 ${ok} 个为无匹配`)
      reload()
    } finally {
      setBulkBusy(false)
    }
  }

  /** 批量确认：对所选各项采用其当前显示的第一候选（与逐个点击首候选同一动作）。 */
  const batchConfirmSelected = async () => {
    if (!selectedItems.length) return
    setBulkBusy(true)
    let ok = 0
    let fail = 0
    try {
      for (const it of selectedItems) {
        const c = it.candidates?.[0]
        if (!c) {
          fail++
          continue
        }
        try {
          await adminApi.matchConfirm(it.entity_id, {
            ref_id: c.ref_id,
            title: c.title,
            first_air_date: c.first_air_date ?? '',
            poster_path: c.poster_path ?? '',
          })
          ok++
        } catch {
          fail++
        }
      }
      if (fail) message.warning(`确认 ${ok} 个，失败 ${fail} 个（可重试或逐个处理）`)
      else message.success(`已确认 ${ok} 个`)
      setSelected(new Set())
      reload()
    } finally {
      setBulkBusy(false)
    }
  }

  const batchNoMatchSelected = async () => {
    if (!selectedItems.length) return
    setBulkBusy(true)
    let ok = 0
    let fail = 0
    try {
      for (const it of selectedItems) {
        try {
          await adminApi.matchConfirm(it.entity_id, { no_match: true })
          ok++
        } catch {
          fail++
        }
      }
      if (fail) message.warning(`标记 ${ok} 个，失败 ${fail} 个`)
      else message.success(`已标记 ${ok} 个为无匹配`)
      setSelected(new Set())
      reload()
    } finally {
      setBulkBusy(false)
    }
  }

  if (error) {
    return (
      <Card title="身份匹配" size="small" style={{ marginTop: 16 }}>
        <Alert type="error" message={formatApiError(error)} />
      </Card>
    )
  }
  if (!data) {
    return (
      <Card title="身份匹配" size="small" style={{ marginTop: 16 }}>
        <Spin />
      </Card>
    )
  }

  const allSelected = pending.length > 0 && selectedItems.length === pending.length
  const counts = data.counts || {}
  return (
    <Card
      title="身份匹配（TMDB）"
      size="small"
      style={{ marginTop: 16 }}
      extra={
        <Space>
          <Tag>已确认 {counts.confirmed ?? 0}</Tag>
          <Tag color="blue">自动 {counts.auto ?? 0}</Tag>
          <Tag color="orange">待确认 {pending.length}</Tag>
          <Tag>无匹配 {counts.no_match ?? 0}</Tag>
        </Space>
      }
    >
      {(data.no_candidates || []).length > 0 && (
        <>
          <Typography.Title level={5} style={{ marginTop: 8 }}>
            未找到候选（{data.no_candidates!.length}）
          </Typography.Title>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
            TMDB（影视资料库）里没有这些内容——儿歌、自然拼读、分级读物类大多不在其中。
            标记“无匹配”后不再重复检索；海报可走本地图片（sidecar / overlay）。
          </Typography.Paragraph>
          <Space size={8} style={{ marginBottom: 8 }}>
            <Popconfirm
              title={`全部 ${data.no_candidates!.length} 个标记为无匹配？`}
              description="标记后这些内容不再参与 TMDB 检索（可随时重新确认匹配）"
              onConfirm={markAllNoMatch}
              okText="标记"
              cancelText="取消"
            >
              <Button size="small" loading={bulkBusy && !selectedItems.length}>
                全部标记无匹配
              </Button>
            </Popconfirm>
          </Space>
          <List
            size="small"
            dataSource={data.no_candidates}
            rowKey={(it) => it.entity_id}
            renderItem={(it) => (
              <List.Item
                actions={[
                  <Popconfirm
                    key="mark"
                    title="标记为无匹配？"
                    onConfirm={() => markNoMatch(it.entity_id)}
                    okText="标记"
                    cancelText="取消"
                  >
                    <Button size="small" loading={busyId === it.entity_id}>
                      标记无匹配
                    </Button>
                  </Popconfirm>,
                ]}
              >
                <Typography.Text>{it.title}</Typography.Text>
              </List.Item>
            )}
            pagination={{
              pageSize: 10,
              hideOnSinglePage: true,
              showSizeChanger: false,
              size: 'small',
            }}
          />
        </>
      )}
      <Typography.Title level={5} style={{ marginTop: 8 }}>
        待确认（{pending.length}）
      </Typography.Title>
      {pending.length === 0 ? (
        <Typography.Text type="secondary">暂无待确认的匹配</Typography.Text>
      ) : (
        <>
          {/* 批量操作条：几十个低置信系列不必逐个点（此前唯一批量只有无候选区） */}
          <Space size={8} style={{ marginBottom: 8 }} wrap>
            <Checkbox
              checked={allSelected}
              indeterminate={selectedItems.length > 0 && !allSelected}
              onChange={(e) =>
                setSelected(e.target.checked ? new Set(pending.map((it) => it.entity_id)) : new Set())
              }
            >
              全选（{pending.length}）
            </Checkbox>
            <Popconfirm
              title={`确认所选 ${selectedItems.length} 项？`}
              description="每项将采用其当前显示的第一候选（与逐个点击首候选相同）；低置信候选请先逐个核对。"
              onConfirm={batchConfirmSelected}
              okText="确认"
              cancelText="取消"
              disabled={selectedItems.length === 0}
            >
              <Button size="small" type="primary" disabled={selectedItems.length === 0} loading={bulkBusy}>
                确认所选（{selectedItems.length}）
              </Button>
            </Popconfirm>
            <Popconfirm
              title={`所选 ${selectedItems.length} 项标记为无匹配？`}
              description="标记后不再参与 TMDB 检索（可随时重新确认匹配）"
              onConfirm={batchNoMatchSelected}
              okText="标记"
              cancelText="取消"
              disabled={selectedItems.length === 0}
            >
              <Button size="small" disabled={selectedItems.length === 0} loading={bulkBusy}>
                所选标记无匹配
              </Button>
            </Popconfirm>
          </Space>
          <List
            size="small"
            dataSource={pending}
            rowKey={(it) => it.entity_id}
            pagination={{
              pageSize: 10,
              hideOnSinglePage: true,
              showSizeChanger: false,
              size: 'small',
            }}
            renderItem={(it) => (
              <List.Item
                actions={[
                  <Button
                    key="search"
                    size="small"
                    onClick={() => setSearching(it)}
                  >
                    手动搜索
                  </Button>,
                  <NoMatchButton key="nomatch" entity={it} onDone={reload} />,
                ]}
              >
                <List.Item.Meta
                  avatar={
                    <Checkbox
                      checked={selected.has(it.entity_id)}
                      onChange={(e) => toggleSelect(it.entity_id, e.target.checked)}
                    />
                  }
                  title={
                    <Space>
                      {it.title}
                      <Tag>{it.entity_type === 'series' ? '系列' : '电影'}</Tag>
                    </Space>
                  }
                  description={
                    <Space wrap size={[8, 4]}>
                      {(it.candidates || []).map((c) => (
                        <CandidateButton
                          key={c.ref_id}
                          entity={it}
                          candidate={c}
                          onDone={reload}
                        />
                      ))}
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        </>
      )}
      {searching && (
        <SearchModal
          entity={searching}
          onClose={() => setSearching(null)}
          onDone={reload}
        />
      )}
      <DecisionTimeline />
    </Card>
  )
}

/** 决策时间线（ADM-012）：确认/无匹配留痕，永不被 Provider Refresh 覆盖的依据。 */
function DecisionTimeline() {
  const { data, error } = useApi<{ decisions: import('../../types/admin').RecentMatchDecision[] }>(
    '/api/v1/admin/match/decisions/recent?limit=50',
  )
  return (
    <Collapse
      size="small"
      style={{ marginTop: 16 }}
      items={[
        {
          key: 'decisions',
          label: `决策记录${data?.decisions?.length ? `（最近 ${data.decisions.length} 条）` : ''}`,
          children: error ? (
            <Typography.Text type="danger">{formatApiError(error)}</Typography.Text>
          ) : !data ? (
            <Spin size="small" />
          ) : data.decisions.length === 0 ? (
            <Typography.Text type="secondary">还没有任何匹配决策</Typography.Text>
          ) : (
            <Timeline
              items={data.decisions.map((d) => {
                const tag = DECISION_TAG[d.decision]
                const conf = CONF_TAG[d.confidence]
                return {
                  children: (
                    <Space direction="vertical" size={2}>
                      <Space size={6} wrap>
                        {tag && <Tag color={tag.color}>{tag.text}</Tag>}
                        <Typography.Text strong>{d.entity_title}</Typography.Text>
                        {d.candidate?.title && d.candidate.title !== d.entity_title && (
                          <Typography.Text type="secondary">→ {d.candidate.title}</Typography.Text>
                        )}
                        {conf && <Tag bordered={false}>{conf.text}</Tag>}
                      </Space>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {new Date(d.created_at).toLocaleString()} · {d.provider} ·{' '}
                        {d.decided_by === 'parent' ? '家长' : '自动'}
                      </Typography.Text>
                    </Space>
                  ),
                }
              })}
            />
          ),
        },
      ]}
    />
  )
}

function CandidateButton({
  entity,
  candidate,
  onDone,
}: {
  entity: PendingMatch
  candidate: MatchCandidate
  onDone: () => void
}) {
  const [loading, setLoading] = useState(false)
  const { message } = AntApp.useApp()
  const conf = CONF_TAG[candidate.confidence ?? 'fuzzy']
  return (
    <Button
      size="small"
      loading={loading}
      onClick={async () => {
        setLoading(true)
        try {
          await adminApi.matchConfirm(entity.entity_id, {
            ref_id: candidate.ref_id,
            title: candidate.title,
            first_air_date: candidate.first_air_date ?? '',
            poster_path: candidate.poster_path ?? '',
          })
          message.success(`已确认：${candidate.title}`)
          onDone()
        } catch (e) {
          message.error(formatApiError(e))
        } finally {
          setLoading(false)
        }
      }}
    >
      {candidate.title}
      {candidate.first_air_date ? `（${candidate.first_air_date.slice(0, 4)}）` : ''}
      {conf && <Tag color={conf.color} style={{ marginLeft: 6 }}>{conf.text}</Tag>}
    </Button>
  )
}

function NoMatchButton({ entity, onDone }: { entity: PendingMatch; onDone: () => void }) {
  const [loading, setLoading] = useState(false)
  const { message } = AntApp.useApp()
  return (
    <Button
      size="small"
      loading={loading}
      onClick={async () => {
        setLoading(true)
        try {
          await adminApi.matchConfirm(entity.entity_id, { no_match: true })
          message.success('已标记无匹配（不再自动重试）')
          onDone()
        } catch (e) {
          message.error(formatApiError(e))
        } finally {
          setLoading(false)
        }
      }}
    >
      无匹配
    </Button>
  )
}

function SearchModal({
  entity,
  onClose,
  onDone,
}: {
  entity: PendingMatch
  onClose: () => void
  onDone: () => void
}) {
  const [query, setQuery] = useState(entity.title)
  const [results, setResults] = useState<MatchCandidate[] | null>(null)
  const [loading, setLoading] = useState(false)
  const { message } = AntApp.useApp()

  const doSearch = async () => {
    if (!query.trim()) return
    setLoading(true)
    try {
      const r = await adminApi.matchSearch(query, entity.entity_id)
      setResults(r.candidates || [])
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      open
      title={`搜索匹配：${entity.title}`}
      onCancel={onClose}
      footer={null}
      width={640}
    >
      <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onPressEnter={doSearch}
          placeholder="输入作品名（中文或原文）"
        />
        <Button type="primary" loading={loading} onClick={doSearch}>
          搜索
        </Button>
      </Space.Compact>
      <List
        size="small"
        dataSource={results ?? []}
        locale={{ emptyText: results ? '无结果' : '输入关键词搜索 TMDB' }}
        renderItem={(c) => (
          <List.Item
            actions={[
              <Button
                key="confirm"
                size="small"
                type="primary"
                onClick={async () => {
                  try {
                    await adminApi.matchConfirm(entity.entity_id, {
                      ref_id: c.ref_id,
                      title: c.title,
                      first_air_date: c.first_air_date ?? '',
                      poster_path: c.poster_path ?? '',
                    })
                    message.success(`已确认：${c.title}`)
                    onDone()
                    onClose()
                  } catch (e) {
                    message.error(formatApiError(e))
                  }
                }}
              >
                确认
              </Button>,
            ]}
          >
            {c.title}
            {c.original_title && c.original_title !== c.title
              ? ` · ${c.original_title}`
              : ''}
            {c.first_air_date ? `（${c.first_air_date.slice(0, 4)}）` : ''}
          </List.Item>
        )}
      />
    </Modal>
  )
}
