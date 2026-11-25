import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { bandColor, createAmap } from './amap'
import type { AmapConfig } from './amap'
import type { Destination, Result } from './api'
import Icon from './Icon'

type Props = {
  center: Destination; radius: number; result: Result | null
  bands: boolean; lines: boolean; samples: boolean
  amapConfig: AmapConfig | null
  layerControl: ReactNode
  jobControl: ReactNode
  setupPrompt: ReactNode
  onConfigure: () => void
  onSelect: (destination: Destination) => void
  onMapInteract: () => void
}

export default function MapView(props: Props) {
  const { amapConfig } = props
  const [mapError, setMapError] = useState<{ config: AmapConfig; message: string } | null>(null)
  const errorMessage = mapError && mapError.config === amapConfig ? mapError.message : ''
  const showAmap = Boolean(amapConfig && !errorMessage)
  const [readyConfig, setReadyConfig] = useState<AmapConfig | null>(null)
  const nativeReady = showAmap && readyConfig === amapConfig
  const mapElement = useRef<HTMLDivElement>(null)
  const adapter = useRef<Awaited<ReturnType<typeof createAmap>> | null>(null)
  const latest = useRef(props)
  latest.current = props
  useEffect(() => {
    if (!showAmap || !amapConfig || !mapElement.current) return
    let disposed = false
    const { center, radius } = latest.current
    createAmap(mapElement.current, amapConfig, center, radius, point => latest.current.onSelect(point)).then(map => {
      if (disposed) {
        map.destroy()
        return
      }
      adapter.current = map
      const current = latest.current
      if (current.center.lng !== center.lng || current.center.lat !== center.lat || current.radius !== radius) {
        map.fit(current.center, current.radius)
      }
      setReadyConfig(amapConfig)
    }).catch(error => {
      if (!disposed) {
        setMapError({ config: amapConfig, message: error.message })
      }
    })
    return () => {
      disposed = true
      adapter.current?.destroy()
      adapter.current = null
    }
  }, [showAmap, amapConfig])

  useEffect(() => {
    const map = adapter.current
    if (!map) return
    const timer = window.setTimeout(() => {
      if (adapter.current === map) map.fit(latest.current.center, latest.current.radius)
    }, 150)
    return () => window.clearTimeout(timer)
  }, [props.center.lng, props.center.lat, props.radius])

  const { result, bands, lines, samples } = props
  useEffect(() => {
    if (nativeReady) adapter.current?.setResult(result, { bands, lines, samples })
  }, [nativeReady, result, bands, lines, samples])

  return (
    <section className={`map${showAmap ? ' is-amap' : ''}`} aria-label="交通可达性地图">
      {showAmap && <div className="amap" ref={mapElement} onPointerDownCapture={event => {
        if (event.button === 0) props.onMapInteract()
      }} />}
      {errorMessage && <div className="map-setup" role="status">
        <strong>高德底图加载失败</strong>
        <button type="button" className="text-button" onClick={props.onConfigure}>检查高德设置</button>
      </div>}
      {!errorMessage && props.setupPrompt}
      <div className="map-bottom">
        {nativeReady && result && <TravelLegend result={result} />}
        {props.jobControl}
        {nativeReady && <div className="map-tools" aria-label="地图工具">
          <div className="map-controls">
            <div className="compass" aria-label="北方"><b>N</b><span>▲</span></div>
            <div className="zoom-controls">
              <button type="button" title="放大" aria-label="放大" onClick={() => adapter.current?.zoomBy(1)}><Icon name="plus" /></button>
              <button type="button" title="缩小" aria-label="缩小" onClick={() => adapter.current?.zoomBy(-1)}><Icon name="minus" /></button>
              <button type="button" title="回到目的地" aria-label="回到目的地" onClick={() => adapter.current?.fit(props.center, props.radius)}><Icon name="center" /></button>
            </div>
            {props.layerControl}
          </div>
        </div>}
      </div>
    </section>
  )
}

const beijingTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', second: '2-digit',
})

function TravelLegend({ result }: { result: Result }) {
  const features = result.bands.features
  const count = features.length
  const from = result.observed_from ? beijingTime.format(new Date(result.observed_from)) : null
  const to = result.observed_to ? beijingTime.format(new Date(result.observed_to)) : null
  return <aside className="legend" aria-label="等时圈图例">
    {from && <p className="legend-time">算路时间 {from}{to && from !== to ? `–${to}` : ''}（北京时间）</p>}
    {count > 0 && <div className="legend-scale" style={{ gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))` }}>
      {features.map(({ properties }, index) => <div className="legend-segment" key={properties.maximum_min}>
        <span style={{ backgroundColor: bandColor(index, count) }}
          title={`${properties.minimum_min}–${properties.maximum_min} 分钟`} />
        <span className={count > 6 && (count - 1 - index) % Math.ceil(count / 6) ? 'legend-tick visually-hidden' : 'legend-tick'}>
          {properties.maximum_min}<small> min</small>
        </span>
      </div>)}
    </div>}
  </aside>
}
