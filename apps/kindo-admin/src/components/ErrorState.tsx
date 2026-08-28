/** 页面内加载失败态：保留重试入口（修复出错即锁死整个页面的问题）。 */
import { Button, Result } from 'antd'

export function ErrorState({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <Result
      status="warning"
      title="加载失败"
      subTitle={error}
      extra={onRetry ? <Button onClick={onRetry}>重试</Button> : undefined}
      style={{ padding: '24px 0' }}
    />
  )
}
