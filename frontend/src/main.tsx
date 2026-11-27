import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { amapNeedsReload, identifyAmapPoint, legacyAmapConfig, searchAmap } from './amap'
import type { AmapConfig } from './amap'
import AmapSettings from './AmapSettings'
import type { SavedAmapSettings } from './AmapSettings'
import Icon from './Icon'
import MapView from './MapView'
import QueryPanel from './QueryPanel'
import { destinationFromInputs, jobs, request, schedules, terminal } from './api'
import type { Destination, Estimate, Job, MetroNetwork, Parameters, Result, Sample, SavedResult, Schedule } from './api'
import tisoIcon from './tiso-icon.svg'
import './style.css'

const shanghai = { lng: 121.4737, lat: 31.2304 }
const minimumSpacings = [250, 500, 1000] as const
const contourIntervals = [5, 10, 15, 20, 30] as const
const stages: Record<string, string> = {
  queued: '排队中', sampling: '采样中', routing: '算路中', expanding: '向外采样中', geometry: '计算等时圈',
  metro_routing: '计算本地地铁路网',
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

type Notice = { messages: string[]; error?: boolean; stale?: boolean }
type TravelMode = NonNullable<Parameters['mode']>
type CachedResult = { value: Result; key: string }

function ProgressPopup({ label, action }: {
  label: string; action?: ReactNode
}) {
  return <div className="progress-popup map-job">
    <div className="progress-background" role="progressbar" aria-label={label} />
    <span className="job-status">{label}</span>
    {action}
  </div>
}

function App() {
  const [destination, setDestination] = useState<Destination>(shanghai)
  const [query, setQuery] = useState('')
  const [destinationName, setDestinationName] = useState('')
  const [mapPointLabel, setMapPointLabel] = useState('')
  const pointLookup = useRef<Promise<string> | null>(null)
  const [places, setPlaces] = useState<Awaited<ReturnType<typeof searchAmap>>>([])
  const [searching, setSearching] = useState(false)
  const [spacingIndex, setSpacingIndex] = useState(0)
  const [mode, setMode] = useState<TravelMode>('driving')
  const [walkSpeed, setWalkSpeed] = useState(1.2)
  const [preparingNetwork, setPreparingNetwork] = useState(false)
  const [duration, setDuration] = useState(60)
  const [intervalIndex, setIntervalIndex] = useState(2)
  const [savedAmap, setSavedAmap] = useState<SavedAmapSettings | null>(null)
  const [panel, setPanel] = useState<'analysis' | 'amap' | null>(null)
  const [queryPanel, setQueryPanel] = useState(false)
  const [scheduleTime, setScheduleTime] = useState<string | null>(null)
  const [savingSchedule, setSavingSchedule] = useState(false)
  const [layersOpen, setLayersOpen] = useState(false)
  const [job, setJob] = useState<Job | null>(null)
  const [starting, setStarting] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [estimate, setEstimate] = useState<{ key: string; value: Estimate } | null>(null)
  const [results, setResults] = useState<Partial<Record<TravelMode, CachedResult>>>({})
  const [jobParams, setJobParams] = useState<Parameters | null>(null)
  const [preview, setPreview] = useState<Result | null>(null)
  const [bands, setBands] = useState(true)
  const [lines, setLines] = useState(true)
  const [samples, setSamples] = useState(false)
  const [liveSamples, setLiveSamples] = useState<{ jobId: string; points: Sample[] } | null>(null)
  const stream = useRef<EventSource | null>(null)
  const submission = useRef<AbortController | null>(null)
  const scheduleToggle = useRef<HTMLButtonElement>(null)

  const jsKey = savedAmap?.js_key
  const amapConfig = useMemo<AmapConfig | null>(() => jsKey ? {
    key: jsKey, serviceHost: window.location.origin + '/_AMapService',
  } : legacyAmapConfig, [jsKey])
  const reloadMap = amapNeedsReload(amapConfig)
  const metro = mode === 'metro'
  const scheduleEnabled = !metro && scheduleTime !== null
  const canCalculate = Boolean(amapConfig && !reloadMap && (metro || savedAmap?.routing_configured))
  const spacing = minimumSpacings[spacingIndex]
  const interval = contourIntervals[intervalIndex]
  const matchesDestination = (point: Destination | undefined) =>
    point !== undefined && destination.lng === point.lng && destination.lat === point.lat
  const params = useMemo<Parameters>(() => {
    const options = {
      destination,
      max_duration_min: duration, contour_interval_min: interval,
    }
    return metro ? {
      ...options, mode: 'metro', traffic_mode: 'static', walk_speed_m_s: walkSpeed,
    } : { ...options, min_spacing_m: spacing }
  }, [destination, spacing, duration, interval, metro, walkSpeed])
  const key = JSON.stringify(params)
  const currentEstimate = estimate?.key === key ? estimate.value : null
  const networkStatus = metro ? currentEstimate?.metro_network?.status : undefined
  const needsNetwork = networkStatus !== undefined && networkStatus !== 'ready'
  const busy = starting || (job !== null && !terminal.has(job.status))
  const canGenerate = canCalculate && !busy && !savingSchedule && (metro || scheduleTime !== '')
    && (needsNetwork || currentEstimate?.fits_budget)
  const liveJobId = job && !terminal.has(job.status) ? job.job_id : null
  const cached = results[mode]
  const result = matchesDestination(cached?.value.destination) ? cached?.value : undefined
  const showingActiveJob = busy && (jobParams?.mode ?? 'driving') === mode && matchesDestination(jobParams?.destination)
  const mapResult = showingActiveJob ? preview : result ?? null
  const staleResult = Boolean(result && !showingActiveJob && cached?.key !== key)
  const completedPoints = useRef(0)
  completedPoints.current = job?.completed_points ?? 0

  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(null), 6000)
    return () => window.clearTimeout(timer)
  }, [notice])

  useEffect(() => {
    setNotice(previous => staleResult
      ? previous?.error ? previous : { messages: ['旧结果 · 参数已修改'], stale: true }
      : previous?.stale ? null : previous)
  }, [staleResult])

  useEffect(() => {
    if (!samples || !liveJobId || !showingActiveJob) return
    const abort = new AbortController()
    let cursor = 0
    let timer = 0
    const points: Sample[] = []
    async function refresh() {
      const previousLength = points.length
      try {
        while (!abort.signal.aborted && cursor < completedPoints.current) {
          const page = await request<{ samples: Sample[]; next: number }>(
            `${jobs}/${liveJobId}/samples?after=${cursor}`, undefined, abort.signal,
          )
          if (abort.signal.aborted || page.next <= cursor) break
          points.push(...page.samples)
          cursor = page.next
        }
      } catch {
        // Retry on the next refresh; routing progress is independent of the overlay.
      } finally {
        if (!abort.signal.aborted) {
          if (points.length !== previousLength) setLiveSamples({ jobId: liveJobId!, points: points.slice() })
          timer = window.setTimeout(() => void refresh(), 300)
        }
      }
    }
    void refresh()
    return () => {
      abort.abort()
      window.clearTimeout(timer)
    }
  }, [liveJobId, samples, showingActiveJob])

  useEffect(() => {
    const abort = new AbortController()
    request<SavedAmapSettings>('/settings/amap', undefined, abort.signal)
      .then(setSavedAmap)
      .catch(() => { if (!abort.signal.aborted) setNotice({ messages: ['无法读取服务配置'], error: true }) })
    return () => {
      abort.abort()
      submission.current?.abort()
      stream.current?.close()
      stream.current = null
    }
  }, [])

  useEffect(() => {
    if (queryPanel || (!panel && !layersOpen)) return
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
  }, [panel, layersOpen, queryPanel])

  useEffect(() => {
    if (preparingNetwork || (!canCalculate && !metro)) return
    const abort = new AbortController()
    const timer = window.setTimeout(refresh, 180)
    async function refresh() {
      try {
        const value = await request<Estimate>('/isochrones/estimate', params, abort.signal)
        if (abort.signal.aborted) return
        setEstimate({ key, value })
        if (!metro && !value.fits_budget) setNotice({ messages: ['计算预算不足，无法开始采样。'], error: true })
      } catch (cause) {
        if (abort.signal.aborted) return
        if (cause instanceof Error) setNotice({ messages: [cause.message], error: true })
      }
    }
    return () => {
      window.clearTimeout(timer)
      abort.abort()
    }
  }, [params, key, canCalculate, metro, preparingNetwork])

  function choose(point: Destination, name = '') {
    const selected = destinationFromInputs(point.lng.toFixed(6), point.lat.toFixed(6))
    if (!selected) return false
    pointLookup.current = null
    setMapPointLabel('')
    setDestination(selected)
    setDestinationName(Array.from(name.trim()).slice(0, 256).join(''))
    setQuery(name)
    setPlaces([])
    return true
  }

  function chooseMapPoint(point: Destination) {
    if (!choose(point) || !amapConfig || reloadMap) return
    setMapPointLabel('正在识别地点…')
    const lookup = (savedAmap?.routing_configured
      ? request<{ name: string }>('/places/reverse', point, AbortSignal.timeout(4000)).then(result => result.name)
      : identifyAmapPoint(amapConfig, point))
      .then(name => Array.from(name).slice(0, 256).join(''))
      .catch(() => '')
    pointLookup.current = lookup
    void lookup.then(name => {
      if (pointLookup.current !== lookup) return
      setMapPointLabel(name || '未识别到地点 · 已保留坐标')
      if (!name) return
      setDestinationName(name)
      setQuery(value => value || name)
    })
  }

  async function findPlace() {
    if (!amapConfig || searching || !query.trim()) return
    setSearching(true)
    setPlaces([])
    try {
      const matches = await searchAmap(amapConfig, query.trim())
      setPlaces(matches)
      if (!matches.length) setNotice({ messages: ['未找到相关地点。'] })
    } catch (cause) {
      setNotice({ messages: [cause instanceof Error ? cause.message : '地点搜索失败。'], error: true })
    } finally {
      setSearching(false)
    }
  }

  function saveAmap(settings: SavedAmapSettings) {
    if (settings.js_key !== jsKey) setPlaces([])
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
    if (!canGenerate || submission.current) return
    const selectedLookup = pointLookup.current
    async function namedParams(base: Parameters) {
      const name = destinationName || await selectedLookup
      return name ? { ...base, destination_name: name } : base
    }
    const abort = new AbortController()
    submission.current = abort
    if (params.mode !== 'metro' && scheduleTime !== null) {
      setSavingSchedule(true)
      setNotice(null)
      try {
        const plan = await request<Schedule>(schedules, { time: scheduleTime, params: await namedParams(params) }, abort.signal)
        if (abort.signal.aborted) return
        setScheduleTime(null)
        dismissMapPanels()
        setNotice({ messages: [`已设置每日 ${plan.clock}（北京时间）自动查询。`] })
      } catch (cause) {
        if (!abort.signal.aborted) setNotice({ messages: [cause instanceof Error ? cause.message : '无法保存定时查询'], error: true })
      } finally {
        submission.current = null
        setSavingSchedule(false)
      }
      return
    }
    dismissMapPanels()
    setStarting(true)
    setJobParams(params)
    setJob(null)
    setNotice(null)
    setPreview(null)
    setLiveSamples(null)
    try {
      if (needsNetwork) {
        setPreparingNetwork(true)
        const timeout = AbortSignal.timeout(240_000)
        const signal = AbortSignal.any([abort.signal, timeout])
        try {
          let status = networkStatus === 'building' ? networkStatus
            : (await request<MetroNetwork>('/metro/networks', params.destination, signal)).status
          while (status === 'building') {
            await new Promise(resolve => window.setTimeout(resolve, 1200))
            const value = await request<Estimate>('/isochrones/estimate', params, signal)
            if (value.metro_network?.status === 'failed') throw new Error(value.metro_network.message || '无法生成地铁路网')
            status = value.metro_network?.status ?? 'missing'
          }
          if (status !== 'ready') throw new Error('地铁路网未就绪，请重试')
        } catch (cause) {
          if (timeout.aborted) throw new Error('等待地铁路网超时，请重试')
          throw cause
        } finally {
          setPreparingNetwork(false)
        }
      }
      const created = await request<Job>(jobs, await namedParams(params), abort.signal)
      if (abort.signal.aborted) return
      setJob(created)
      const path = jobs + '/' + created.job_id
      const source = new EventSource('/api/v1' + path + '/events')
      stream.current?.close()
      stream.current = source
      let previewRequest: AbortController | null = null
      let reconnecting = false
      source.onopen = () => { reconnecting = false }
      source.onerror = () => {
        if (reconnecting) return
        reconnecting = true
        setNotice({ messages: ['进度连接中断，正在重连…'] })
      }
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
        if (next.error) setNotice({ messages: [next.error.message], error: true })
        if (next.status === 'partial' || next.status === 'completed') {
          setStarting(true)
          try {
            const completed = await request<Result>(path + '/result')
            setResults(previous => ({ ...previous, [mode]: { value: completed, key } }))
            const messages = (completed.warnings ?? []).filter(warning =>
              warning !== '未知区域不填色。' && !warning.startsWith('站间和换乘耗时为静态估算。'))
            if (next.status === 'partial' && messages.length === 0) messages.push('已生成部分结果。')
            if (completed.bands.features.length === 0 && completed.contours.features.length === 0) {
              messages.push('当前时长内未形成可达区。')
            }
            setNotice(messages.length ? { messages } : null)
          } catch (cause) {
            setNotice({ messages: [cause instanceof Error ? cause.message : '无法读取结果'], error: true })
          } finally {
            setPreview(null)
            setStarting(false)
          }
        } else {
          setPreview(null)
          if (next.status === 'cancelled') setNotice({ messages: ['已取消计算。'] })
        }
      }
      for (const event of ['snapshot', 'progress', 'completed', 'failed', 'cancelled']) {
        source.addEventListener(event, message => {
          if (message instanceof MessageEvent) void update(message)
        })
      }
    } catch (cause) {
      if (!abort.signal.aborted) setNotice({ messages: [cause instanceof Error ? cause.message : '无法创建任务'], error: true })
    } finally {
      submission.current = null
      setStarting(false)
    }
  }

  async function cancel() {
    if (!job || terminal.has(job.status)) return
    try {
      await request<Job>(jobs + '/' + job.job_id + '/cancel', {})
    } catch (cause) {
      setNotice({ messages: [cause instanceof Error ? cause.message : '取消失败'], error: true })
    }
  }

  function viewHistory(entry: SavedResult) {
    const { destination, max_duration_min, contour_interval_min } = entry.summary.params
    const options = { destination: { lng: destination.lng, lat: destination.lat }, max_duration_min, contour_interval_min }
    const next = entry.summary.params
    const historyMode = next.mode ?? 'driving'
    pointLookup.current = null
    setMapPointLabel('')
    setDestination(options.destination)
    setDestinationName(next.destination_name ?? '')
    setQuery(next.destination_name ?? '')
    setPlaces([])
    setMode(historyMode)
    setScheduleTime(null)
    setDuration(max_duration_min)
    setIntervalIndex(contourIntervals.findIndex(value => value === contour_interval_min))
    const historyParams = next.mode === 'metro'
      ? { ...options, mode: 'metro', traffic_mode: 'static', walk_speed_m_s: next.walk_speed_m_s }
      : { ...options, min_spacing_m: next.min_spacing_m }
    if (next.mode === 'metro') setWalkSpeed(next.walk_speed_m_s)
    else setSpacingIndex(minimumSpacings.findIndex(value => value === next.min_spacing_m))
    setResults(previous => ({ ...previous, [historyMode]: { value: entry.result, key: JSON.stringify(historyParams) } }))
    setJob(null)
    setPreview(null)
    setNotice(null)
    setQueryPanel(false)
    dismissMapPanels()
  }

  const analysisOpen = panel === 'analysis'
  const settingsOpen = panel === 'amap'
  const setupMessage = !amapConfig
    ? `请填写 Web 端 JS Key 和安全密钥${metro || savedAmap?.routing_configured ? '。' : '，以及用于算路的 Web 服务 Key。'}`
    : '请填写 Web 服务 Key，以计算等时圈。'
  const jobStage = preparingNetwork ? '正在生成地铁路网…'
    : liveJobId && job ? stages[job.stage] : job ? '正在读取结果…' : '正在提交任务…'
  const cancelButton = liveJobId && <button type="button" className="secondary" onClick={() => void cancel()}>取消</button>
  const activeProgress = busy && <ProgressPopup label={jobStage} action={cancelButton} />
  const mapNotice = notice && (!notice.stale || staleResult) && <div className={`map-notice${notice.error ? ' error' : ''}`}
    role={notice.error ? 'alert' : 'status'}>
    {notice.messages.map(message => <p key={message}>{message}</p>)}
  </div>
  const layerControl = <div className="layer-dock">
    <button type="button" id="layers-toggle" className="floating-button"
      aria-label="图层"
      aria-expanded={layersOpen} aria-controls="map-layers"
      onClick={() => setLayersOpen(value => !value)}><Icon name="layers" /></button>
    {layersOpen && <div className="layers floating-panel" id="map-layers" aria-label="结果图层">
      <label><input type="checkbox" checked={bands} onChange={e => setBands(e.target.checked)} />等时面</label>
      <label><input type="checkbox" checked={lines} onChange={e => setLines(e.target.checked)} />等时线</label>
      <label><input type="checkbox" checked={samples} onChange={e => setSamples(e.target.checked)} />采样点</label>
    </div>}
  </div>

  return (
    <main className="app">
      <MapView center={destination} radius={duration <= 30 ? 7500 : duration <= 60 ? 15000 : 30000} result={mapResult}
        notice={mapNotice}
        liveSamples={liveJobId && showingActiveJob ? (liveSamples?.jobId === liveJobId ? liveSamples.points : []) : undefined}
        bands={bands} lines={lines} samples={samples} onSelect={chooseMapPoint} onMapInteract={dismissMapPanels}
        amapConfig={reloadMap ? null : amapConfig} layerControl={layerControl} onConfigure={() => setPanel('amap')}
        jobControl={activeProgress}
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
        <button type="button" id="analysis-toggle" className="dock-toggle" aria-expanded={analysisOpen}
          aria-label="TISO · 交通可达性" title={analysisOpen ? undefined : 'TISO · 交通可达性'}
          aria-controls="analysis-controls" onClick={() => togglePanel('analysis')}>
          <span className="brand-icon"><img src={tisoIcon} alt="" /></span>
          <span className="brand-name"><strong className="menu-title">TISO</strong><span>交通可达性</span></span>
          {analysisOpen && <span className="dock-collapse">收起</span>}
          <Icon name="chevron" />
        </button>
        <section className="dock-body" id="analysis-controls" aria-labelledby="analysis-title" inert={!analysisOpen}>
          <div className="dock-content">
            <div className="analysis-card">
              <h1 id="analysis-title" className="visually-hidden">等时圈分析</h1>
              {amapConfig && !reloadMap && (
                <form className="search" onSubmit={event => { event.preventDefault(); void findPlace() }}>
                  <Icon name="search" />
                  <input aria-label="地点关键词" placeholder="搜索目的地" value={query} onChange={event => setQuery(event.target.value)} />
                  <button type="submit" className="primary" disabled={searching || !query.trim()}>{searching ? '搜索中…' : '搜索'}</button>
                  {!!places.length && <ul className="search-results">{places.map(({ name, address, point }, index) =>
                    <li key={index}><button type="button" onClick={() => choose(point, name)}>
                      {name}{address && ` · ${address}`}
                    </button></li>)}</ul>}
                </form>
              )}

              <div className="mode-switch" role="radiogroup" aria-label="交通方式" data-mode={mode}>
                <label>
                  <input type="radio" name="travel-mode" value="driving" checked={!metro}
                    onChange={() => setMode('driving')} />
                  <span><Icon name="car" />驾车</span>
                </label>
                <label>
                  <input type="radio" name="travel-mode" value="metro" checked={metro}
                    onChange={() => { setMode('metro'); setScheduleTime(null) }} />
                  <span><Icon name="transit" />地铁</span>
                </label>
              </div>

              <div className="analysis-sliders">
                <AnalysisSlider label="最长耗时" value={duration} min={15} max={120} step={5}
                  display={`${duration} 分钟`} onChange={setDuration} />
                <AnalysisSlider label="等时线间隔" value={intervalIndex} min={0} max={contourIntervals.length - 1}
                  display={`${interval} 分钟`} onChange={setIntervalIndex} />
                {metro
                  ? <AnalysisSlider label="步行速度" value={walkSpeed} min={0.5} max={7} step={0.1}
                    display={`${walkSpeed.toFixed(1)} m/s`} onChange={setWalkSpeed} />
                  : <AnalysisSlider label="最小间距" value={spacingIndex} min={0} max={minimumSpacings.length - 1}
                    display={`${spacing} 米`} onChange={setSpacingIndex} />}
                {!metro && <div className="schedule-option" data-open={scheduleEnabled} onKeyDown={event => {
                  if (event.key !== 'Escape' || !scheduleEnabled || savingSchedule || event.defaultPrevented) return
                  event.stopPropagation()
                  setScheduleTime(null)
                  scheduleToggle.current?.focus()
                }}>
                  <button type="button" className="schedule-enable" ref={scheduleToggle}
                    aria-expanded={scheduleEnabled} aria-controls="schedule-fields" disabled={savingSchedule}
                    onClick={event => {
                      if (scheduleEnabled) event.currentTarget.focus()
                      setScheduleTime(current => current === null ? '' : null)
                    }}>
                    <Icon name="clock" />{scheduleEnabled ? '关闭定时' : '开启定时'}<Icon name="chevron" />
                  </button>
                  <div id="schedule-fields" className="schedule-fields" inert={!scheduleEnabled} aria-hidden={!scheduleEnabled}>
                    <input id="schedule-time" type="time" aria-label="定时时间" value={scheduleTime ?? ''}
                      required={scheduleEnabled} disabled={!scheduleEnabled || savingSchedule}
                      onChange={event => setScheduleTime(event.target.value)} />
                  </div>
                </div>}
              </div>
              <div className="action-pair analysis-actions">
                <button type="button" className="primary generate" disabled={!canGenerate}
                  onClick={() => void generate()}><Icon name="arrow" />{savingSchedule ? '正在保存…' : busy ? '正在计算…' : '生成等时圈'}</button>
                <button type="button" className="secondary" onClick={() => setQueryPanel(true)}><Icon name="history" />历史结果</button>
              </div>
            </div>
          </div>
        </section>
      </div>

      <div className={`dock settings-dock${settingsOpen ? ' expanded' : ''}`}>
        <button type="button" id="amap-toggle" className="dock-toggle" disabled={!savedAmap}
          aria-label="高德设置" title={settingsOpen ? undefined : '高德设置'}
          aria-expanded={settingsOpen} aria-controls="amap-settings" onClick={() => togglePanel('amap')}>
          <span className="brand-icon"><Icon name="key" /></span><span className="menu-title">高德设置</span>
          {settingsOpen && <span className="dock-collapse">收起</span>}
          <Icon name="chevron" />
        </button>
        <div className="dock-body" id="amap-settings" inert={!settingsOpen}>
          <div className="dock-content">
            {savedAmap && <AmapSettings key={String(settingsOpen)} settings={savedAmap} busy={busy} onSave={saveAmap} />}
          </div>
        </div>
      </div>
      {!panel && !busy && mapPointLabel && <div className="map-point-label" role="status">
        <Icon name="pin" /><span title={mapPointLabel}>{mapPointLabel}</span>
      </div>}
      {queryPanel && <QueryPanel busy={busy} onClose={() => setQueryPanel(false)} onView={viewHistory}
        onDelete={id => setResults(previous => ({
          driving: previous.driving?.value.job_id === id ? undefined : previous.driving,
          metro: previous.metro?.value.job_id === id ? undefined : previous.metro,
        }))} />}
    </main>
  )
}

createRoot(document.getElementById('root')!).render(<App />)
