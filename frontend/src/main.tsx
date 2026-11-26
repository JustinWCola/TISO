import { useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { amapNeedsReload, legacyAmapConfig, searchAmap } from './amap'
import type { AmapConfig } from './amap'
import AmapSettings from './AmapSettings'
import type { SavedAmapSettings } from './AmapSettings'
import Icon from './Icon'
import MapView from './MapView'
import { destinationFromInputs, jobs, request, terminal } from './api'
import type { Destination, Estimate, Job, Parameters, Result, Sample } from './api'
import './style.css'

const shanghai = { lng: 121.4737, lat: 31.2304 }
const minimumSpacings = [250, 500, 1000] as const
const contourIntervals = [5, 10, 15, 20, 30] as const
const stages: Record<string, string> = {
  queued: '排队中', sampling: '采样中', routing: '算路中', expanding: '向外采样中', geometry: '计算等时圈',
  preview: '计算初步等时圈', refining: '六边形网格加密中',
  completed: '计算完成', failed: '计算失败', cancelled: '已取消',
}

function AnalysisSlider({ label, value, min, max, step = 1, display, onChange }: {
  label: string; value: number; min: number; max: number; step?: number; display: string
  onChange: (value: number) => void
}) {
  return <label className="analysis-slider">
    <span>{label}</span>
    <input type="range" aria-label={label} aria-valuetext={display} min={min} max={max} step={step}
      value={value} onChange={event => onChange(event.currentTarget.valueAsNumber)} />
    <span className="slider-value" aria-hidden="true">{display}</span>
  </label>
}

function App() {
  const [lng, setLng] = useState(String(shanghai.lng))
  const [lat, setLat] = useState(String(shanghai.lat))
  const [query, setQuery] = useState('')
  const [places, setPlaces] = useState<Awaited<ReturnType<typeof searchAmap>>>([])
  const [searchStatus, setSearchStatus] = useState('')
  const [searching, setSearching] = useState(false)
  const [spacingIndex, setSpacingIndex] = useState(0)
  const [duration, setDuration] = useState(60)
  const [intervalIndex, setIntervalIndex] = useState(2)
  const [savedAmap, setSavedAmap] = useState<SavedAmapSettings | null>(null)
  const [panel, setPanel] = useState<'analysis' | 'amap' | null>(null)
  const [layersOpen, setLayersOpen] = useState(false)
  const [job, setJob] = useState<Job | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [connection, setConnection] = useState('')
  const [estimate, setEstimate] = useState<{ key: string; value: Estimate } | null>(null)
  const [estimateError, setEstimateError] = useState('')
  const [result, setResult] = useState<Result | null>(null)
  const [preview, setPreview] = useState<Result | null>(null)
  const [bands, setBands] = useState(true)
  const [lines, setLines] = useState(true)
  const [samples, setSamples] = useState(false)
  const [liveSamples, setLiveSamples] = useState<{ jobId: string; points: Sample[] } | null>(null)
  const stream = useRef<EventSource | null>(null)

  const jsKey = savedAmap?.js_key
  const amapConfig = useMemo<AmapConfig | null>(() => jsKey ? {
    key: jsKey, serviceHost: window.location.origin + '/_AMapService',
  } : legacyAmapConfig, [jsKey])
  const reloadMap = amapNeedsReload(amapConfig)
  const canCalculate = Boolean(amapConfig && !reloadMap && savedAmap?.routing_configured)
  const spacing = minimumSpacings[spacingIndex]
  const interval = contourIntervals[intervalIndex]

  const destination = useMemo(() => destinationFromInputs(lng, lat), [lng, lat])
  const params = useMemo<Parameters | null>(() => destination && ({
    destination, min_spacing_m: spacing,
    max_duration_min: duration, contour_interval_min: interval,
  }), [destination, spacing, duration, interval])
  const key = JSON.stringify(params)
  const currentEstimate = estimate?.key === key ? estimate.value : null
  const busy = starting || (job !== null && !terminal.has(job.status))
  const liveJobId = job && !terminal.has(job.status) ? job.job_id : null
  const completedPoints = useRef(0)
  completedPoints.current = job?.completed_points ?? 0

  useEffect(() => {
    if (!samples || !liveJobId) return
    const abort = new AbortController()
    let cursor = 0
    let timer = 0
    let points: Sample[] = []
    async function refresh() {
      try {
        while (!abort.signal.aborted && cursor < completedPoints.current) {
          const page = await request<{ samples: Sample[]; next: number }>(
            `${jobs}/${liveJobId}/samples?after=${cursor}`, undefined, abort.signal,
          )
          if (abort.signal.aborted || page.next <= cursor) break
          points = [...points, ...page.samples]
          cursor = page.next
          setLiveSamples({ jobId: liveJobId!, points })
        }
      } catch {
        // Retry on the next refresh; routing progress is independent of the overlay.
      } finally {
        if (!abort.signal.aborted) timer = window.setTimeout(() => void refresh(), 300)
      }
    }
    void refresh()
    return () => {
      abort.abort()
      window.clearTimeout(timer)
    }
  }, [liveJobId, samples])

  useEffect(() => {
    const abort = new AbortController()
    request<SavedAmapSettings>('/settings/amap', undefined, abort.signal)
      .then(setSavedAmap)
      .catch(() => { if (!abort.signal.aborted) setConnection('无法读取服务配置') })
    return () => {
      abort.abort()
      stream.current?.close()
      stream.current = null
    }
  }, [])

  useEffect(() => {
    if (!panel && !layersOpen) return
    function dismiss(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      if (layersOpen) {
        setLayersOpen(false)
        document.getElementById('layers-toggle')?.focus()
      } else {
        setPanel(null)
        document.getElementById(`${panel}-toggle`)?.focus()
      }
    }
    window.addEventListener('keydown', dismiss)
    return () => window.removeEventListener('keydown', dismiss)
  }, [panel, layersOpen])

  useEffect(() => {
    setEstimateError('')
    if (!params || !canCalculate) return
    const abort = new AbortController()
    const timer = window.setTimeout(() => {
      request<Estimate>('/isochrones/estimate', params, abort.signal)
        .then(value => setEstimate({ key, value }))
        .catch(cause => { if (!abort.signal.aborted) setEstimateError(cause.message) })
    }, 180)
    return () => {
      window.clearTimeout(timer)
      abort.abort()
    }
  }, [params, key, canCalculate])

  function choose(point: Destination) {
    if (destinationFromInputs(String(point.lng), String(point.lat))) {
      setLng(point.lng.toFixed(6))
      setLat(point.lat.toFixed(6))
    }
  }

  async function findPlace() {
    if (!amapConfig || searching || !query.trim()) return
    setSearching(true)
    setPlaces([])
    setSearchStatus('')
    try {
      const matches = await searchAmap(amapConfig, query.trim())
      setPlaces(matches)
      if (!matches.length) setSearchStatus('未找到相关地点。')
    } catch (cause) {
      setSearchStatus(cause instanceof Error ? cause.message : '地点搜索失败。')
    } finally {
      setSearching(false)
    }
  }

  function saveAmap(settings: SavedAmapSettings) {
    if (settings.js_key !== jsKey) {
      setPlaces([])
      setSearchStatus('')
    }
    setSavedAmap(settings)
  }

  function togglePanel(next: typeof panel) {
    setPanel(current => current === next ? null : next)
  }

  function dismissMapPanels() {
    setPanel(null)
    setLayersOpen(false)
  }

  async function generate() {
    if (!params || !canCalculate || busy || !currentEstimate?.fits_budget) return
    dismissMapPanels()
    setStarting(true)
    setError('')
    setConnection('')
    setPreview(null)
    setLiveSamples(null)
    try {
      const created = await request<Job>(jobs, params)
      setJob(created)
      const path = jobs + '/' + created.job_id
      const source = new EventSource('/api/v1' + path + '/events')
      stream.current?.close()
      stream.current = source
      let previewRequest: AbortController | null = null
      source.onopen = () => setConnection('')
      source.onerror = () => setConnection('进度连接中断，正在重连…')
      async function update(event: MessageEvent<string>) {
        const next: Job = JSON.parse(event.data)
        setJob(next)
        if (!terminal.has(next.status)) {
          if (next.preview_ready && !previewRequest) {
            const abort = new AbortController()
            previewRequest = abort
            request<Result>(path + '/preview', undefined, abort.signal)
              .then(value => { if (!abort.signal.aborted && stream.current === source) setPreview(value) })
              .catch(() => { if (!abort.signal.aborted) previewRequest = null })
          }
          return
        }
        previewRequest?.abort()
        source.close()
        setConnection('')
        if (next.error) setError(next.error.message)
        if (next.status === 'partial' || next.status === 'completed') {
          setStarting(true)
          try {
            setResult(await request<Result>(path + '/result'))
          } catch (cause) {
            setError(cause instanceof Error ? cause.message : '无法读取结果')
          } finally {
            setPreview(null)
            setStarting(false)
          }
        } else {
          setPreview(null)
        }
      }
      for (const event of ['snapshot', 'progress', 'completed', 'failed', 'cancelled']) {
        source.addEventListener(event, message => {
          if (message instanceof MessageEvent) void update(message)
        })
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '无法创建任务')
    } finally {
      setStarting(false)
    }
  }

  async function cancel() {
    if (!job || terminal.has(job.status)) return
    try {
      await request<Job>(jobs + '/' + job.job_id + '/cancel', {})
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '取消失败')
    }
  }

  const analysisOpen = panel === 'analysis'
  const settingsOpen = panel === 'amap'
  const center = destination ?? result?.destination ?? shanghai
  const setupMessage = !amapConfig
    ? `请填写 Web 端 JS Key 和安全密钥${savedAmap?.routing_configured ? '。' : '，以及用于算路的 Web 服务 Key。'}`
    : '请填写 Web 服务 Key，以计算驾车等时圈。'
  const canCancel = job && !terminal.has(job.status)
  const jobStatus = <div role="status" className="job-status">
    {job ? <span>{job.status === 'partial' ? '计算完成 · 部分结果' : stages[job.stage]} · {job.completed_points}{job.stage === 'expanding' ? '' : `/${job.total_points}`} 点{job.requests_sent > 0 ? ` · ${job.requests_sent} 次请求` : ''}</span>
      : starting && <span>正在提交任务…</span>}
    {connection && <span>{connection}</span>}
    {error && <span className="error">{error}</span>}
  </div>
  const cancelButton = canCancel && <button type="button" className="secondary" onClick={() => void cancel()}>取消</button>
  const coordinateFields = <div className="coordinates-entry">
    <div className="pair">
      <label>经度<input inputMode="decimal" value={lng} onChange={e => setLng(e.target.value)} /></label>
      <label>纬度<input inputMode="decimal" value={lat} onChange={e => setLat(e.target.value)} /></label>
    </div>
    {!destination && <p className="hint error">请输入中国大陆范围内的经纬度。</p>}
    <button type="button" className="text-button" onClick={() => choose(shanghai)}>使用上海示例</button>
  </div>
  const layerControl = <div className="layer-dock">
    <button type="button" id="layers-toggle" className="floating-button"
      aria-expanded={layersOpen} aria-controls="map-layers"
      onClick={() => setLayersOpen(value => !value)}><Icon name="layers" />图层</button>
    {layersOpen && <div className="layers floating-panel" id="map-layers" aria-label="结果图层">
      <label><input type="checkbox" checked={bands} onChange={e => setBands(e.target.checked)} />等时面</label>
      <label><input type="checkbox" checked={lines} onChange={e => setLines(e.target.checked)} />等时线</label>
      <label><input type="checkbox" checked={samples} onChange={e => setSamples(e.target.checked)} />采样点</label>
    </div>}
  </div>

  return (
    <main className="app">
      <MapView center={center} radius={duration <= 30 ? 7500 : duration <= 60 ? 15000 : 30000} result={preview ?? (busy ? null : result)}
        liveSamples={liveJobId ? (liveSamples?.jobId === liveJobId ? liveSamples.points : []) : undefined}
        bands={bands} lines={lines} samples={samples} onSelect={choose} onMapInteract={dismissMapPanels}
        amapConfig={reloadMap ? null : amapConfig} layerControl={layerControl} onConfigure={() => setPanel('amap')}
        jobControl={!analysisOpen && (busy || connection || error || job?.status === 'cancelled') &&
          <div className="map-job">{jobStatus}{cancelButton}</div>}
        setupPrompt={savedAmap && !canCalculate && !settingsOpen && <div className="map-setup">
          {reloadMap ? <>
            <strong>高德配置已更新</strong><p>刷新页面后会载入新的底图配置。</p>
            <button type="button" className="primary" onClick={() => window.location.reload()}>刷新页面</button>
          </> : <>
            <strong>配置高德 API</strong><p>{setupMessage}</p>
            <button type="button" className="primary" onClick={() => setPanel('amap')}>打开高德设置</button>
          </>}
        </div>} />

      <div className={`dock analysis-dock${analysisOpen ? ' expanded' : ''}`}>
        <button type="button" id="analysis-toggle" className="dock-toggle brand" aria-expanded={analysisOpen}
          aria-controls="analysis-controls" onClick={() => togglePanel('analysis')}>
          <span className="brand-icon"><Icon name="pin" /></span>
          <span className="brand-name"><strong>TISO</strong><span>交通可达性</span></span>
          {analysisOpen && <span className="dock-collapse">收起</span>}
          <Icon name="chevron" />
        </button>
        <section className="dock-body" id="analysis-controls" aria-labelledby="analysis-title" inert={!analysisOpen}>
          <div className="dock-content">
            <div className="analysis-card">
              <div className="analysis-heading">
                <div className="mode-icon"><Icon name="car" /></div>
                <div className="analysis-title">
                  <h1 id="analysis-title">驾车等时圈</h1>
                </div>
              </div>

              {amapConfig && !reloadMap ? <>
                <form className="search" onSubmit={event => { event.preventDefault(); void findPlace() }}>
                  <Icon name="search" />
                  <input aria-label="地点关键词" placeholder="搜索目的地" value={query} onChange={event => setQuery(event.target.value)} />
                  <button type="submit" className="text-button" disabled={searching || !query.trim()}>{searching ? '搜索中…' : '搜索'}</button>
                  {!!places.length && <ul className="search-results">{places.map(({ name, point }, index) =>
                    <li key={index}><button type="button" onClick={() => {
                      choose(point); setQuery(name); setPlaces([])
                    }}>{name}</button></li>)}</ul>}
                </form>
                {searchStatus && <p className="hint" role="status">{searchStatus}</p>}
                <details className="coordinate-details"><summary>目的地坐标</summary>{coordinateFields}</details>
              </> : coordinateFields}
              {reloadMap && <p className="hint" role="status">刷新页面后应用新的高德底图配置。</p>}

              <div className="analysis-sliders">
                <AnalysisSlider label="最长耗时" value={duration} min={15} max={120} step={5}
                  display={`${duration} 分钟`} onChange={setDuration} />
                <AnalysisSlider label="等时线间隔" value={intervalIndex} min={0} max={contourIntervals.length - 1}
                  display={`${interval} 分钟`} onChange={setIntervalIndex} />
                <AnalysisSlider label="最小间距" value={spacingIndex} min={0} max={minimumSpacings.length - 1}
                  display={`${spacing} 米`} onChange={setSpacingIndex} />
              </div>
              <div className="analysis-estimate" aria-live="polite">
                {currentEstimate
                  ? null
                  : <span>{estimateError || (!canCalculate ? '配置高德后可生成等时圈。' : destination ? '等待预算估算…' : '请选择目的地')}</span>}
                {currentEstimate && !currentEstimate.fits_budget &&
                  <span className="error">计算预算不足，无法开始采样。</span>}
              </div>
              <div className="actions">
                <button type="button" className="primary generate" disabled={!canCalculate || busy || !currentEstimate?.fits_budget}
                  onClick={() => void generate()}>{busy ? '正在计算…' : '生成等时圈'}<Icon name="arrow" /></button>
                {analysisOpen && cancelButton}
              </div>
              {analysisOpen && (job || connection || error) && jobStatus}
            </div>
          </div>
        </section>
      </div>

      <div className={`dock settings-dock${settingsOpen ? ' expanded' : ''}`}>
        <button type="button" id="amap-toggle" className="dock-toggle amap-toggle" disabled={!savedAmap}
          aria-expanded={settingsOpen} aria-controls="amap-settings" onClick={() => togglePanel('amap')}>
          <span className="brand-icon"><Icon name="key" /></span><span>高德设置</span>
          {settingsOpen && <span className="dock-collapse">收起</span>}
          <Icon name="chevron" />
        </button>
        <div className="dock-body" id="amap-settings" inert={!settingsOpen}>
          <div className="dock-content">
            {savedAmap && <AmapSettings key={String(settingsOpen)} settings={savedAmap} busy={busy} onSave={saveAmap} />}
          </div>
        </div>
      </div>
    </main>
  )
}

createRoot(document.getElementById('root')!).render(<App />)
