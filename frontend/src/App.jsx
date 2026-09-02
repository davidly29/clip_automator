import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

/* ---------------------------------------------------------------- hooks --- */

function useAuth() {
  const [state, setState] = useState({ user: null, authRequired: true, loading: true, error: null })

  const refresh = useCallback(async () => {
    try {
      const r = await fetch('/api/me')
      if (r.status === 401) {
        const d = await r.json().catch(() => ({}))
        setState({ user: null, authRequired: d.authRequired !== false, loading: false, error: null })
        return
      }
      const d = await r.json()
      setState({ user: d.user, authRequired: d.authRequired, loading: false, error: null })
    } catch (e) {
      setState((s) => ({ ...s, loading: false, error: String(e) }))
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const login = useCallback(async (username, password) => {
    const r = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      throw new Error(d.error || 'Login failed')
    }
    await refresh()
  }, [refresh])

  const logout = useCallback(async () => {
    await fetch('/api/logout', { method: 'POST' }).catch(() => {})
    await refresh()
  }, [refresh])

  return { ...state, login, logout, refresh }
}

function useVideos() {
  const [videos, setVideos] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const r = await fetch('/api/videos')
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setVideos(await r.json())
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])
  return { videos, loading, error, refresh }
}

const ACTIVE = new Set(['queued', 'running'])

function useRuns(onSettled) {
  const [runs, setRuns] = useState([])
  const timer = useRef(null)
  const prevActive = useRef(0)

  const refresh = useCallback(async () => {
    try {
      const r = await fetch('/api/runs')
      if (!r.ok) return
      const data = await r.json()
      setRuns(data)
      const active = data.filter((j) => ACTIVE.has(j.status)).length
      if (prevActive.current > 0 && active === 0) onSettled?.()
      prevActive.current = active
    } catch { /* ignore poll errors */ }
  }, [onSettled])

  useEffect(() => {
    refresh()
    timer.current = setInterval(refresh, 2000)
    return () => clearInterval(timer.current)
  }, [refresh])

  const startRun = useCallback(async (params) => {
    const r = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
    const d = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`)
    await refresh()
    return d
  }, [refresh])

  return { runs, refresh, startRun }
}

/* ------------------------------------------------------------- helpers --- */

const verdictClass = (p) => (p === true ? 'pass' : p === false ? 'fail' : 'unknown')

const STATUS_LABEL = { queued: 'Queued', running: 'Running', passed: 'Pass', failed: 'Fail', error: 'Error' }
const STATUS_CLASS = { queued: 'muted', running: 'info', passed: 'pass', failed: 'fail', error: 'warn' }

function timeAgo(iso) {
  if (!iso) return ''
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return `${Math.floor(s)}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function StatusBadge({ status }) {
  return (
    <span className={`badge ${STATUS_CLASS[status] || 'muted'}`}>
      {status === 'running' && <span className="spin" />}
      <span className="badge-dot" />
      {STATUS_LABEL[status] || status}
    </span>
  )
}

function Verdict({ passed, code }) {
  const text = passed === true ? 'PASS' : passed === false ? 'FAIL' : 'N/A'
  return (
    <span className={`badge ${verdictClass(passed)}`}>
      <span className="badge-dot" />
      {text}
      {passed === false && code ? <span className="badge-code">{code}</span> : null}
    </span>
  )
}

/* --------------------------------------------------------------- Login --- */

function Login({ onLogin }) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError(null)
    try { await onLogin(username, password) }
    catch (err) { setError(err.message || String(err)) }
    finally { setBusy(false) }
  }

  return (
    <div className="auth-screen">
      <form className="auth-card" onSubmit={submit}>
        <div className="brand brand-lg"><span className="logo" /> VPV</div>
        <p className="auth-sub">Playback Verifier — sign in to continue</p>
        <label className="field">
          <span>Username</span>
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </label>
        <label className="field">
          <span>Password</span>
          <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <div className="form-error">{error}</div>}
        <button className="btn btn-primary btn-block" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}

/* -------------------------------------------------------------- Verify --- */

const ADVANCED_FIELDS = [
  ['video_selector', 'Video selector', 'video'],
  ['play_selector', 'Play button selector', ''],
  ['fullscreen_selector', 'Fullscreen button selector', ''],
  ['fullscreen_target', 'Fullscreen target selector', ''],
  ['dismiss_selectors', 'Dismiss selectors (comma-separated)', '#consent, #age-gate'],
  ['search_selector', 'Search box selector', ''],
  ['search_query', 'Search query', ''],
  ['result_selector', 'Result-to-open selector', ''],
  ['random_selector', 'Random pick selector', ''],
  ['skip_ad_selector', 'Skip-ad selector', ''],
  ['viewport', 'Viewport (WxH)', '1920x1080'],
]

function Verify({ startRun, goToRuns }) {
  const [url, setUrl] = useState('')
  const [mode, setMode] = useState('clip')
  const [duration, setDuration] = useState(14)
  const [fullscreen, setFullscreen] = useState(true)
  const [autoDismiss, setAutoDismiss] = useState(true)
  const [advOpen, setAdvOpen] = useState(false)
  const [adv, setAdv] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const setAdvField = (k, v) => setAdv((a) => ({ ...a, [k]: v }))

  const submit = async (e) => {
    e.preventDefault()
    setError(null); setNotice(null)
    if (!url.trim()) { setError('A URL is required.'); return }
    const params = {
      url: url.trim(),
      mode,
      duration_s: duration,
      fullscreen,
      auto_dismiss_consent: autoDismiss,
    }
    for (const [k, v] of Object.entries(adv)) {
      if (v != null && String(v).trim() !== '') params[k] = v
    }
    setBusy(true)
    try {
      await startRun(params)
      setNotice('Run started — tracking it in Runs.')
      goToRuns()
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="view view-narrow">
      <h1 className="view-title">Verify a video</h1>
      <p className="view-lead">Point VPV at a page, and it will confirm the video actually plays and capture proof.</p>

      <form className="panel" onSubmit={submit}>
        <label className="field">
          <span>Page URL <em className="req">required</em></span>
          <input className="input" placeholder="https://example.com/watch/123"
            value={url} onChange={(e) => setUrl(e.target.value)} autoFocus />
        </label>

        <div className="field-row">
          <label className="field">
            <span>Mode</span>
            <select className="select" value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="clip">Clip (video)</option>
              <option value="frames">Frames (screenshots)</option>
            </select>
          </label>
          <label className="field">
            <span>Duration (s)</span>
            <input className="input" type="number" min="1" step="1"
              value={duration} onChange={(e) => setDuration(e.target.value)} />
          </label>
        </div>

        <div className="toggles">
          <label className="switch">
            <input type="checkbox" checked={fullscreen} onChange={(e) => setFullscreen(e.target.checked)} />
            <span>Fullscreen before capture</span>
          </label>
          <label className="switch">
            <input type="checkbox" checked={autoDismiss} onChange={(e) => setAutoDismiss(e.target.checked)} />
            <span>Auto-dismiss consent modals</span>
          </label>
        </div>

        <button type="button" className={`disclosure ${advOpen ? 'open' : ''}`} onClick={() => setAdvOpen((o) => !o)}>
          <span className="chev">▸</span> Advanced selectors
        </button>
        {advOpen && (
          <div className="adv-grid">
            {ADVANCED_FIELDS.map(([key, label, ph]) => (
              <label className="field" key={key}>
                <span>{label}</span>
                <input className="input" placeholder={ph}
                  value={adv[key] || ''} onChange={(e) => setAdvField(key, e.target.value)} />
              </label>
            ))}
          </div>
        )}

        {error && <div className="form-error">{error}</div>}
        {notice && <div className="form-notice">{notice}</div>}
        <div className="panel-actions">
          <button className="btn btn-primary" disabled={busy}>{busy ? 'Starting…' : 'Run check'}</button>
        </div>
      </form>
    </div>
  )
}

/* ---------------------------------------------------------------- Runs --- */

function Runs({ runs, onOpenArtifact }) {
  const [openId, setOpenId] = useState(null)
  if (runs.length === 0) {
    return <div className="view"><div className="empty">No runs yet. Start one from <b>Verify</b>.</div></div>
  }
  return (
    <div className="view">
      <h1 className="view-title">Runs</h1>
      <div className="table">
        <div className="tr th">
          <div className="td col-status">Status</div>
          <div className="td col-url">URL</div>
          <div className="td col-code">Code</div>
          <div className="td col-time">Started</div>
        </div>
        {runs.map((j) => (
          <div key={j.id} className="run-row">
            <button className="tr" onClick={() => setOpenId(openId === j.id ? null : j.id)}>
              <div className="td col-status"><StatusBadge status={j.status} /></div>
              <div className="td col-url" title={j.url}>{j.url}</div>
              <div className="td col-code mono">{j.code || (j.status === 'error' ? 'error' : '—')}</div>
              <div className="td col-time">{timeAgo(j.created_at)}</div>
            </button>
            {openId === j.id && (
              <div className="run-detail">
                {j.error && <div className="detail-line"><span className="k">Error</span><span className="v">{j.error}</span></div>}
                {j.reasons?.length > 0 && (
                  <div className="detail-line"><span className="k">Reasons</span><span className="v">{j.reasons.join(' · ')}</span></div>
                )}
                {j.signals && Object.keys(j.signals).length > 0 && (
                  <div className="detail-line"><span className="k">Signals</span>
                    <span className="v mono">{Object.entries(j.signals).map(([k, v]) => `${k}=${v}`).join('  ')}</span></div>
                )}
                {j.artifact_dir && (
                  <div className="panel-actions">
                    <button className="btn btn-ghost" onClick={onOpenArtifact}>View clip in Library →</button>
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------- Library --- */

function Library({ videos, onPlay }) {
  if (videos.length === 0) {
    return <div className="view"><div className="empty">No clips captured yet. Run a check in <b>Verify</b>.</div></div>
  }
  return (
    <div className="view">
      <h1 className="view-title">Library <span className="count">{videos.length}</span></h1>
      <div className="grid">
        {videos.map((vid, i) => (
          <button className="cell" key={vid.src} onClick={() => onPlay(i)} title={vid.label}>
            {vid.poster ? <img src={vid.poster} alt="" loading="lazy" /> : <div className="noposter" />}
            <span className={`dot ${verdictClass(vid.passed)}`} />
            <span className="cell-play">▶</span>
            <span className="cell-label">{vid.label}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

function PlayerModal({ video, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  if (!video) return null
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <Verdict passed={video.passed} code={video.code} />
          <div className="modal-title">{video.label}</div>
          <button className="icon" onClick={onClose}>✕</button>
        </div>
        <video src={video.src} poster={video.poster || undefined} controls autoPlay />
        {video.reasons?.length > 0 && <div className="modal-reasons">{video.reasons.join(' · ')}</div>}
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- Feed --- */

function Feed({ videos, muted, fit, startIndex }) {
  const containerRef = useRef(null)

  useEffect(() => {
    const root = containerRef.current
    if (!root) return
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const slide = e.target
          const v = slide.querySelector('video')
          if (e.isIntersecting && v && !v.src) v.src = v.dataset.src
          if (e.isIntersecting && e.intersectionRatio >= 0.55) {
            slide.classList.add('active')
            if (v) v.play().catch(() => {})
          } else {
            slide.classList.remove('active')
            if (v) { v.pause(); if (v.readyState) { try { v.currentTime = 0 } catch { /* noop */ } } }
          }
        }
      },
      { threshold: [0, 0.55, 1] },
    )
    root.querySelectorAll('.slide').forEach((s) => io.observe(s))
    return () => io.disconnect()
  }, [videos])

  useEffect(() => {
    containerRef.current?.querySelectorAll('video').forEach((v) => { v.muted = muted })
  }, [muted, videos])

  useEffect(() => {
    if (startIndex > 0 && containerRef.current) {
      containerRef.current.scrollTo({ top: startIndex * window.innerHeight })
    }
  }, [startIndex])

  if (videos.length === 0) {
    return <div className="view"><div className="empty">No clips to play yet.</div></div>
  }

  return (
    <div className="feed" ref={containerRef}>
      {videos.map((vid) => (
        <section className="slide" key={vid.src}>
          <div className="ambient" style={vid.poster ? { backgroundImage: `url("${vid.poster}")` } : undefined} />
          <div className={`vcard paused ${fit}`}>
            <video
              data-src={vid.src} poster={vid.poster || undefined} muted={muted} loop playsInline preload="none"
              onClick={(e) => { const v = e.currentTarget; v.paused ? v.play().catch(() => {}) : v.pause() }}
              onPlay={(e) => e.currentTarget.closest('.vcard').classList.remove('paused')}
              onPause={(e) => e.currentTarget.closest('.vcard').classList.add('paused')}
            />
            <div className="play-glyph">▶</div>
            <div className="card-overlay">
              <Verdict passed={vid.passed} code={vid.code} />
              <div className="caption">{vid.label}</div>
              {vid.reasons?.length > 0 && <div className="reasons">{vid.reasons.join(' · ')}</div>}
            </div>
          </div>
        </section>
      ))}
    </div>
  )
}

/* -------------------------------------------------------------- Studio --- */

const VIDEO_RE = /\.(mp4|webm|mov|m4v|ogg|ogv)$/i
const isVideoFile = (f) => (f.type && f.type.startsWith('video/')) || VIDEO_RE.test(f.name)

function Studio({ videos }) {
  const [slots, setSlots] = useState([null, null, null])
  const [uploads, setUploads] = useState([])
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const palette = [...uploads, ...videos]
  const setSlot = (i, item) => setSlots((s) => s.map((x, j) => (j === i ? item : x)))
  const chosen = slots.filter(Boolean)

  const uploadFile = async (file) => {
    try {
      const r = await fetch(`/api/upload?name=${encodeURIComponent(file.name)}`, {
        method: 'POST', headers: { 'Content-Type': file.type || 'application/octet-stream' }, body: file,
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`)
      setUploads((u) => (u.some((x) => x.src === data.src) ? u : [data, ...u]))
      return data
    } catch (e) { setError(String(e.message || e)); return null }
  }

  const addFiles = async (fileList) => {
    const files = [...fileList].filter(isVideoFile)
    if (files.length === 0) return null
    setBusy(true); setError(null)
    let first = null
    for (const f of files) { const item = await uploadFile(f); if (item && !first) first = item }
    setBusy(false)
    return first
  }

  const onDrop = (i) => async (e) => {
    e.preventDefault()
    if (e.dataTransfer.files && e.dataTransfer.files.length) {
      const item = await addFiles(e.dataTransfer.files)
      if (item) setSlot(i, item)
      return
    }
    const idx = e.dataTransfer.getData('text/plain')
    if (idx !== '') setSlot(i, palette[+idx])
  }

  const render = async () => {
    setBusy(true); setError(null); setResult(null)
    try {
      const r = await fetch('/api/compose', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clips: chosen.map((c) => c.src) }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`)
      setResult(data.src)
    } catch (e) { setError(String(e.message || e)) } finally { setBusy(false) }
  }

  return (
    <div className="view">
      <h1 className="view-title">Studio</h1>
      <p className="view-lead">Combine up to 3 clips side by side into a single video (with sound).</p>

      <div className="slots">
        {slots.map((item, i) => (
          <div key={i} className={`slot ${item ? 'filled' : ''}`}
            onDragOver={(e) => e.preventDefault()} onDrop={onDrop(i)}>
            {item ? (
              <>
                {item.poster ? <img src={item.poster} alt="" /> : <div className="noposter" />}
                <span className="cell-label">{item.label}</span>
                <button className="slot-x" title="Remove" onClick={() => setSlot(i, null)}>×</button>
              </>
            ) : <span className="slot-hint">Drop clip {i + 1}</span>}
          </div>
        ))}
      </div>

      <div className="panel-actions center">
        <button className="btn btn-primary" disabled={busy || chosen.length === 0} onClick={render}>
          {busy ? 'Working…' : `Render ${chosen.length || ''} side by side`}
        </button>
        <label className="btn btn-ghost">
          Add files
          <input type="file" accept="video/*" multiple hidden
            onChange={(e) => { addFiles(e.target.files); e.target.value = '' }} />
        </label>
      </div>
      <p className="hint-text">Drag clips below into a slot, or drop video files from your desktop onto a slot.</p>
      {error && <div className="form-error center-text">{error}</div>}

      {result && <video className="studio-result" src={result} controls autoPlay key={result} />}

      <div className="palette">
        {palette.map((vid, i) => (
          <div className="pal-item" key={vid.src} draggable title={vid.label}
            onDragStart={(e) => e.dataTransfer.setData('text/plain', String(i))}>
            {vid.poster ? <img src={vid.poster} alt="" loading="lazy" /> : <div className="noposter" />}
            <span className="cell-label">{vid.label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ----------------------------------------------------------------- App --- */

const TABS = ['verify', 'runs', 'library', 'feed', 'studio']
const TAB_LABEL = { verify: 'Verify', runs: 'Runs', library: 'Library', feed: 'Feed', studio: 'Studio' }

export default function App() {
  const auth = useAuth()
  const { videos, loading, refresh: refreshVideos } = useVideos()
  const { runs, startRun } = useRuns(refreshVideos)
  const [tab, setTab] = useState('verify')
  const [muted, setMuted] = useState(true)
  const [fit, setFit] = useState('contain')
  const [startIndex] = useState(0)
  const [playing, setPlaying] = useState(null)

  const activeRuns = useMemo(() => runs.filter((j) => ACTIVE.has(j.status)).length, [runs])

  useEffect(() => {
    const onKey = (e) => { if ((e.key === 'm' || e.key === 'M') && tab === 'feed') setMuted((x) => !x) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [tab])

  if (auth.loading) return <div className="auth-screen"><div className="skeleton-card" /></div>
  if (auth.authRequired && !auth.user) return <Login onLogin={auth.login} />

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand"><span className="logo" /> VPV <span className="brand-sub">Playback Verifier</span></div>
        <nav className="tabs">
          {TABS.map((t) => (
            <button key={t} className={`tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
              {TAB_LABEL[t]}
              {t === 'runs' && activeRuns > 0 && <span className="tab-badge">{activeRuns}</span>}
            </button>
          ))}
        </nav>
        <div className="topbar-right">
          {tab === 'feed' && (
            <>
              <button className="icon" title="Fit / Fill" onClick={() => setFit((x) => (x === 'contain' ? 'cover' : 'contain'))}>
                {fit === 'contain' ? '⤢' : '▭'}
              </button>
              <button className="icon" title="Mute (m)" onClick={() => setMuted((m) => !m)}>{muted ? '🔇' : '🔊'}</button>
            </>
          )}
          {auth.authRequired && (
            <div className="userbox">
              <span className="user">{auth.user}</span>
              <button className="btn btn-ghost btn-sm" onClick={auth.logout}>Sign out</button>
            </div>
          )}
        </div>
      </header>

      <main className={`content ${tab === 'feed' ? 'no-pad' : ''}`}>
        {tab === 'verify' && <Verify startRun={startRun} goToRuns={() => setTab('runs')} />}
        {tab === 'runs' && <Runs runs={runs} onOpenArtifact={() => { refreshVideos(); setTab('library') }} />}
        {tab === 'library' && (
          loading ? <div className="view"><div className="skeleton-card" /></div>
            : <Library videos={videos} onPlay={(i) => setPlaying(videos[i])} />
        )}
        {tab === 'feed' && <Feed videos={videos} muted={muted} fit={fit} startIndex={startIndex} />}
        {tab === 'studio' && <Studio videos={videos} />}
      </main>

      {playing && <PlayerModal video={playing} onClose={() => setPlaying(null)} />}
    </div>
  )
}
