import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { App as AntApp, Button, Layout, Menu, Spin } from 'antd'
import {
  BarChartOutlined,
  DashboardOutlined,
  FolderOpenOutlined,
  IdcardOutlined,
  LogoutOutlined,
  RobotOutlined,
  SafetyOutlined,
  AudioOutlined,
  DesktopOutlined,
  SettingOutlined,
  GiftOutlined,
} from '@ant-design/icons'
import { KINDO_UNAUTHORIZED_EVENT, logout } from './api/client'
import { adminApi } from './api/admin'
import type { AuthState } from './types/admin'
import { ErrorBoundary } from './components/ErrorBoundary'
import { LoginPage, SetupPage } from './pages/Login'
import { HealthPage } from './pages/Health'
import { MediaPage } from './pages/media/MediaPage'
import { PipelinePage } from './pages/Pipeline'
import { PolicyPage } from './pages/Policy'
import { AnalyticsPage } from './pages/Analytics'
import { ActivitiesPage } from './pages/Activities'
import { ModelsPage } from './pages/Models'
import { VoicePage } from './pages/Voice'
import { DevicesPage } from './pages/Devices'
import { SettingsPage } from './pages/Settings'

const NAV_ITEMS = [
  { key: 'health', icon: <DashboardOutlined />, label: '概览 / 状态' },
  { key: 'media', icon: <FolderOpenOutlined />, label: '媒体库' },
  { key: 'pipeline', icon: <IdcardOutlined />, label: '刮削与匹配' },
  { key: 'policy', icon: <SafetyOutlined />, label: '屏幕时间' },
  { key: 'activities', icon: <GiftOutlined />, label: '活动库' },
  { key: 'analytics', icon: <BarChartOutlined />, label: '观看统计' },
  { key: 'models', icon: <RobotOutlined />, label: 'AI 模型' },
  { key: 'voice', icon: <AudioOutlined />, label: '家长声音' },
  { key: 'devices', icon: <DesktopOutlined />, label: '设备 / 配对' },
  { key: 'settings', icon: <SettingOutlined />, label: '设置' },
]

/**
 * 认证门卫：渲染哪个入口由服务端 /auth/state 状态机唯一决定——
 * setup_required → 初始化页；ready+未登录 → 登录页；ready+已登录 → 应用。
 * 会话过期（401 事件）、退出、初始化完成一律"重取状态"回流，URL 全程保留。
 */
export default function App() {
  const [auth, setAuth] = useState<AuthState | null>(null)
  const [stateTick, setStateTick] = useState(0)
  const { message } = AntApp.useApp()

  useEffect(() => {
    let cancelled = false
    adminApi
      .authState()
      .then((s) => {
        if (!cancelled) setAuth(s)
      })
      .catch(() => {
        // 状态接口不可达时按"已初始化但未登录"兜底，保持可登录
        if (!cancelled) setAuth({ phase: 'ready', authenticated: false, username: null })
      })
    return () => {
      cancelled = true
    }
  }, [stateTick])

  const refreshAuth = useCallback(() => setStateTick((t) => t + 1), [])

  useEffect(() => {
    const onUnauthorized = () => {
      refreshAuth()
      message.warning('登录已过期，请重新登录')
    }
    window.addEventListener(KINDO_UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(KINDO_UNAUTHORIZED_EVENT, onUnauthorized)
  }, [message, refreshAuth])

  const handleLogout = useCallback(async () => {
    try {
      await logout()
      message.success('已退出登录')
    } catch {
      // 会话可能已过期，本地状态照样清理
    }
    refreshAuth()
  }, [message, refreshAuth])

  if (auth === null) {
    return (
      <div
        style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}
      >
        <Spin size="large" />
      </div>
    )
  }
  if (auth.phase === 'setup_required') return <SetupPage onDone={refreshAuth} />
  if (!auth.authenticated) return <LoginPage onDone={refreshAuth} />

  return (
    <MainLayout onLogout={handleLogout}>
      <ErrorBoundary>
        <Routes>
          <Route path="/" element={<Navigate to="/health" replace />} />
          <Route path="/health" element={<HealthPage />} />
          <Route path="/media" element={<MediaPage />} />
          <Route path="/pipeline" element={<PipelinePage />} />
          <Route path="/policy" element={<PolicyPage />} />
          <Route path="/activities" element={<ActivitiesPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/voice" element={<VoicePage />} />
          <Route path="/devices" element={<DevicesPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/health" replace />} />
        </Routes>
      </ErrorBoundary>
    </MainLayout>
  )
}

function MainLayout({ children, onLogout }: { children: ReactNode; onLogout: () => void }) {
  const location = useLocation()
  const navigate = useNavigate()
  const selected = location.pathname.split('/')[1] || 'health'
  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Sider
        collapsible
        breakpoint="lg"
        collapsedWidth={0}
        width={208}
        theme="light"
        style={{ borderRight: '1px solid #eceef2' }}
      >
        <div className="brand">
          童映 Kindo
          <small>家长管理后台</small>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selected]}
          items={NAV_ITEMS}
          onClick={(e) => navigate(`/${e.key}`)}
        />
        <Button
          block
          type="text"
          icon={<LogoutOutlined />}
          onClick={onLogout}
          style={{ marginTop: 24, textAlign: 'left', paddingLeft: 24 }}
        >
          退出登录
        </Button>
      </Layout.Sider>
      <Layout.Content style={{ padding: '16px 24px 32px' }}>
        <div style={{ maxWidth: 1120, margin: '0 auto' }}>{children}</div>
      </Layout.Content>
    </Layout>
  )
}
