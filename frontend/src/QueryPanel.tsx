import { useEffect, useRef, useState } from 'react'
import { history, request, schedules } from './api'
import type { HistoryItem, Parameters, SavedResult, Schedule } from './api'
import Icon from './Icon'

const pageSize = 15
const dateFormat = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short',
})
const states: Record<string, string> = {
  running: '运行中', completed: '成功', partial: '部分结果', failed: '失败',
  cancelled: '已取消', skipped: '已跳过', interrupted: '服务重启时中断',
}

function location(params: Parameters) {
  const { lng, lat } = params.destination
  return `${lng.toFixed(4)}, ${lat.toFixed(4)}`
}

function RecordHeading({ params }: { params: Parameters }) {
  const metro = params.mode === 'metro'
  return <div className="query-record-heading">
    <span className="query-mode"><Icon name={metro ? 'transit' : 'car'} />{metro ? '地铁' : '驾车'}</span>
    <strong>{params.destination_name || location(params)}</strong>
  </div>
}

export default function QueryPanel({ busy, onClose, onView, onDelete }: {
  busy: boolean
  onClose: () => void
  onView: (entry: SavedResult) => void
  onDelete: (id: string) => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [offset, setOffset] = useState(0)
  const [past, setPast] = useState<{ items: HistoryItem[]; more: boolean } | null>(null)
  const [plans, setPlans] = useState<Schedule[] | null>(null)
  const [revision, refresh] = useState(0)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const popup = dialog.current
    popup?.showModal()
    return () => popup?.close()
  }, [])

  useEffect(() => {
    const abort = new AbortController()
    async function load() {
      try {
        await Promise.all([
          request<{ items: HistoryItem[]; more: boolean }>(
            `${history}?limit=${pageSize}&offset=${offset}`, undefined, abort.signal)
            .then(data => { if (!abort.signal.aborted) setPast(data) }),
          request<Schedule[]>(schedules, undefined, abort.signal)
            .then(data => { if (!abort.signal.aborted) setPlans(data) }),
        ])
      } catch (cause) {
        if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : '读取失败')
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), 15_000)
    return () => {
      abort.abort()
      window.clearInterval(timer)
    }
  }, [offset, revision])

  async function perform(action: () => Promise<void>) {
    setError('')
    setPending(true)
    try {
      await action()
      refresh(value => value + 1)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '操作失败')
    } finally {
      setPending(false)
    }
  }

  return <dialog className="query-panel" aria-labelledby="query-title" ref={dialog}
    onCancel={event => { event.preventDefault(); onClose() }}>
    <header className="query-header">
      <div className="query-heading">
        <span className="brand-icon"><Icon name="history" /></span>
        <h2 id="query-title" className="menu-title">历史结果</h2>
      </div>
      <button type="button" className="icon-button query-close" aria-label="关闭历史结果" onClick={onClose}>
        <Icon name="close" />
      </button>
    </header>
    <div className="query-content">
      {error && <p className="hint error" role="alert">{error}</p>}
      <section aria-labelledby="query-title" className="query-section">
        {past === null && !error && <p className="hint" role="status">正在读取历史结果…</p>}
        {past?.items.length === 0 && <p className="hint">暂无历史结果</p>}
        {!!past?.items.length && <ul className="query-list">
          {past.items.map(item => <li key={item.id}>
            <div className="query-record-content">
              <RecordHeading params={item.params} />
              <p>{dateFormat.format(new Date(item.finished_at))} · {item.params.max_duration_min} 分钟
                {item.status === 'partial' && ' · 部分结果'}
                {item.schedule_id && ' · 定时查询'}</p>
            </div>
            <div className="query-actions">
              <button type="button" className="secondary" disabled={busy || pending}
                onClick={() => void perform(async () => onView(await request<SavedResult>(history + '/' + item.id)))}>查看</button>
              <button type="button" className="secondary" disabled={pending} onClick={() => {
                if (!window.confirm('删除这条历史结果？')) return
                void perform(async () => {
                  await request(history + '/' + item.id, undefined, undefined, 'DELETE')
                  onDelete(item.id)
                  if (past.items.length === 1 && offset > 0) setOffset(value => value - pageSize)
                })
              }}>删除</button>
            </div>
          </li>)}
        </ul>}
        {(offset > 0 || past?.more) && <div className="query-actions query-pagination">
          <button type="button" className="secondary" disabled={offset === 0 || pending}
            onClick={() => { setPast(null); setOffset(value => value - pageSize) }}>上一页</button>
          <button type="button" className="secondary" disabled={!past?.more || pending}
            onClick={() => { setPast(null); setOffset(value => value + pageSize) }}>下一页</button>
        </div>}
      </section>
      {plans && plans.length > 0 && <section aria-labelledby="plans-title" className="query-section">
        <h3 id="plans-title">已设置的日程</h3>
        <ul className="query-list">
          {plans.map(plan => <li key={plan.id}>
            <div className="query-record-content">
              <RecordHeading params={plan.params} />
              <p>每天 {plan.clock} · {plan.params.max_duration_min} 分钟</p>
              <p>{plan.enabled && plan.next_run ? `下次 ${dateFormat.format(new Date(plan.next_run))}` : '已暂停'}</p>
              {plan.last_run && <p>上次 {dateFormat.format(new Date(plan.last_run))}
                {plan.last_status && ` · ${states[plan.last_status] ?? plan.last_status}`}
                {plan.last_error && ` · ${plan.last_error}`}</p>}
            </div>
            <div className="query-actions">
              <button type="button" className="secondary" disabled={pending} onClick={() => void perform(async () => {
                await request(schedules + '/' + plan.id, { enabled: !plan.enabled }, undefined, 'PATCH')
              })}>{plan.enabled ? '暂停' : '恢复'}</button>
              <button type="button" className="secondary" disabled={pending} onClick={() => {
                if (!window.confirm('删除这个定时查询？')) return
                void perform(async () => { await request(schedules + '/' + plan.id, undefined, undefined, 'DELETE') })
              }}>删除</button>
            </div>
          </li>)}
        </ul>
      </section>}
    </div>
  </dialog>
}
