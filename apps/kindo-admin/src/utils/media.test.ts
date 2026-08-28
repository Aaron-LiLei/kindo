import { describe, expect, it } from 'vitest'
import {
  collectionSiblings,
  scanJobMeta,
  compactDuration,
  filterMedia,
  groupByCollection,
  mediaSeed,
  mediaTypeLabel,
  mountLocation,
  placeholderGradient,
  sequenceLabel,
  titleInitials,
} from './media'
import type { MediaItem } from '../types/admin'

const item = (over: Partial<MediaItem>): MediaItem => ({
  media_id: 'm1',
  title: '小猪佩奇 第一集',
  media_type: 'animation',
  mount_id: 'main',
  path_key: 'a/b.mp4',
  duration_ms: 300000,
  language: 'zh-CN',
  age_band: null,
  tags: { characters: ['佩奇'], themes: ['家庭'] },
  playable: true,
  missing: false,
  metadata_version: 1,
  parent_edited: false,
  has_poster: false,
  size_bytes: 1024 * 1024,
  series: null,
  course: null,
  ...over,
})

describe('filterMedia', () => {
  const items = [
    item({}),
    item({
      media_id: 'm2',
      title: '海底小纵队',
      tags: { characters: ['巴克队长'], themes: ['海洋'] },
    }),
  ]

  it('按标题匹配', () => {
    expect(filterMedia(items, '佩奇')).toHaveLength(1)
  })
  it('按角色匹配', () => {
    expect(filterMedia(items, '巴克')).toHaveLength(1)
    expect(filterMedia(items, '巴克')[0].media_id).toBe('m2')
  })
  it('按主题匹配', () => {
    expect(filterMedia(items, '海洋')).toHaveLength(1)
  })
  it('空查询返回全部', () => {
    expect(filterMedia(items, '')).toHaveLength(2)
    expect(filterMedia(items, '   ')).toHaveLength(2)
  })
  it('无命中返回空', () => {
    expect(filterMedia(items, '不存在')).toHaveLength(0)
  })
})

describe('mountLocation', () => {
  it('local 显示 root/sub', () => {
    expect(
      mountLocation({ mount_type: 'local', root_id: 'main', sub_path: '动画库', config: {} }),
    ).toBe('main/动画库')
  })
  it('smb 显示 host/share（含子路径）', () => {
    expect(
      mountLocation({
        mount_type: 'smb',
        root_id: '',
        sub_path: '',
        config: { host: '192.168.1.20', share: 'media', path: 'kids' },
      }),
    ).toBe('192.168.1.20/media/kids')
    expect(
      mountLocation({
        mount_type: 'smb',
        root_id: '',
        sub_path: '',
        config: { host: 'nas', share: 'm' },
      }),
    ).toBe('nas/m')
  })
  it('webdav 显示 url（含子路径）', () => {
    expect(
      mountLocation({
        mount_type: 'webdav',
        root_id: '',
        sub_path: '',
        config: { url: 'http://nas:5005/dav', path: 'x' },
      }),
    ).toBe('http://nas:5005/dav/x')
  })
})

describe('媒体展示工具（海报墙 / 合集）', () => {
  const seriesItem = (no: number, over: Partial<MediaItem> = {}) =>
    item({
      media_id: `ep${no}`,
      title: `汪汪队 第${no}集`,
      media_type: 'episode',
      series: { series_id: 's1', title: '汪汪队', season_no: 1, episode_no: no },
      ...over,
    })

  it('mediaTypeLabel 映射已知类型', () => {
    expect(mediaTypeLabel('episode')).toBe('剧集')
    expect(mediaTypeLabel('movie')).toBe('电影')
    expect(mediaTypeLabel('lesson')).toBe('课程')
    expect(mediaTypeLabel('other')).toBe('other')
  })

  it('sequenceLabel：剧集集号 / 课程章节 / 电影为空', () => {
    expect(sequenceLabel(seriesItem(2))).toBe('S1·E2')
    expect(
      sequenceLabel(
        item({
          media_type: 'lesson',
          course: { course_id: 'c1', title: '英语启蒙', chapter_no: 1, lesson_no: 3 },
        }),
      ),
    ).toBe('第1章·第3课')
    expect(sequenceLabel(item({ media_type: 'movie' }))).toBeNull()
  })

  it('titleInitials 取前两个字（忽略空白）', () => {
    expect(titleInitials('汪汪队 立大功')).toBe('汪汪')
    expect(titleInitials('A')).toBe('A')
  })

  it('placeholderGradient 同种子稳定、不同种子可能不同', () => {
    expect(placeholderGradient('汪汪队')).toBe(placeholderGradient('汪汪队'))
    expect(placeholderGradient('汪汪队')).toMatch(/^linear-gradient\(/)
    const seeds = new Set(Array.from({ length: 20 }, (_, i) => placeholderGradient(`seed-${i}`)))
    expect(seeds.size).toBeGreaterThan(1)
  })

  it('mediaSeed 系列优先于标题', () => {
    expect(mediaSeed(seriesItem(1))).toBe('汪汪队')
    expect(mediaSeed(item({}))).toBe('小猪佩奇 第一集')
  })

  it('compactDuration 紧凑时长', () => {
    expect(compactDuration(0)).toBeNull()
    expect(compactDuration(59_000)).toBe('0:59')
    expect(compactDuration(754_000)).toBe('12:34')
    expect(compactDuration(3_900_000)).toBe('1:05:00')
  })

  it('groupByCollection 按系列/课程分组并跳过单集电影', () => {
    const groups = groupByCollection([
      seriesItem(2),
      seriesItem(1),
      item({ media_id: 'mv', title: '电影', media_type: 'movie' }),
      item({
        media_id: 'ls1',
        media_type: 'lesson',
        course: { course_id: 'c1', title: '英语启蒙', chapter_no: 1, lesson_no: 1 },
      }),
    ])
    expect(groups.map((g) => g.title)).toEqual(['汪汪队', '英语启蒙'])
    expect(groups[0].items.map((m) => m.series?.episode_no)).toEqual([2, 1])
  })

  it('collectionSiblings 同系列按集号排序、不含自身；电影返回空', () => {
    const siblings = collectionSiblings(
      [seriesItem(1), seriesItem(3), seriesItem(2)],
      seriesItem(1),
    )
    expect(siblings.map((s) => s.series?.episode_no)).toEqual([2, 3])
    expect(collectionSiblings([item({})], item({}))).toEqual([])
  })
})

describe('scanJobMeta 扫描状态机', () => {
  it('五种状态全覆盖，interrupted 显示已中断', () => {
    expect(scanJobMeta('done').text).toBe('完成')
    expect(scanJobMeta('failed').text).toBe('失败')
    expect(scanJobMeta('running').text).toBe('进行中')
    expect(scanJobMeta('queued').text).toBe('排队中')
    expect(scanJobMeta('interrupted')).toMatchObject({ text: '已中断', color: 'warning' })
  })
  it('未知状态回落原值', () => {
    expect(scanJobMeta('weird').text).toBe('weird')
  })
})
