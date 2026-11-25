import { useEffect, useRef, useState } from 'react'
import Icon from './Icon'
import { request } from './api'

export type SavedAmapSettings = { js_key: string; routing_configured: boolean; web_service_saved: boolean }

type SecretName = 'js_security_code' | 'web_service_key'

function CredentialField({ id, label, saved, publicValue, secret, draft, onChange, disabled }: {
  id: string; label: string; saved: boolean; publicValue?: string; secret?: SecretName
  draft: string | undefined; onChange: (value: string) => void; disabled: boolean
}) {
  const [visible, setVisible] = useState(false)
  const [revealed, setRevealed] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef<AbortController | null>(null)

  useEffect(() => () => pending.current?.abort(), [])

  async function toggleVisibility() {
    if (visible) {
      setVisible(false)
      setRevealed('')
      return
    }
    setError('')
    if (draft !== undefined || !secret) {
      setVisible(true)
      return
    }
    const abort = new AbortController()
    pending.current = abort
    setLoading(true)
    try {
      const { value } = await request<{ value: string }>('/settings/amap/reveal', { field: secret }, abort.signal)
      if (abort.signal.aborted) return
      setRevealed(value)
      setVisible(true)
    } catch (cause) {
      if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : '无法查看密钥')
    } finally {
      if (!abort.signal.aborted) setLoading(false)
    }
  }

  return <div className="credential-field">
    <label htmlFor={id}>{label}</label>
    <div className="credential-input">
      <input id={id} type={visible ? 'text' : 'password'} autoComplete="new-password" spellCheck={false}
        autoCapitalize="none" value={draft ?? (visible ? publicValue ?? revealed : '')}
        placeholder={saved && draft === undefined && !visible ? '••••••••••••' : undefined}
        maxLength={128} disabled={disabled || loading} onChange={event => onChange(event.target.value)} />
      <button type="button" className="icon-button" disabled={disabled || loading || !(draft ?? saved)}
        aria-label={`${visible ? '隐藏' : '显示'} ${label}`} aria-controls={id}
        onClick={() => void toggleVisibility()}><Icon name={visible ? 'eyeOff' : 'eye'} /></button>
    </div>
    {error && <p className="hint error" role="status">{error}</p>}
  </div>
}

export default function AmapSettings({ settings, onSave, busy }: {
  settings: SavedAmapSettings
  onSave: (settings: SavedAmapSettings) => void
  busy: boolean
}) {
  const [jsKey, setJsKey] = useState<string>()
  const [securityCode, setSecurityCode] = useState<string>()
  const [webKey, setWebKey] = useState<string>()
  const [saving, setSaving] = useState(false)
  const [revision, setRevision] = useState(0)
  const [feedback, setFeedback] = useState({ text: '', error: false })

  async function save(clear = false) {
    if (saving || busy) return
    const nextJsKey = jsKey?.trim() ?? settings.js_key
    if (!clear && nextJsKey && (securityCode?.trim() === '' || (nextJsKey !== settings.js_key && !securityCode?.trim()))) {
      setFeedback({ text: '请填写与 Web 端 JS Key 对应的安全密钥。', error: true })
      return
    }
    setSaving(true)
    setFeedback({ text: '', error: false })
    try {
      const saved = await request<SavedAmapSettings>('/settings/amap', clear ? {
        js_key: '', js_security_code: '', web_service_key: '',
      } : {
        js_key: nextJsKey, js_security_code: nextJsKey ? securityCode?.trim() ?? '' : '',
        ...(webKey === undefined ? {} : { web_service_key: webKey.trim() }),
      })
      onSave(saved)
      setJsKey(undefined)
      setSecurityCode(undefined)
      setWebKey(undefined)
      setRevision(value => value + 1)
      setFeedback({ text: clear ? '已清除本地保存的凭据。' : '已保存高德配置。', error: false })
    } catch (error) {
      setFeedback({ text: error instanceof Error ? error.message : '保存失败', error: true })
    } finally {
      setSaving(false)
    }
  }

  return <div className="amap-settings-content">
    <form autoComplete="off" onSubmit={event => { event.preventDefault(); void save() }}>
      <fieldset key={`map-${revision}`}>
        <legend><span className="settings-group-title">
          <span className="mode-icon"><Icon name="pin" /></span>底图与地点搜索
        </span></legend>
        <CredentialField id="amap-js-key" label="Web 端 JS Key" publicValue={settings.js_key}
          saved={Boolean(settings.js_key)} draft={jsKey} onChange={setJsKey} disabled={saving} />
        <CredentialField id="amap-security-code" label="JS API 安全密钥" secret="js_security_code"
          saved={Boolean(settings.js_key)} draft={securityCode} onChange={setSecurityCode} disabled={saving} />
      </fieldset>
      <fieldset key={`routing-${revision}`}>
        <legend><span className="settings-group-title">
          <span className="mode-icon"><Icon name="car" /></span>驾车算路
        </span></legend>
        <CredentialField id="amap-web-key" label="Web 服务 Key" secret="web_service_key"
          saved={settings.web_service_saved} draft={webKey} onChange={setWebKey} disabled={saving} />
        {settings.routing_configured && !settings.web_service_saved && <p className="hint">当前使用环境变量中的 Web 服务 Key。</p>}
      </fieldset>
      <a className="settings-link" href="https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode" target="_blank" rel="noreferrer">高德 Key 与安全密钥说明 ↗</a>
      <div className="actions settings-actions">
        <button type="submit" className="primary" disabled={busy || saving}>{saving ? '正在保存…' : '保存设置'}</button>
        <button className="secondary" type="button" disabled={busy || saving || (!settings.js_key && !settings.web_service_saved)}
          onClick={() => void save(true)}>清除本地配置</button>
      </div>
      {(feedback.text || busy) && <p role="status" className={`hint${feedback.error ? ' error' : ''}`}>
        {busy ? '请等待当前分析结束后再保存。' : feedback.text}
      </p>}
    </form>
  </div>
}
