import { useEffect, useRef, useState } from 'react'
import { amapConfigured, createAmap } from './amap'
import type { Coordinates, Destination, Result } from './api'

function bandColor(index: number, count: number, opacity = 1): string {
  const position = index / Math.max(1, count - 1)
  return `hsl(${71 + 128 * position} 63% ${68 - 20 * position}% / ${opacity})`
}
const latitudeScale = Math.PI * 6371008.8 / 180

type Props = {
  center: Destination; radius: number; result: Result | null
  bands: boolean; lines: boolean; samples: boolean
  onSelect: (destination: Destination) => void
}

export default function MapView(props: Props) {
  const [showAmap, setShowAmap] = useState(false)
  const [mapError, setMapError] = useState('')
  const [mapVersion, setMapVersion] = useState(0)
  const mapElement = useRef<HTMLDivElement>(null)
  const adapter = useRef<Awaited<ReturnType<typeof createAmap>> | null>(null)
  const latest = useRef(props)
  latest.current = props
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const controls = useRef<MapControls | null>(null)
  const dragging = useRef(false)
  const skipClick = useRef(false)
  const [dragPoint, setDragPoint] = useState<Destination | null>(null)
  const [sample, setSample] = useState<string | null>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  useEffect(() => {
    const canvas = canvasRef.current!
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height })
    })
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!showAmap || !mapElement.current) return
    let disposed = false
    let frame = 0
    setMapError('')
    createAmap(mapElement.current, latest.current.center, point => latest.current.onSelect(point), () => {
      if (disposed || frame) return
      frame = requestAnimationFrame(() => {
        frame = 0
        setMapVersion(value => value + 1)
      })
    }).then(map => {
      if (disposed) {
        map.destroy()
        return
      }
      adapter.current = map
      map.fit(latest.current.center, latest.current.radius)
      setMapVersion(value => value + 1)
    }).catch(error => {
      if (!disposed) {
        setMapError(error.message)
        setShowAmap(false)
      }
    })
    return () => {
      disposed = true
      cancelAnimationFrame(frame)
      adapter.current?.destroy()
      adapter.current = null
    }
  }, [showAmap])

  useEffect(() => {
    adapter.current?.fit(props.center, props.radius)
  }, [props.center.lng, props.center.lat, props.radius])

  useEffect(() => {
    const canvas = canvasRef.current!
    if (!size.width || !size.height) return
    controls.current = draw(canvas, size, props, showAmap ? adapter.current?.project : undefined, dragPoint)
  }, [size, props.center.lng, props.center.lat, props.radius, props.result, props.bands,
    props.lines, props.samples, mapVersion, showAmap, dragPoint])

  return (
    <section className="map" aria-label="交通可达性坐标视图">
      {showAmap && <div className="amap" ref={mapElement} />}
      <canvas className={showAmap ? 'amap-overlay' : ''} ref={canvasRef} aria-label="点击坐标视图可设置目的地"
        onPointerDown={event => {
          const x = event.nativeEvent.offsetX, y = event.nativeEvent.offsetY
          if (event.button === 0 && Math.hypot(x - size.width / 2, y - size.height / 2) <= 10) {
            dragging.current = true
            event.currentTarget.setPointerCapture(event.pointerId)
          }
        }}
        onPointerMove={event => {
          if (dragging.current) {
            const point = controls.current?.unproject(event.nativeEvent.offsetX, event.nativeEvent.offsetY)
            setDragPoint(point ?? null)
            return
          }
          const nearest = controls.current?.nearest(event.nativeEvent.offsetX, event.nativeEvent.offsetY)
          setSample(nearest ? (nearest.duration_s === null ? '未知耗时' :
            `样本 · ${(nearest.duration_s / 60).toFixed(1)} 分钟`) : null)
        }}
        onPointerLeave={() => setSample(null)}
        onPointerUp={event => {
          if (!dragging.current) return
          dragging.current = false
          skipClick.current = true
          window.setTimeout(() => { skipClick.current = false }, 0)
          const point = controls.current?.unproject(event.nativeEvent.offsetX, event.nativeEvent.offsetY)
          setDragPoint(null)
          if (point) props.onSelect(point)
        }}
        onPointerCancel={() => { dragging.current = false; setDragPoint(null) }}
        onClick={event => {
          if (skipClick.current) return
          const point = controls.current?.unproject(event.nativeEvent.offsetX, event.nativeEvent.offsetY)
          if (point) props.onSelect(point)
        }} />
      <div className="map-title"><span className="signal" />{showAmap ? '高德底图 · GCJ-02' : 'GCJ-02 坐标视图'}</div>
      {amapConfigured && <button className="map-switch" type="button"
        onClick={() => setShowAmap(value => !value)}>{showAmap ? '切换离线视图' : '显示高德底图'}</button>}
      {mapError && <div role="status" className="sample-tooltip">{mapError}</div>}
      <div className="compass" aria-label="北方">N<span>↑</span></div>
      {sample && <div className="sample-tooltip">{sample}</div>}
      <div className="map-caption">单击视图或拖动标记设置目的地</div>
      <div className="legend">
        <strong>到达目的地 · 分钟</strong>
        {props.result?.bands.features.map((feature, index) => (
          <div key={feature.properties.maximum_min}>
            <i style={{ backgroundColor: bandColor(index, props.result!.bands.features.length) }} />
            {feature.properties.minimum_min}–{feature.properties.maximum_min}
          </div>
        ))}
        {!props.result?.bands.features.length && <span>生成后显示耗时区间</span>}
      </div>
    </section>
  )
}

type MapControls = {
  unproject: (x: number, y: number) => Destination
  nearest: (x: number, y: number) => Result['samples'][number] | undefined
}

function draw(
  canvas: HTMLCanvasElement, size: { width: number; height: number }, props: Props,
  mapProject: ((point: Coordinates) => Coordinates) | undefined, dragPoint: Destination | null,
): MapControls {
  const { width, height } = size
  const ratio = window.devicePixelRatio || 1
  canvas.width = Math.round(width * ratio)
  canvas.height = Math.round(height * ratio)
  const ctx = canvas.getContext('2d')!
  ctx.scale(ratio, ratio)
  const cx = width / 2, cy = height / 2
  const result = props.result
  const shownRadius = Math.max(props.radius, result?.radius_m ?? 0)
  const longitudeScale = latitudeScale * Math.cos(props.center.lat * Math.PI / 180)
  const scale = Math.min(width, height) * 0.36 / shownRadius
  const project = mapProject ?? (([lng, lat]: Coordinates): Coordinates => [
    cx + (lng - props.center.lng) * longitudeScale * scale,
    cy - (lat - props.center.lat) * latitudeScale * scale,
  ])
  const marker = dragPoint ?? props.center
  const [markerX, markerY] = project([marker.lng, marker.lat])

  if (!mapProject) {
    ctx.strokeStyle = '#263b36'
    ctx.lineWidth = 1
    ctx.beginPath()
    for (let x = cx % 36; x <= width; x += 36) {
      ctx.moveTo(x, 0); ctx.lineTo(x, height)
    }
    for (let y = cy % 36; y <= height; y += 36) {
      ctx.moveTo(0, y); ctx.lineTo(width, y)
    }
    ctx.stroke()
  }
  const boundary: Coordinates[] = []
  for (let index = 0; index < 64; index++) {
    const angle = index * Math.PI / 32
    boundary.push([
      props.center.lng + props.radius * Math.cos(angle) / longitudeScale,
      props.center.lat + props.radius * Math.sin(angle) / latitudeScale,
    ])
  }
  ctx.beginPath()
  trace(boundary, true)
  ctx.strokeStyle = '#7c9884'
  ctx.setLineDash([3, 6])
  ctx.stroke()
  ctx.setLineDash([])

  function trace(points: Coordinates[], closed: boolean) {
    points.forEach((point, index) => {
      const [x, y] = project(point)
      if (index === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    if (closed) ctx.closePath()
  }

  if (props.bands) result?.bands.features.forEach((feature, index) => {
    ctx.fillStyle = bandColor(index, result.bands.features.length, 0.68)
    for (const polygon of feature.geometry.coordinates) {
      ctx.beginPath()
      for (const ring of polygon) trace(ring, true)
      ctx.fill('evenodd')
    }
  })
  if (props.lines) result?.contours.features.forEach(feature => {
    ctx.beginPath()
    for (const line of feature.geometry.coordinates) trace(line, false)
    ctx.strokeStyle = '#ebffd1'
    ctx.lineWidth = 1.15
    ctx.stroke()
  })
  const locations = props.samples ? result?.samples.map(sample => ({
    sample, position: project(sample.coordinates),
  })) ?? [] : []
  for (const { sample, position } of locations) {
    ctx.beginPath()
    ctx.arc(...position, 2.2, 0, 2 * Math.PI)
    ctx.fillStyle = sample.status === 'ok' ? '#e8f7d1' : '#f6ad82'
    ctx.fill()
  }

  if (!mapProject) {
    ctx.beginPath()
    ctx.arc(markerX, markerY, 6, 0, 2 * Math.PI)
    ctx.fillStyle = '#effc9a'
    ctx.fill()
    ctx.strokeStyle = '#111b18'
    ctx.lineWidth = 2
    ctx.stroke()
  }

  if (!mapProject) {
    ctx.font = '12px monospace'
    ctx.fillStyle = '#a8bcaf'
    const bar = props.radius / 3
    ctx.fillRect(22, height - 44, bar * scale, 2)
    ctx.fillText(`${(bar / 1000).toFixed(1)} km`, 22, height - 50)
  }

  return {
    unproject: (x, y) => ({
      lng: props.center.lng + (x - cx) / (scale * longitudeScale),
      lat: props.center.lat - (y - cy) / (scale * latitudeScale),
    }),
    nearest: (x, y) => locations.find(({ position }) =>
      Math.hypot(x - position[0], y - position[1]) <= 6)?.sample,
  }
}
