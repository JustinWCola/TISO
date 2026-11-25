import { useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { amapConfigured, searchAmap } from './amap'
import MapView from './MapView'
import { destinationFromInputs, jobs, request, terminal } from './api'
import type { Destination, Estimate, Job, Parameters, Result } from './api'
import './style.css'

const shanghai = { lng: 121.4737, lat: 31.2304 }
const stages: Record<string, string> = {
  queued: '排队中', sampling: '采样中', routing: '算路中', geometry: '计算等时圈',
  completed: '计算完成', failed: '计算失败', cancelled: '已取消',
}

function App() {
  const [lng, setLng] = useState(String(shanghai.lng))
  const [lat, setLat] = useState(String(shanghai.lat))
  const [query, setQuery] = useState('')
  const [places, setPlaces] = useState<Awaited<ReturnType<typeof searchAmap>>>([])
  const [searchStatus, setSearchStatus] = useState('')
  const [searching, setSearching] = useState(false)
  const [radius, setRadius] = useState(15)
  const [spacing, setSpacing] = useState(1000)
  const [duration, setDuration] = useState(60)
  const [interval, setInterval] = useState(15)
  const [maxRequests, setMaxRequests] = useState(150)
  const [provider, setProvider] = useState<Parameters['provider']>('mock')
  const [hasAmap, setHasAmap] = useState(false)
  const [job, setJob] = useState<Job | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [connection, setConnection] = useState('')
  const [estimate, setEstimate] = useState<{ key: string; value: Estimate } | null>(null)
  const [estimateError, setEstimateError] = useState('')
  const [result, setResult] = useState<{ key: string; value: Result } | null>(null)
  const [bands, setBands] = useState(true)
  const [lines, setLines] = useState(true)
  const [samples, setSamples] = useState(false)
  const stream = useRef<EventSource | null>(null)

  const destination = useMemo(() => destinationFromInputs(lng, lat), [lng, lat])
  const params = useMemo<Parameters | null>(() => destination && ({
    destination, provider, radius_m: radius * 1000, spacing_m: spacing,
    max_duration_min: duration, contour_interval_min: interval,
    max_points: 2000, max_requests: maxRequests,
  }), [destination, provider, radius, spacing, duration, interval, maxRequests])
  const key = JSON.stringify(params)
  const currentEstimate = estimate?.key === key ? estimate.value : null
  const busy = starting || (job !== null && !terminal.has(job.status))
  const stale = result !== null && (result.key !== key || job?.job_id !== result.value.job_id)

  useEffect(() => {
    const abort = new AbortController()
    request<{ providers: string[] }>('/capabilities', undefined, abort.signal)
      .then(data => setHasAmap(data.providers.includes('amap')))
      .catch(() => { if (!abort.signal.aborted) setConnection('无法读取服务配置') })
    return () => {
      abort.abort()
      stream.current?.close()
    }
  }, [])

  useEffect(() => {
    setEstimateError('')
    if (!params) return
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
  }, [params, key])

  function choose(point: Destination) {
    if (destinationFromInputs(String(point.lng), String(point.lat))) {
      setLng(point.lng.toFixed(6))
      setLat(point.lat.toFixed(6))
    }
  }

  async function findPlace() {
    if (searching || !query.trim()) return
    setSearching(true)
    setPlaces([])
    setSearchStatus('')
    try {
      const matches = await searchAmap(query.trim())
      setPlaces(matches)
      if (!matches.length) setSearchStatus('未找到相关地点。')
    } catch (cause) {
      setSearchStatus(cause instanceof Error ? cause.message : '地点搜索失败。')
    } finally {
      setSearching(false)
    }
  }

  async function generate() {
    if (!params || busy || !currentEstimate?.fits_budget) return
    setStarting(true)
    setError('')
    setConnection('')
    try {
      const created = await request<Job>(jobs, params)
      setJob(created)
      const path = jobs + '/' + created.job_id
      const source = new EventSource('/api/v1' + path + '/events')
      stream.current?.close()
      stream.current = source
      source.onopen = () => setConnection('')
      source.onerror = () => setConnection('进度连接中断，正在重连…')
      async function update(event: MessageEvent<string>) {
        const next: Job = JSON.parse(event.data)
        setJob(next)
        if (!terminal.has(next.status)) return
        source.close()
        setConnection('')
        if (next.error) setError(next.error.message)
        if (next.status === 'partial' || next.status === 'completed') {
          setStarting(true)
          try {
            setResult({ key, value: await request<Result>(path + '/result') })
          } catch (cause) {
            setError(cause instanceof Error ? cause.message : '无法读取结果')
          } finally {
            setStarting(false)
          }
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

  return (
    <main className="app">
      <header className="top">
        <div className="brand"><span aria-hidden="true" className="brand-icon">◎</span>TISO</div>
        <div className="tagline">交通可达性分析</div>
        <span className="provider"><span className="signal" />{provider === 'mock' ? '离线模拟' : '高德实时驾车'}</span>
      </header>

      <div className="workspace">
        <aside className="panel" aria-label="分析设置">
          <form onSubmit={event => { event.preventDefault(); void generate() }}>
            <h1>到达这里需要多久？</h1>
            <p className="subheading">以目的地为中心，计算周边驾车可达范围。</p>

            <fieldset>
              <legend>目的地</legend>
              <div className="pair">
                <label>经度<input inputMode="decimal" value={lng} onChange={e => setLng(e.target.value)} /></label>
                <label>纬度<input inputMode="decimal" value={lat} onChange={e => setLat(e.target.value)} /></label>
              </div>
              {!destination && <p className="hint error">请输入中国大陆范围内的经纬度。</p>}
              <button type="button" className="text-button" onClick={() => choose(shanghai)}>使用上海示例</button>
              {amapConfigured && <>
                <div className="search">
                  <input aria-label="地点关键词" placeholder="输入地点关键词" value={query}
                    onChange={event => setQuery(event.target.value)} onKeyDown={event => {
                      if (event.key === 'Enter') { event.preventDefault(); void findPlace() }
                    }} />
                  <button type="button" className="secondary" disabled={searching || !query.trim()}
                    onClick={() => void findPlace()}>{searching ? '搜索中' : '搜索'}</button>
                </div>
                {searchStatus && <p className="hint" role="status">{searchStatus}</p>}
                <ul className="search-results">{places.map(({ name, point }, index) =>
                  <li key={index}><button type="button" className="text-button" onClick={() => {
                    choose(point); setPlaces([])
                  }}>{name}</button></li>
                )}</ul>
              </>}
            </fieldset>

            <fieldset>
              <legend>分析参数</legend>
              {hasAmap && <label className="setting">数据源
                <select value={provider} onChange={e => setProvider(e.target.value as Parameters['provider'])}>
                  <option value="mock">离线模拟</option><option value="amap">高德 · 使用配额</option>
                </select>
              </label>}
              <label className="setting">分析半径<select value={radius} onChange={e => setRadius(Number(e.target.value))}>
                {[1, 3, 5, 10, 15, 20, 30].map(value => <option key={value} value={value}>{value} km</option>)}
              </select></label>
              <label className="setting">采样精度<select value={spacing} onChange={e => setSpacing(Number(e.target.value))}>
                <option value={2000}>快速 · 2 km</option><option value={1000}>标准 · 1 km</option>
                <option value={500}>精细 · 500 m</option>
              </select></label>
              <label className="setting">最长耗时<select value={duration} onChange={e => setDuration(Number(e.target.value))}>
                {[15, 30, 45, 60, 90, 120].map(value => <option key={value} value={value}>{value} 分钟</option>)}
              </select></label>
              <label className="setting">等时线间隔<select value={interval} onChange={e => setInterval(Number(e.target.value))}>
                {[5, 10, 15, 20, 30].map(value => <option key={value} value={value}>{value} 分钟</option>)}
              </select></label>
              <label className="setting">批次预算<input className="budget" type="number" min="1" max="150"
                value={maxRequests || ''} onChange={e => setMaxRequests(Number(e.target.value))} /></label>
            </fieldset>

            <div className="estimate" aria-live="polite">
              {currentEstimate ? <>
                <span>{currentEstimate.total_points} 个采样点 · {currentEstimate.batches} 批</span>
                <span>预计 {currentEstimate.estimated_requests} 次外部请求</span>
                {!currentEstimate.fits_budget && <span className="error">超出预算，请缩小半径或降低精度。</span>}
              </> : <span>{estimateError || (destination ? '等待预算估算…' : '请选择目的地')}</span>}
            </div>
            <div className="actions">
              <button className="primary" disabled={busy || !currentEstimate?.fits_budget}>
                {busy ? '正在计算…' : '生成等时圈'}<span aria-hidden="true"> ↗</span>
              </button>
              {job && !terminal.has(job.status) &&
                <button type="button" className="secondary" onClick={() => void cancel()}>取消</button>}
            </div>
          </form>
          <div role="status" className="job-status">
            {job && <span>{job.status === 'partial' ? '计算完成 · 存在缺测' : stages[job.stage]} · {job.completed_points}/{job.total_points} 点 · {job.requests_sent} 次外部请求</span>}
            {connection && <span>{connection}</span>}
            {error && <span className="error">{error}</span>}
          </div>
        </aside>

        <div className="visual">
          <div className="toolbar">
            <div className="layers" aria-label="结果图层">
              <label><input type="checkbox" checked={bands} onChange={e => setBands(e.target.checked)} />等时面</label>
              <label><input type="checkbox" checked={lines} onChange={e => setLines(e.target.checked)} />等时线</label>
              <label><input type="checkbox" checked={samples} onChange={e => setSamples(e.target.checked)} />采样点</label>
            </div>
            <span>驾车 · 反向可达</span>
          </div>
          <MapView center={destination ?? result?.value.destination ?? shanghai} radius={radius * 1000}
            result={result?.value ?? null} bands={bands} lines={lines} samples={samples} onSelect={choose} />
          <div className="map-footer" aria-live="polite">
            {result ? <>
              <strong>{stale ? '旧结果 · ' : ''}{result.value.simulated ? '模拟数据' : '高德驾车数据'}</strong>
              <span>{result.value.success_count}/{result.value.sample_count} 点有效 ·
                未知区域 {((1 - result.value.coverage) * 100).toFixed(1)}%</span>
              {result.value.observed_from && result.value.observed_to && <span>{new Date(result.value.observed_from).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai' })}
                –{new Date(result.value.observed_to).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai' })}（北京时间）</span>}
              <span>耗时为近似值。{result.value.warnings.join('')}</span>
            </> : <span>设置目的地后生成结果。坐标视图可离线使用。</span>}
          </div>
        </div>
      </div>
    </main>
  )
}

createRoot(document.getElementById('root')!).render(<App />)
