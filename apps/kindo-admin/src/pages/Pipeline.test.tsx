import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PipelinePage } from './Pipeline'

vi.mock('./media/ScrapeCard', () => ({
  ScrapeCard: () => <div data-testid="scrape-card">ScrapeCard</div>,
}))
vi.mock('./media/MatchManager', () => ({
  MatchManager: () => <div data-testid="match-manager">MatchManager</div>,
}))

describe('PipelinePage（刮削与匹配独立入口）', () => {
  it('同时承载海报刮削与身份匹配两个组件', () => {
    render(<PipelinePage />)
    expect(screen.getByTestId('scrape-card')).toBeInTheDocument()
    expect(screen.getByTestId('match-manager')).toBeInTheDocument()
  })
})
