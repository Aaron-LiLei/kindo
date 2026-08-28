/** 根级异常兜底：任何页面渲染异常不再白屏，可一键恢复。 */
import { Button, Result } from 'antd'
import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('页面渲染异常', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <Result
          status="error"
          title="页面出错了"
          subTitle={this.state.error.message}
          extra={
            <Button
              type="primary"
              onClick={() => {
                this.setState({ error: null })
                window.location.reload()
              }}
            >
              重新加载
            </Button>
          }
        />
      )
    }
    return this.props.children
  }
}
