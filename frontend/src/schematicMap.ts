import { bandColor, paintTravelLayers } from './amap'
import type { Coordinates, Result } from './api'

const dateTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short',
})

export function renderMap(result: Result): HTMLCanvasElement {
  const width = 1440
  const height = 1000
  const left = 56
  const top = 155
  const mapWidth = width - left * 2
  const mapHeight = 686
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('无法绘制等时圈示意图')

  const { lng, lat } = result.destination
  const lonScale = Math.cos(lat * Math.PI / 180)
  let east = 0, west = 0, north = 0, south = 0
  function measure(path: Coordinates[]) {
    for (const [x, y] of path) {
      const dx = (x - lng) * lonScale, dy = y - lat
      east = Math.max(east, dx)
      west = Math.min(west, dx)
      north = Math.max(north, dy)
      south = Math.min(south, dy)
    }
  }
  for (const feature of result.bands.features) for (const polygon of feature.geometry.coordinates) polygon.forEach(measure)
  for (const feature of result.contours.features) feature.geometry.coordinates.forEach(measure)
  const zoom = Math.min((mapWidth - 140) / Math.max(east - west, 0.002),
    (mapHeight - 140) / Math.max(north - south, 0.002))
  function project([x, y]: Coordinates): Coordinates {
    return [
      width / 2 + ((x - lng) * lonScale - (east + west) / 2) * zoom,
      top + mapHeight / 2 - (y - lat - (north + south) / 2) * zoom,
    ]
  }

  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, width, height)
  ctx.fillStyle = '#141922'
  ctx.font = '700 46px sans-serif'
  ctx.fillText(result.mode === 'metro' ? '地铁等时圈' : '驾车等时圈', left, 77)
  ctx.font = '24px sans-serif'
  ctx.fillStyle = '#48546a'
  const takenAt = result.observed_from ?? result.finished_at
  const timestamp = takenAt ? `  ·  ${dateTime.format(new Date(takenAt))} 北京时间` : ''
  ctx.fillText(`TISO  ·  ${lng.toFixed(5)}, ${lat.toFixed(5)}  ·  GCJ-02${timestamp}`, left, 120)

  ctx.fillStyle = '#f3f5f8'
  ctx.fillRect(left, top, mapWidth, mapHeight)
  ctx.save()
  ctx.beginPath()
  ctx.rect(left, top, mapWidth, mapHeight)
  ctx.clip()
  ctx.strokeStyle = '#dce2eb'
  ctx.lineWidth = 1
  ctx.beginPath()
  for (let x = left + 34; x < width - left; x += 68) {
    ctx.moveTo(x, top); ctx.lineTo(x, top + mapHeight)
  }
  for (let y = top + 34; y < top + mapHeight; y += 68) {
    ctx.moveTo(left, y); ctx.lineTo(width - left, y)
  }
  ctx.stroke()
  paintTravelLayers(ctx, project, result, { bands: true, lines: true, samples: false })
  const [x, y] = project([lng, lat])
  ctx.beginPath()
  ctx.arc(x, y, 10, 0, Math.PI * 2)
  ctx.fillStyle = '#17191d'
  ctx.fill()
  ctx.strokeStyle = '#ffffff'
  ctx.lineWidth = 3
  ctx.stroke()
  ctx.restore()
  ctx.fillStyle = '#172338'
  ctx.font = '700 24px sans-serif'
  ctx.fillText('▲ N', width - 145, top + 46)
  const bands = result.bands.features
  const levels = bands.length ? bands : result.contours.features
  const swatchWidth = Math.min(160, mapWidth / Math.max(1, levels.length))
  ctx.textAlign = 'center'
  ctx.font = '21px sans-serif'
  levels.forEach(({ properties }, index) => {
    const sx = left + index * swatchWidth
    ctx.fillStyle = bands.length ? bandColor(index, levels.length) : '#324b51'
    ctx.fillRect(sx, 870, swatchWidth - 3, 13)
    ctx.fillStyle = '#172338'
    if (levels.length <= 12 || index % 2 === 0) {
      ctx.fillText(`${properties.maximum_min ?? properties.duration_min}`, sx + swatchWidth / 2, 911)
    }
  })
  ctx.textAlign = 'left'
  ctx.font = '22px sans-serif'
  ctx.fillText(`单位：分钟   ·   等时圈示意图${result.boundary_truncated ? '   ·   外缘未确认' : ''}`, left, 960)
  return canvas
}
