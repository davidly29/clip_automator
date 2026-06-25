import { useEffect, useRef, useState } from 'react'

function useVideos() {
  const [state, setState] = useState({ videos: [], loading: true, error: null })
  useEffect(() => {
    let alive = true
    fetch('/api/videos')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((videos) => alive && setState({ videos, loading: false, error: null }))
      .catch((e) => alive && setState({ videos: [], loading: false, error: String(e) }))
    return () => { alive = false }
  }, [])
  return state
}

const verdictClass = (p) => (p === true ? 'pass' : p === false ? 'fail' : 'unknown')

function Badge({ passed, code }) {
  const text = passed === true ? 'PASS' : passed === false ? 'FAIL' : 'N/A'
  return (
    <span className={`badge ${verdictClass(passed)}`}>
      <span className="badge-dot" />
      {text}
      {passed === false && code ? <span className="badge-code">{code}</span> : null}
    </span>
  )
}

function Feed({ videos, muted, fit, startIndex }) {
  const containerRef = useRef(null)

  // Lazy-load: a clip's <video src> is only set once it scrolls into view; the
  // in-view clip autoplays + is emphasised, the rest pause/rewind.
  useEffect(() => {
    const root = containerRef.current
    if (!root) return
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const slide = e.target
          const v = slide.querySelector('video')
          if (e.isIntersecting && v && !v.src) v.src = v.dataset.src  // begin loading
          if (e.isIntersecting && e.intersectionRatio >= 0.55) {
            slide.classList.add('active')
            if (v) v.play().catch(() => {})
          } else {
            slide.classList.remove('active')
            if (v) { v.pause(); if (v.readyState) { try { v.currentTime = 0 } catch (_) {} } }
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

  useEffect(() => {
    const onKey = (ev) => {
      const c = containerRef.current
      if (!c) return
      const cur = Math.round(c.scrollTop / window.innerHeight)
      if (['ArrowDown', 'PageDown', ' '].includes(ev.key)) {
        c.scrollTo({ top: (cur + 1) * window.innerHeight, behavior: 'smooth' }); ev.preventDefault()
      } else if (['ArrowUp', 'PageUp'].includes(ev.key)) {
        c.scrollTo({ top: (cur - 1) * window.innerHeight, behavior: 'smooth' }); ev.preventDefault()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="feed" ref={containerRef}>
      {videos.map((vid) => (
        <section className="slide" key={vid.src}>
          <div
            className="ambient"
            style={vid.poster ? { backgroundImage: `url("${vid.poster}")` } : undefined}
          />
          <div className={`card paused ${fit}`}>
            <video
              data-src={vid.src}
              poster={vid.poster || undefined}
              muted={muted}
              loop
              playsInline
              preload="none"
              onClick={(e) => {
                const v = e.currentTarget
                v.paused ? v.play().catch(() => {}) : v.pause()
              }}
              onPlay={(e) => e.currentTarget.closest('.card').classList.remove('paused')}
              onPause={(e) => e.currentTarget.closest('.card').classList.add('paused')}
            />
            <div className="play-glyph">▶</div>
            <div className="card-overlay">
              <Badge passed={vid.passed} code={vid.code} />
              <div className="caption">{vid.label}</div>
              {vid.reasons && vid.reasons.length > 0 && (
                <div className="reasons">{vid.reasons.join(' · ')}</div>
              )}
            </div>
          </div>
        </section>
      ))}
    </div>
  )
}

const VIDEO_RE = /\.(mp4|webm|mov|m4v|ogg|ogv)$/i
const isVideoFile = (f) => (f.type && f.type.startsWith('video/')) || VIDEO_RE.test(f.name)

// Studio: drop 1-3 clips into the placeholders — either from the folder palette
// or dragged in from the desktop (uploaded) — then render a single side-by-side
// video (with sound) via the /api/compose endpoint.
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
        method: 'POST',
        headers: { 'Content-Type': file.type || 'application/octet-stream' },
        body: file,
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`)
      setUploads((u) => (u.some((x) => x.src === data.src) ? u : [data, ...u]))
      return data
    } catch (e) {
      setError(String(e.message || e))
      return null
    }
  }

  const addFiles = async (fileList) => {
    const files = [...fileList].filter(isVideoFile)
    if (files.length === 0) return null
    setBusy(true); setError(null)
    let first = null
    for (const f of files) {
      const item = await uploadFile(f)
      if (item && !first) first = item
    }
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
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clips: chosen.map((c) => c.src) }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`)
      setResult(data.src)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="studio">
      <div className="slots">
        {slots.map((item, i) => (
          <div
            key={i}
            className={`slot ${item ? 'filled' : ''}`}
            onDragOver={(e) => e.preventDefault()}
            onDrop={onDrop(i)}
          >
            {item ? (
              <>
                {item.poster ? <img src={item.poster} alt="" /> : <div className="noposter" />}
                <span className="slot-label">{item.label}</span>
                <button className="slot-x" title="Remove" onClick={() => setSlot(i, null)}>×</button>
              </>
            ) : (
              <span className="slot-hint">Drop clip {i + 1}</span>
            )}
          </div>
        ))}
      </div>

      <div className="studio-actions">
        <button className="render-btn" disabled={busy || chosen.length === 0} onClick={render}>
          {busy ? 'Working…' : `Render ${chosen.length || ''} side by side`}
        </button>
        <label className="upload-btn">
          Add files
          <input
            type="file"
            accept="video/*"
            multiple
            hidden
            onChange={(e) => { addFiles(e.target.files); e.target.value = '' }}
          />
        </label>
        {error && <span className="studio-err">{error}</span>}
      </div>

      <p className="studio-tip">Drag clips below into a slot, or drop video files from your desktop onto a slot.</p>

      {result && (
        <video className="studio-result" src={result} controls autoPlay key={result} />
      )}

      <div className="palette">
        {palette.map((vid, i) => (
          <div
            className="pal-item"
            key={vid.src}
            draggable
            onDragStart={(e) => e.dataTransfer.setData('text/plain', String(i))}
            title={vid.label}
          >
            {vid.poster ? <img src={vid.poster} alt="" loading="lazy" /> : <div className="noposter" />}
            <span className="cell-label">{vid.label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function Grid({ videos, onPick }) {
  return (
    <div className="grid">
      {videos.map((vid, i) => (
        <button className="cell" key={vid.src} onClick={() => onPick(i)} title={vid.label}>
          {vid.poster ? <img src={vid.poster} alt="" loading="lazy" /> : <div className="noposter" />}
          <span className={`dot ${verdictClass(vid.passed)}`} />
          <span className="cell-play">▶</span>
          <span className="cell-label">{vid.label}</span>
        </button>
      ))}
    </div>
  )
}

export default function App() {
  const { videos, loading, error } = useVideos()
  const [filter, setFilter] = useState('all')
  const [view, setView] = useState('feed')
  const [muted, setMuted] = useState(true)
  const [fit, setFit] = useState('contain')   // 'contain' (fit) | 'cover' (fill)
  const [startIndex, setStartIndex] = useState(0)
  const [showHint, setShowHint] = useState(true)

  // 'm' toggles sound; auto-hide the shortcuts hint after a few seconds.
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'm' || e.key === 'M') setMuted((x) => !x) }
    window.addEventListener('keydown', onKey)
    const t = setTimeout(() => setShowHint(false), 4500)
    return () => { window.removeEventListener('keydown', onKey); clearTimeout(t) }
  }, [])

  const counts = {
    all: videos.length,
    pass: videos.filter((v) => v.passed === true).length,
    fail: videos.filter((v) => v.passed === false).length,
  }
  const shown = videos.filter((v) =>
    filter === 'all' ? true : filter === 'pass' ? v.passed === true : v.passed === false,
  )

  return (
    <>
      <header className="bar">
        <div className="brand"><span className="logo" />VPV<span className="brand-sub">viewer</span></div>
        <div className="filters">
          {['all', 'pass', 'fail'].map((f) => (
            <button
              key={f}
              className={`chip ${filter === f ? 'active' : ''} ${f}`}
              onClick={() => setFilter(f)}
            >
              {f}<span className="chip-n">{counts[f]}</span>
            </button>
          ))}
        </div>
        <div className="actions">
          <button className="icon" title={fit === 'contain' ? 'Fill (crop)' : 'Fit (letterbox)'}
            onClick={() => setFit((x) => (x === 'contain' ? 'cover' : 'contain'))}>
            {fit === 'contain' ? '⤢' : '▭'}
          </button>
          <button className="icon" title="Toggle view (grid/feed)"
            onClick={() => setView((v) => (v === 'grid' ? 'feed' : 'grid'))}>
            {view === 'grid' ? '▤' : '▦'}
          </button>
          <button className={`icon ${view === 'studio' ? 'active' : ''}`} title="Studio — combine clips side by side"
            onClick={() => setView((v) => (v === 'studio' ? 'feed' : 'studio'))}>
            ⊞
          </button>
          <button className="icon" title="Toggle sound (m)" onClick={() => setMuted((m) => !m)}>
            {muted ? '🔇' : '🔊'}
          </button>
        </div>
      </header>

      {loading ? (
        <div className="center"><div className="skeleton" /></div>
      ) : error ? (
        <div className="center msg">Couldn’t load clips: {error}</div>
      ) : view === 'studio' ? (
        <Studio videos={videos} />
      ) : shown.length === 0 ? (
        <div className="center msg">
          {videos.length === 0
            ? 'No clips found. Capture some with `vpv … --mode clip`, then point `vpv-view --dir` here.'
            : `No ${filter} clips.`}
        </div>
      ) : view === 'feed' ? (
        <Feed videos={shown} muted={muted} fit={fit} startIndex={startIndex} />
      ) : (
        <Grid videos={shown} onPick={(i) => { setStartIndex(i); setView('feed') }} />
      )}

      {!loading && !error && shown.length > 0 && view === 'feed' && showHint && (
        <div className="hint">↑ ↓ navigate · tap to pause · M mute</div>
      )}
    </>
  )
}
