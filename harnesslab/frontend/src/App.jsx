import { Component, useEffect } from 'react'
import RigApp from './rig/RigApp'
import { isRigHash, makeSpec } from './rig/route'

/* One UI. HarnessLab serves the Rig workbench and nothing else.

   The console that used to live at #/ is retired, but its links are not: a bookmark, a handout
   or an exported report may still carry #/s/<dir>/judge or #/run/<dir>/<id>. Each old address is
   rewritten once, in place (replaceState, so Back does not bounce), to the Rig document that
   replaced it. Anything unrecognised opens the Rig's home rather than a blank page. */

// The six method steps of the old study page, as the Rig's analysis views name them.
const STEP_VIEW = {
  family: 'family', overview: null, run: 'setup', new: 'setup', measure: 'outcomes',
  attribute: 'delta', judge: 'judge', experiment: 'fit', close: 'report',
}

const dec = (s) => { try { return decodeURIComponent(s) } catch { return s } }

/** The Rig hash that replaces a legacy console hash. Pure; never throws. */
export function legacyToRig(hash) {
  const raw = String(hash || '').replace(/^#\/?/, '')
  if (isRigHash('#/' + raw)) return '#/' + raw
  const [path, query] = raw.split('?')
  const params = new URLSearchParams(query || '')
  const [a, ...rest] = path.split('/').filter(Boolean)
  const [b, c, d] = rest.map(dec)
  const model = params.get('model'), harness = params.get('harness'), dataset = params.get('dataset')
  const rig = (spec) => '#/rig/' + spec
  switch (a) {
    case undefined: case 'home': case 'studies': case 'new':
      return rig('home')
    case 'workspace': case 'designs':
      return dataset ? rig(makeSpec('ds', dataset, model, harness)) : rig('home')
    case 's': {
      if (!b) return rig('home')
      const view = c ? STEP_VIEW[c] : null
      return view ? rig(makeSpec('an', b, view, model, harness)) : rig(makeSpec('ds', b, model, harness))
    }
    case 'field': {
      const run = params.get('run')
      return b ? rig(run ? makeSpec('run', run) : makeSpec('field', b)) : rig('home')
    }
    case 'run': case 'spans': return c ? rig(makeSpec('run', c)) : rig('home')
    case 'span': return c ? rig(makeSpec('span', c, d || '0')) : rig('home')
    case 'compare': return c ? rig(makeSpec('cmp', c, d)) : (b ? rig(makeSpec('field', b)) : rig('home'))
    case 'fork': return c ? rig(makeSpec('fork', c)) : rig('home')
    case 'load': return rig('sources')
    case 'start': return rig('guide')
    case 'canvas': case 'sentinel': case 'sources': case 'capture': case 'settings': case 'package':
      return rig(a)
    default: return rig('home')
  }
}

function normalise() {
  if (typeof location === 'undefined' || isRigHash(location.hash)) return false
  const next = legacyToRig(location.hash)
  try { history.replaceState(null, '', next) } catch { location.hash = next }
  return true
}

/* The last line of defence. Every document tab already has its own boundary; this one catches a
   crash in the shell itself (titlebar, rail, status bar, palette) so the page is never blank. It styles itself from the
   global tokens in index.css, not the Rig's stylesheet, so it draws even if that is what failed. */
const RIG_KEYS = ['rig.theme', 'rig.density', 'rig.motion']
export class AppBoundary extends Component {
  constructor(p) { super(p); this.state = { error: null } }
  static getDerivedStateFromError(error) { return { error } }
  componentDidCatch(e) { if (typeof console !== 'undefined') console.warn('[harnesslab] shell crashed:', e && e.message) }
  reset = () => {
    try { for (const k of RIG_KEYS) localStorage.removeItem(k) } catch { /* storage blocked: nothing to reset */ }
    try { history.replaceState(null, '', '#/rig/home') } catch { location.hash = '#/rig/home' }
    this.setState({ error: null })
  }
  render() {
    if (!this.state.error) return this.props.children
    const box = { position: 'fixed', inset: 0, display: 'grid', placeItems: 'center', background: 'var(--pw-night-canvas)', color: 'var(--pw-night-ink)', font: '15px/1.55 system-ui, sans-serif', padding: 24 }
    const btn = { font: 'inherit', padding: '8px 14px', borderRadius: 6, border: '1px solid var(--pw-night-rule-strong)', background: 'var(--pw-night-raised)', color: 'inherit', cursor: 'pointer', marginRight: 8 }
    return (
      <div style={box} data-el="app-crash" role="alert">
        <div style={{ maxWidth: 560 }}>
          <h1 style={{ font: '600 20px/1.3 system-ui, sans-serif', margin: '0 0 8px' }}>HarnessLab hit an error while drawing the workbench.</h1>
          <p style={{ margin: '0 0 16px', color: 'var(--pw-night-secondary)' }}>Your recorded runs are untouched: this is the page, not the data. Reset the layout to reopen the workbench on the datasets page, or reload to try the same address again.</p>
          <p style={{ margin: '0 0 20px', font: '12.5px/1.5 ui-monospace, monospace', color: 'var(--pw-night-tertiary)', overflowWrap: 'anywhere' }}>{String(this.state.error.message || this.state.error)}</p>
          <button type="button" style={{ ...btn, background: 'var(--pw-cobalt)', borderColor: 'var(--pw-cobalt)', color: 'var(--pw-paper)' }} onClick={this.reset}>Reset layout</button>
          <button type="button" style={btn} onClick={() => location.reload()}>Reload</button>
        </div>
      </div>
    )
  }
}

export default function App() {
  try { normalise() } catch { /* a hash the browser refuses to rewrite still opens the Rig */ }   // before the Rig reads the hash; idempotent
  useEffect(() => {
    // A legacy link followed while the Rig is open: rewrite it, then let the Rig hear the change.
    const on = () => { if (normalise()) window.dispatchEvent(new HashChangeEvent('hashchange')) }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return <AppBoundary><RigApp /></AppBoundary>
}
