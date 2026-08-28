import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MediaCard } from './MediaCard'
import type { MediaItem } from '../../types/admin'

vi.mock('../../api/admin', () => ({ adminApi: { posterUrl: (id: string) => `/p/${id}` } }))

const item = (over: Partial<MediaItem>): MediaItem => ({
  media_id: 'm1',
  title: 'Twinkle Twinkle Little Star',
  media_type: 'episode',
  mount_id: 'family',
  path_key: '儿歌/SSS儿歌/002. Twinkle.mp4',
  duration_ms: 0,
  language: null,
  age_band: null,
  tags: {},
  playable: true,
  missing: false,
  metadata_version: 1,
  parent_edited: false,
  has_poster: false,
  size_bytes: 258 * 1024 * 1024,
  series: null,
  course: null,
  ...over,
})

describe('MediaCard', () => {
  it('显示所属系列行（自动归组后的核心信息）', () => {
    render(
      <MediaCard
        media={item({
          series: { series_id: 's1', title: 'SSS儿歌', season_no: 1, episode_no: 2 },
        })}
        onOpen={vi.fn()}
      />,
    )
    expect(screen.getByText('SSS儿歌')).toBeInTheDocument()
    expect(screen.getByText(/剧集/)).toBeInTheDocument()
  })

  it('时长未知时角标退化为文件体积', () => {
    render(<MediaCard media={item({})} onOpen={vi.fn()} />)
    expect(screen.getByText('258 MB')).toBeInTheDocument()
  })

  it('有时长时优先显示时长角标', () => {
    render(<MediaCard media={item({ duration_ms: 3 * 60 * 1000 })} onOpen={vi.fn()} />)
    expect(screen.getByText('3:00')).toBeInTheDocument()
    expect(screen.queryByText('258 MB')).not.toBeInTheDocument()
  })

  it('点击整卡打开详情', async () => {
    const onOpen = vi.fn()
    render(<MediaCard media={item({})} onOpen={onOpen} />)
    screen.getByRole('button', { name: 'Twinkle Twinkle Little Star' }).click()
    expect(onOpen).toHaveBeenCalledTimes(1)
  })
})
