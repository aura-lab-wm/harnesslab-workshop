/* ====================================================================================
   Rig · RigApp.jsx — the workbench shell at #/rig, HarnessLab's one UI (ported from the Rig prototype:
   directions/rig/src/00_core.js, 90_boot.js, shell.html, style.css).

   Titlebar (palette button, Buddy, split, maximize, theme) · left rail · one or two panes of document
   tabs (split with Ctrl/⌘-\) · bottom dock (Buddy / Case file) and a right-hand panel (Event log /
   Capture watcher: docks registered with `side: true`) ·
   status bar (runtime, dataset, model·harness, oracle, running jobs, Student Lab) · command
   palette (⌘K / Ctrl-K; ↑↓ ↵, ⌘↵ opens to the side) · phone layout (single tab, switcher
   sheet, full-screen palette, dock as bottom sheet) · theme scoped to the Rig root ·
   maximize (Ctrl/⌘-Shift-Enter, or double-click a tab): rail and status bar fold away, the
   focused pane takes the window, and the browser goes full screen where it allows it.

   The hash is the state (route.js). Views are looked up in the registry by kind. Views talk to
   the shell only through useRig() (context.js). Nothing here writes to the backend.
   ==================================================================================== */
import { Component, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { IS_STATIC } from '../api'
import { parseHash, toHash, parseSpec, makeSpec, withChain, specKind, isRigHash, SUITES } from './route'
import { RigContext, useRig } from './context'
import { getView, listViews, listDocks } from './registry'
import { useOverview, useJobs, useAllRuns, useCacheVersion, useConnection, normOverview, peek, findRunCached, resolveCondition, short, sfx, fmt, runHandle } from './data'
import { QUESTIONS } from './answers'
import { useCaseFile } from './caseFile'
import { useBuildWatch } from './buildWatch'
import { AN_VIEWS } from './views/analysis.jsx'
import { TaskSideNav } from './views/trajectory.jsx'
import { Icon, Kbd, MOD, NotFound, EvidenceChain, Toaster, toast as showToast, Seg, NextSteps } from './ui'

const cx = (...a) => a.filter(Boolean).join(' ')
import './rig-fonts.css'
import './rig.css'

const LAB_KEY = 'hs.studentLab'
const THEME_KEY = 'rig.theme'
const NAV_KEY = 'rig.nav'
const DOCKW_KEY = 'rig.dockW'
/* The sidebar opens with labels on a laptop or wider; the reader's own choice wins after that. */
const readNav = () => { try { const v = localStorage.getItem(NAV_KEY); if (v === 'open' || v === 'closed') return v === 'open' } catch { /* blocked */ } return typeof window === 'undefined' || window.innerWidth >= 1280 }
const writeNav = (open) => { try { localStorage.setItem(NAV_KEY, open ? 'open' : 'closed') } catch { /* this visit only */ } }
const DOCK_MIN = 320, DOCK_DEFAULT = 420
const clampDock = (w) => Math.round(Math.max(DOCK_MIN, Math.min(w, (typeof window !== 'undefined' ? window.innerWidth : 1600) * 0.6)))
const readDockW = () => { try { const n = Number(localStorage.getItem(DOCKW_KEY)); return Number.isFinite(n) && n > 0 ? n : DOCK_DEFAULT } catch { return DOCK_DEFAULT } }
const readLab = () => { try { return localStorage.getItem(LAB_KEY) === 'on' } catch { return false } }
const writeLab = (on) => { try { localStorage.setItem(LAB_KEY, on ? 'on' : 'off'); return true } catch { return false } }
const readTheme = () => { try { const t = localStorage.getItem(THEME_KEY); return ['dark', 'light', 'system'].includes(t) ? t : 'dark' } catch { return 'dark' } }
const writeTheme = (t) => { try { localStorage.setItem(THEME_KEY, t) } catch { /* blocked storage: this visit only */ } }
const mq = (q) => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(q) : null)

function useMedia(query) {
  const [on, setOn] = useState(() => !!(mq(query) && mq(query).matches))
  useEffect(() => {
    const m = mq(query); if (!m) return
    const f = () => setOn(m.matches)
    f()
    m.addEventListener ? m.addEventListener('change', f) : m.addListener(f)
    return () => (m.removeEventListener ? m.removeEventListener('change', f) : m.removeListener(f))
  }, [query])
  return on
}

/* ---------------------------------------------------------------- per-tab helpers */
const env = () => {
  const ov = peek('/overview')
  const n = ov ? normOverview(ov) : null
  return { peek, findRun: findRunCached, dataset: (dir) => (n ? n.results.find((r) => r.name === dir) || null : null), datasets: n ? n.results : [] }
}
function tabInfo(spec) {
  const { kind, args } = parseSpec(spec)
  const def = getView(kind)
  if (!def) return { title: 'not found', tag: '404', def: null, kind, args }
  let title = kind
  try { title = def.title ? def.title(args, env()) : kind } catch { title = kind }
  return { title: title || kind, tag: def.tag || kind, def, kind, args }
}
/** The chain a tab carries: explicit `~q:` suffix, or implied by a q:<dir>:<qid> spec. */
function chainOf(spec) {
  const { kind, args, chain } = parseSpec(spec)
  if (chain) return chain
  if (kind === 'q' && args[0] && args[1]) return { dir: args[0], qid: args[1] }
  return null
}
function chainStep(spec) {
  const { kind, args } = parseSpec(spec)
  if (kind === 'q') return args[2] === 'figure' ? 'figure' : args[2] === 'runs' ? 'runs' : 'answer'
  if (kind === 'run') return 'run'
  if (kind === 'span') return 'event'
  return null
}

class Boundary extends Component {
  constructor(p) { super(p); this.state = { error: null } }
  static getDerivedStateFromError(error) { return { error } }
  componentDidCatch(e) { if (typeof console !== 'undefined') console.warn('[rig] view crashed:', e && e.message) }
  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="rg-state rg-errstate" data-el="view-crash" role="alert">
        <div className="rg-empty-t"><span className="rg-v f" aria-hidden="true">×</span><b>This view failed while drawing.</b></div>
        <p className="rg-empty-why">The document hit an error while rendering, so nothing is shown in its place. Retry, or go back to the datasets; the rest of the workbench still works.</p>
        <p className="rg-errstate-msg"><span className="rg-mute">Error · </span><span className="rg-mono">{String(this.state.error.message || this.state.error)}</span> <span className="rg-mute rg-mono">({this.props.spec})</span></p>
        <NextSteps actions={[{ label: 'Retry', onClick: () => this.setState({ error: null }), primary: true }, this.props.onBack && { label: 'Back to datasets', onClick: this.props.onBack }]} />
      </div>
    )
  }
}

/* ---------------------------------------------------------------- fuzzy search (prototype) */
function fz(q, s) {
  let qi = 0, sc = 0, last = -2
  const pos = []
  for (let i = 0; i < s.length && qi < q.length; i++) {
    if (s[i] === q[qi]) {
      let b = 1
      if (i === last + 1) b += 3
      if (i === 0 || /[\s:_\-/·.↔]/.test(s[i - 1])) b += 3
      sc += b; pos.push(i); last = i; qi++
    }
  }
  return qi < q.length ? null : { sc: sc - pos[0] * 0.05 - s.length * 0.01, pos }
}
export function paletteSearch(items, query, limit = 60) {
  const q = query.trim().toLowerCase()
  if (!q) return items.filter((i) => i.pick).map((i) => ({ i, pos: [] }))
  const toks = q.split(/\s+/)
  const out = []
  for (const it of items) {
    let sc = 0, pos = [], ok = true
    for (const t of toks) {
      const m = fz(t, it.s)
      if (!m) { ok = false; break }
      sc += m.sc; pos = pos.concat(m.pos)
      if (it.t.toLowerCase().split(/[\s·]+/).includes(t)) sc += 20
    }
    if (ok) out.push({ i: it, sc: sc + (it.boost || 0), pos })
  }
  out.sort((a, b) => b.sc - a.sc)
  return out.slice(0, limit)
}
function Hl({ t, pos }) {
  const set = new Set(pos.filter((p) => p < t.length))
  return <>{[...t].map((c, i) => (set.has(i) ? <mark key={i}>{c}</mark> : c))}</>
}
const GROUPS = { go: 'Go to', question: 'Questions', dataset: 'Datasets', view: 'Views', action: 'Actions & settings', task: 'Tasks', run: 'Runs' }

/* ================================================================ the shell */
export default function RigApp() {
  const [route, setRoute] = useState(() => parseHash(typeof location !== 'undefined' ? location.hash : ''))
  const routeRef = useRef(route)
  routeRef.current = route
  const [lab, setLabState] = useState(readLab)
  const [themePref, setThemePref] = useState(readTheme)
  const [pop, setPop] = useState(null)          // { id: 'cond'|'oracle', left } | null
  const [keysOpen, setKeysOpen] = useState(false)   // the ? shortcut sheet (not deep-linked)
  const [navOpen, setNavOpenState] = useState(readNav)
  const setNavOpen = useCallback((open) => { setNavOpenState(open); writeNav(open) }, [])
  const [dockW, setDockWState] = useState(readDockW)
  const setDockW = useCallback((w) => { const v = clampDock(w); setDockWState(v); try { localStorage.setItem(DOCKW_KEY, String(v)) } catch { /* this visit only */ } }, [])
  const isMobile = useMedia('(max-width: 760px)')
  const prefersLight = useMedia('(prefers-color-scheme: light)')
  const ov = useOverview()
  const jobs = useJobs()
  const caseItems = useCaseFile()
  const cacheV = useCacheVersion()   // titles and ctx() read the cache synchronously
  const online = useConnection()
  const build = useBuildWatch()
  const wasOnline = useRef(true)
  useEffect(() => {
    if (online && !wasOnline.current) showToast('Reconnected to the local server — refreshed what had failed')
    wasOnline.current = online
  }, [online])

  /* ---- hash <-> state */
  const commit = useCallback((next, replace = false) => {
    const h = toHash(next)
    if (location.hash !== h) {
      try { history[replace ? 'replaceState' : 'pushState'](null, '', h) } catch { location.hash = h }
    }
    routeRef.current = next
    setRoute(next)
  }, [])
  useEffect(() => {
    const on = () => { if (!isRigHash(location.hash)) return; const s = parseHash(location.hash); routeRef.current = s; setRoute(s) }
    window.addEventListener('popstate', on)
    window.addEventListener('hashchange', on)
    return () => { window.removeEventListener('popstate', on); window.removeEventListener('hashchange', on) }
  }, [])
  // one-shot `lab=1|0` in a link: apply, persist, drop from the hash
  useEffect(() => {
    if (route.lab == null) return
    writeLab(route.lab); setLabState(route.lab)
    commit({ ...route, lab: null }, true)
  }, [route.lab]) // eslint-disable-line react-hooks/exhaustive-deps
  // another tab's Settings may flip Student Lab or the theme
  useEffect(() => {
    const on = (e) => { if (e.key === LAB_KEY) setLabState(readLab()); if (e.key === THEME_KEY) setThemePref(readTheme()) }
    window.addEventListener('storage', on)
    return () => window.removeEventListener('storage', on)
  }, [])

  const pref = route.theme || themePref
  const theme = pref === 'system' ? (prefersLight ? 'light' : 'dark') : pref

  /* ---- panes */
  const panes = route.panes
  const focus = Math.min(route.focus, panes.length - 1)
  const curPane = panes[focus] || panes[0]
  const curSpec = curPane.tabs[curPane.active]

  const openTab = useCallback((spec, opt = {}) => {
    const s0 = routeRef.current
    const st = { ...s0, panes: s0.panes.map((p) => ({ ...p, tabs: [...p.tabs] })), pal: false, sheet: null }
    let target = String(spec)
    const cur = st.panes[st.focus] || st.panes[0]
    const from = cur.tabs[cur.active]
    if (opt.chain !== undefined) target = withChain(target, opt.chain)
    else if (['run', 'span'].includes(specKind(target)) && !target.includes('~')) {
      const ch = chainOf(from)
      if (ch) target = withChain(target, ch)
    }
    if (opt.side && !(typeof window !== 'undefined' && window.innerWidth <= 760)) {
      if (st.panes.length < 2) st.panes.push({ tabs: [], active: 0 })
      st.focus = st.focus === 0 ? 1 : 0
    }
    const p = st.panes[st.focus]
    let i = p.tabs.indexOf(target)
    if (i < 0) {
      if (opt.replace && p.tabs.length) { p.tabs[p.active] = target; i = p.active }
      else if (!p.tabs.length) { p.tabs.push(target); i = 0 }
      else { p.tabs.splice(p.active + 1, 0, target); i = p.active + 1 }
    }
    p.active = i
    setPop(null)
    commit(st)
    ;(window.requestAnimationFrame || setTimeout)(() => { const d = document.getElementById('rig-doc-main'); if (d) d.scrollTop = 0 })
  }, [commit])
  const replaceTab = useCallback((spec) => openTab(spec, { replace: true }), [openTab])
  const closeTab = useCallback((pi, i) => {
    const s0 = routeRef.current
    const st = { ...s0, panes: s0.panes.map((p) => ({ ...p, tabs: [...p.tabs] })), sheet: null }
    const p = st.panes[pi]; if (!p) return
    p.tabs.splice(i, 1)
    if (p.active >= p.tabs.length) p.active = p.tabs.length - 1
    else if (i < p.active) p.active--
    if (!p.tabs.length) {
      if (st.panes.length > 1) { st.panes.splice(pi, 1); st.focus = 0 } else { p.tabs = ['home']; p.active = 0 }
    }
    st.focus = Math.min(st.focus, st.panes.length - 1)
    commit(st)
  }, [commit])
  const focusTab = useCallback((pi, i) => {
    const s0 = routeRef.current
    const st = { ...s0, panes: s0.panes.map((p, k) => (k === pi ? { ...p, active: i } : p)), focus: pi, sheet: null }
    commit(st)
  }, [commit])
  const toggleSplit = useCallback(() => {
    const s0 = routeRef.current
    const st = { ...s0, panes: s0.panes.map((p) => ({ ...p, tabs: [...p.tabs] })) }
    if (st.panes.length > 1) {
      const b = st.panes.pop(); const a = st.panes[0]
      for (const t of b.tabs) if (!a.tabs.includes(t)) a.tabs.push(t)
      st.focus = 0
    } else {
      const a = st.panes[0]
      st.panes.push({ tabs: [a.tabs[a.active]], active: 0 }); st.focus = 1
    }
    commit(st)
  }, [commit])
  const setDock = useCallback((id) => commit({ ...routeRef.current, dock: id || null }, true), [commit])
  const toggleDock = useCallback((id) => commit({ ...routeRef.current, dock: routeRef.current.dock === id ? null : id }, true), [commit])
  const setPalette = useCallback((on) => commit({ ...routeRef.current, pal: !!on, sheet: null }, true), [commit])
  const setSheet = useCallback((v) => commit({ ...routeRef.current, sheet: v, pal: false }, true), [commit])
  const setOracle = useCallback((o) => { if (SUITES.includes(o)) commit({ ...routeRef.current, oracle: o }) }, [commit])
  const setLab = useCallback((on) => {
    const ok = writeLab(on); setLabState(on)
    showToast(ok ? `Student Lab ${on ? 'on' : 'off'}` : `Student Lab ${on ? 'on' : 'off'} for this visit only — browser storage is unavailable`)
  }, [])
  // Maximize is in-app first (hash max=1, so it survives a reload); browser full screen is a
  // bonus taken only on a user gesture, and leaving it (the browser's own Esc) leaves maximize.
  const ownFullscreen = useRef(false)
  const setMax = useCallback((on) => {
    const s0 = routeRef.current
    if (!!s0.max === !!on) return
    commit({ ...s0, max: !!on }, true)
    try {
      if (on && document.fullscreenEnabled && !document.fullscreenElement && document.documentElement.requestFullscreen) {
        const p = document.documentElement.requestFullscreen({ navigationUI: 'hide' })
        ownFullscreen.current = true
        if (p && p.catch) p.catch(() => { ownFullscreen.current = false })
      } else if (!on && ownFullscreen.current && document.fullscreenElement && document.exitFullscreen) {
        ownFullscreen.current = false
        const p = document.exitFullscreen(); if (p && p.catch) p.catch(() => {})
      }
    } catch { ownFullscreen.current = false }
  }, [commit])
  const toggleMax = useCallback(() => setMax(!routeRef.current.max), [setMax])
  useEffect(() => {
    const on = () => { if (!document.fullscreenElement && ownFullscreen.current) { ownFullscreen.current = false; if (routeRef.current.max) commit({ ...routeRef.current, max: false }, true) } }
    document.addEventListener('fullscreenchange', on)
    return () => document.removeEventListener('fullscreenchange', on)
  }, [commit])
  const setTheme = useCallback((t) => { writeTheme(t); setThemePref(t); if (routeRef.current.theme) commit({ ...routeRef.current, theme: null }, true) }, [commit])

  /* ---- condition */
  const datasets = ov.data ? ov.data.results : []
  const rowOf = useCallback((dir) => datasets.find((r) => r.name === dir) || null, [datasets])
  const conditionFor = useCallback((dir) => {
    const row = rowOf(dir)
    const mem = route.cond && route.cond.dir === dir ? route.cond : null
    return row ? resolveCondition(row, mem && mem.model, mem && mem.harness) : { dir, model: null, harness: null }
  }, [rowOf, route.cond])
  const ctxOf = useCallback((spec) => {
    const { kind, args } = parseSpec(spec)
    const def = getView(kind)
    let raw = {}
    try { raw = (def && def.ctx && def.ctx(args, env())) || {} } catch { raw = {} }
    if (!raw.dir) return raw
    const row = rowOf(raw.dir)
    if (!row) return raw
    if (raw.model || raw.harness) { const c = resolveCondition(row, raw.model, raw.harness); return { ...raw, ...c } }
    return { ...raw, ...conditionFor(raw.dir) }
  }, [rowOf, conditionFor])
  const focusCtx = useMemo(() => ctxOf(curSpec), [ctxOf, curSpec, cacheV]) // eslint-disable-line react-hooks/exhaustive-deps
  // Event log and Capture watcher are side panels: they follow the document, so they sit beside it.
  const sideDock = !!(route.dock && (listDocks().find((d) => d.id === route.dock) || {}).side)
  const biggest = [...datasets].sort((a, b) => (a.kind === b.kind ? b.runs - a.runs : a.kind === 'recorded' ? -1 : 1))[0]
  // a tab about a dataset that is not in the library (a not-found tab) does not set the condition
  const focusDir = focusCtx.dir && (!ov.data || rowOf(focusCtx.dir)) ? focusCtx.dir : null
  const fallbackDir = focusDir || (route.cond && route.cond.dir) || (biggest && biggest.name) || null
  const condition = focusDir ? { dir: focusDir, model: focusCtx.model, harness: focusCtx.harness } : (fallbackDir ? conditionFor(fallbackDir) : null)

  const setCondition = useCallback((ch) => {
    const s0 = routeRef.current
    const p0 = s0.panes[s0.focus] || s0.panes[0]
    const spec = p0.tabs[p0.active]
    const cur = ctxOf(spec)
    const dir = ch.dir || cur.dir || fallbackDir
    const row = rowOf(dir)
    if (!row) return
    const same = dir === cur.dir
    const c = resolveCondition(row, ch.model || (same ? cur.model : null), ch.harness || (same ? cur.harness : null))
    const st = { ...s0, cond: { dir: c.dir, model: c.model, harness: c.harness }, panes: s0.panes.map((p) => ({ ...p, tabs: [...p.tabs] })) }
    const { kind, args, chain } = parseSpec(spec)
    const def = getView(kind)
    let next = null
    try { next = def && def.retarget ? def.retarget(args, c) : null } catch { next = null }
    setPop(null)
    if (next && next !== spec.split('~')[0]) {
      const pane = st.panes[st.focus]
      const target = withChain(next, chain)
      const dup = pane.tabs.indexOf(target)
      if (dup >= 0 && dup !== pane.active) { pane.tabs.splice(pane.active, 1); pane.active = pane.tabs.indexOf(target) } else pane.tabs[pane.active] = target
      commit(st)
    } else if (!next && !same && !(def && def.retarget)) {
      commit(st, true)
      openTab(makeSpec('ds', c.dir, short(c.model), c.harness))
    } else commit(st)
  }, [ctxOf, rowOf, fallbackDir, commit, openTab])

  const rig = useMemo(() => ({
    state: route, openTab, replaceTab, closeTab, focusTab, toggleSplit, maximized: !!route.max, setMax, toggleMax, setDock, toggleDock, setPalette,
    oracle: route.oracle, setOracle, condition, conditionFor, setCondition,
    lab, setLab, theme, themePref: pref, setTheme, isMobile, focusCtx, toast: showToast, datasets,
  }), [route, openTab, replaceTab, closeTab, focusTab, toggleSplit, setMax, toggleMax, setDock, toggleDock, setPalette, setOracle, condition, conditionFor, setCondition, lab, setLab, theme, pref, setTheme, isMobile, focusCtx, datasets])

  /* ---- keyboard */
  useEffect(() => {
    const onKey = (e) => {
      const mod = e.metaKey || e.ctrlKey
      const s = routeRef.current
      const k = (e.key || '').toLowerCase()
      if (mod && k === 'k') { e.preventDefault(); setPalette(!s.pal); return }
      if (s.pal) return   // the palette handles its own keys
      if (keysOpen) { if (e.key === 'Escape' || e.key === '?') { e.preventDefault(); setKeysOpen(false) } return }
      if (mod && k === 'j') { e.preventDefault(); toggleDock('buddy'); return }
      if (mod && e.key === '\\') { e.preventDefault(); toggleSplit(); return }
      if (mod && e.shiftKey && e.key === 'Enter') { e.preventDefault(); toggleMax(); return }
      if (mod && !e.shiftKey && k === 'b' && !/INPUT|TEXTAREA|SELECT/.test((document.activeElement && document.activeElement.tagName) || '')) { e.preventDefault(); setNavOpenState((o) => { writeNav(!o); return !o }); return }
      if (e.altKey && k === 'w') { e.preventDefault(); const p = s.panes[s.focus]; closeTab(s.focus, p.active); return }
      if (e.altKey && (e.key === ']' || e.key === '[')) {
        e.preventDefault(); const p = s.panes[s.focus]
        focusTab(s.focus, (p.active + (e.key === ']' ? 1 : -1) + p.tabs.length) % p.tabs.length); return
      }
      if (e.key === 'Escape') {
        if (pop) { setPop(null); return }
        if (s.sheet) { setSheet(null); return }
        if (s.dock) { setDock(null); return }
        if (s.max) setMax(false)
        return
      }
      const typing = /INPUT|TEXTAREA|SELECT/.test((document.activeElement && document.activeElement.tagName) || '')
      if (!typing && e.key === '?') { e.preventDefault(); setKeysOpen(true); return }
      if (!typing && e.key === '/') {
        const i = document.querySelector('#rig-doc-main input[type=search]')
        if (i) { e.preventDefault(); i.focus() }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setPalette, toggleDock, toggleSplit, toggleMax, setMax, closeTab, focusTab, setDock, setSheet, pop, keysOpen])

  useEffect(() => {
    const t = tabInfo(curSpec).title
    document.title = `${t} · HarnessLab`
  })

  const nTabs = panes.reduce((a, p) => a + p.tabs.length, 0)
  const curInfo = tabInfo(curSpec)
  const railDir = fallbackDir

  const rail = railItems(specKind(curSpec), railDir, lab)
  // In Analysis the sidebar carries the eight study steps, so the page needs no second nav column.
  const anSteps = specKind(curSpec) === 'an' && railDir ? analysisSteps(curSpec, railDir, condition) : null
  // On a task page the sidebar carries the task list under "Trajectories", for the same reason.
  const taskArgs = specKind(curSpec) === 'task' ? parseSpec(curSpec).args : null

  return (
    <RigContext.Provider value={rig}>
      <div className={cx('rig', sideDock && 'has-side')} data-rig-theme={theme} data-rig-mobile={isMobile ? '1' : undefined} data-rig-max={route.max && !isMobile ? '1' : undefined} data-rig-nav={navOpen ? 'open' : 'closed'} style={{ '--rig-dock-w': `${dockW}px` }}>
        <a className="rg-skip" href="#rig-doc-main" data-el="skip-link" onClick={(e) => { e.preventDefault(); document.getElementById('rig-doc-main')?.focus() }}>Skip to document</a>
        <header className="rg-tb">
          <div className="rg-tb-l">
            <button type="button" className="rg-brand" data-el="brand" aria-label="HarnessLab home" onClick={() => openTab('home')}><BrandMark /><b>HarnessLab</b></button>
          </div>
          <button type="button" className="rg-mtab" aria-label={`Switch document (${nTabs} open)`} onClick={() => setSheet(route.sheet ? null : 'tabs')}><Icon name="tabs" size={16} /><span className="tt">{curInfo.title}</span><span className="n">{nTabs}</span></button>
          <button type="button" className="rg-cmd" aria-label="Open command palette" onClick={() => setPalette(true)}><Icon name="search" size={15} /><span className="rg-cmd-ph">Search datasets, runs, tasks and views…</span><span className="rg-cmd-k"><Kbd>{MOD}</Kbd><Kbd>K</Kbd></span></button>
          <div className="rg-tb-r">
            <button type="button" className={`rg-tbtn${route.dock === 'buddy' ? ' on' : ''}`} data-el="buddy-launcher" aria-label="Ask Buddy" aria-pressed={route.dock === 'buddy'} onClick={() => toggleDock('buddy')}><Icon name="buddy" size={16} /><span className="lbl-d">Ask Buddy</span></button>
            <span className="rg-tb-sep rg-hide-sm" aria-hidden="true" />
            <button type="button" className={`rg-tbtn ico rg-hide-sm${panes.length > 1 ? ' on' : ''}`} aria-label="Toggle split view" aria-pressed={panes.length > 1} title={`Split view (${MOD}+\\)`} onClick={toggleSplit}><Icon name="split" size={16} /></button>
            <button type="button" className={`rg-tbtn ico rg-hide-sm${route.max ? ' on' : ''}`} data-el="maximize" aria-label={route.max ? 'Restore layout' : 'Maximize document'} aria-pressed={!!route.max} title={route.max ? `Restore (${MOD}+Shift+Enter or Esc)` : `Maximize (${MOD}+Shift+Enter)`} onClick={toggleMax}><Icon name={route.max ? 'unmax' : 'max'} size={16} /></button>
            <button type="button" className="rg-tbtn ico" aria-label={`Theme: ${pref}. Switch theme`} title={`Theme: ${pref}`} data-el="appearance" onClick={() => setTheme(pref === 'dark' ? 'light' : pref === 'light' ? 'system' : 'dark')}><Icon name={pref === 'system' ? 'sys' : theme === 'light' ? 'moon' : 'sun'} size={16} /></button>
          </div>
        </header>

        <nav className="rg-rail" aria-label="Activity">
          {navOpen && <DatasetSwitch condition={condition} datasets={datasets} />}
          {RAIL_GROUPS.map((g) => (
            <div key={g} className="rg-nav-g" role="group" aria-label={g}>
              <span className="rg-nav-gl" aria-hidden="true">{g}</span>
              {rail.items.filter((r) => r.group === g).map((r) => (
                <div key={r.label} className="rg-nav-node">
                  <RailButton {...r} onGo={openTab} />
                  {navOpen && r.label === 'Analysis' && anSteps && (
                    <div className="rg-nav-sub" role="group" aria-label="Analysis steps" data-el="nav-analysis-steps">
                      {anSteps.map((s) => (
                        <button key={s.view} type="button" className={cx('rg-nav-sub-it', s.on && 'on')} aria-current={s.on ? 'page' : undefined}
                          onClick={(e) => (e.metaKey || e.ctrlKey ? openTab(s.spec, { side: true }) : replaceTab(s.spec))}>
                          <span className="n">{s.n}</span><span className="lbl">{s.label}</span></button>
                      ))}
                    </div>
                  )}
                  {navOpen && r.label === 'Trajectories' && taskArgs && taskArgs[0] && <TaskSideNav args={taskArgs} />}
                </div>
              ))}
            </div>
          ))}
          <span className="sp" />
          <div className="rg-nav-g rg-nav-foot">
            {rail.bottom.map((r) => <RailButton key={r.label} {...r} onGo={openTab} />)}
            <button type="button" className="rg-nav-it rg-nav-toggle" data-el="nav-toggle" aria-label={navOpen ? 'Collapse sidebar' : 'Expand sidebar'} aria-expanded={navOpen}
              title={`${navOpen ? 'Collapse' : 'Expand'} sidebar (${MOD}+B)`} onClick={() => setNavOpen(!navOpen)}>
              <Icon name="sidebar" size={18} /><span className="lbl">Collapse</span><span className="tip">Expand sidebar</span></button>
          </div>
        </nav>

        <main className="rg-work" id="rig-work">
          {panes.map((p, pi) => <Pane key={pi} pi={pi} pane={p} focused={pi === focus} nPanes={panes.length} lab={lab} onSplit={toggleSplit} />)}
        </main>

        {route.dock && <Dock dock={route.dock} side={sideDock} setDock={setDock} ctx={focusCtx} onBack={() => openTab('home')} caseCount={caseItems.length} width={dockW} setWidth={setDockW} />}

        {/* The status bar is the shell's one quiet strip: where you are on the left, the dock
            launchers on the right (a closed dock takes no row of its own). */}
        <footer className="rg-sb" aria-label="Status">
          <span className="it" data-el="runtime-status">
            {IS_STATIC ? <><span className="rg-dot off" /><span data-el="read-only-export-state">read-only snapshot</span></>
              : ov.error || !online ? <><span className="rg-dot warn" /><span data-el="backend-down">backend unreachable · retrying</span></>
                : ov.data ? <><span className="rg-dot" />local workspace · connected</> : <><span className="rg-dot off" />connecting…</>}
          </span>
          {condition && <button type="button" className="it" title="Open this dataset" onClick={() => openTab(makeSpec('ds', condition.dir))}>ds <b>{condition.dir}</b></button>}
          {condition && condition.model && <button type="button" className="it" data-pop="cond" aria-label="Change model and harness" aria-expanded={!!pop && pop.id === 'cond'} onClick={(e) => setPop(pop && pop.id === 'cond' ? null : { id: 'cond', left: e.currentTarget.getBoundingClientRect().left })}>{short(condition.model)} · {condition.harness} ▾</button>}
          <button type="button" className="it" data-el="harness-oracle-picker" aria-label="Change test suite and harness" aria-expanded={!!pop && pop.id === 'oracle'} onClick={(e) => setPop(pop && pop.id === 'oracle' ? null : { id: 'oracle', left: e.currentTarget.getBoundingClientRect().left })}>oracle <b className={route.oracle === 'hidden' ? '' : 'alt'}>{route.oracle}</b> ▾</button>
          {!IS_STATIC && jobs.data.running > 0 && <span className="it" title={`${jobs.data.active.length} job(s) active`}><span className="rg-dot" />{jobs.data.running} {jobs.data.running === 1 ? 'run' : 'runs'} in progress</span>}
          {build.stale && <button type="button" className="it rg-sb-update" data-el="update-ready" title="HarnessLab was rebuilt on the server. Reload to use it (your tabs are kept)." onClick={() => location.reload()}>New version ready · reload</button>}
          {lab ? <button type="button" className="it lab-on" data-el="guided-indicator" onClick={() => openTab('guide')}>Guided analysis on</button>
            : <button type="button" className="it" data-el="student-lab-toggle" title="Turn on the Student Lab reading guide" onClick={() => setLab(true)}>Student Lab off</button>}
          <span className="sp" />
          <DockLaunchers dock={route.dock} toggleDock={toggleDock} caseCount={caseItems.length} />
        </footer>

        {pop && <StatusPop pop={pop} close={() => setPop(null)} condition={condition} rowOf={rowOf} />}
        {route.pal && <Palette close={() => setPalette(false)} datasets={datasets} condition={condition} onKeys={() => setKeysOpen(true)} />}
        {keysOpen && <KeySheet close={() => setKeysOpen(false)} />}
        {route.sheet === 'tabs' && <TabSheet panes={panes} focus={focus} close={() => setSheet(null)} rail={rail} />}
        <Toaster />
      </div>
    </RigContext.Provider>
  )
}

/* ---------------------------------------------------------------- rail */
const RAIL_GROUPS = ['Workspace', 'Tools']
/** The analysis steps for the focused an: tab, carrying its condition, as the in-page nav does. */
function analysisSteps(spec, dir, condition) {
  const { args } = parseSpec(spec)
  const view = args[1] || 'family'
  const cs = condition && condition.dir === dir ? [short(condition.model), condition.harness] : []
  return AN_VIEWS.map(([k, label, , n]) => ({
    view: k, label, n, on: k === view,
    spec: k === 'traj' ? makeSpec('tasks', dir, ...cs) : makeSpec('an', dir, k, ...cs),
  }))
}
function railItems(kind, dir, lab) {
  const on = (ks) => ks.includes(kind)
  const items = [
    { group: 'Workspace', spec: 'home', icon: 'db', label: 'Datasets', on: on(['home', 'ds', 'q']), el: 'nav-datasets' },
    { group: 'Workspace', spec: dir ? makeSpec('an', dir, 'family') : 'home', icon: 'an', label: 'Analysis', on: on(['an']) },
    { group: 'Workspace', spec: dir ? makeSpec('field', dir) : 'home', icon: 'traj', label: 'Trajectories', on: on(['field', 'tasks', 'task', 'run', 'span', 'cmp', 'fork']) },
    { group: 'Tools', spec: 'sources', icon: 'src', label: 'Sources & capture', on: on(['sources', 'capture']), el: 'nav-sources' },
    { group: 'Tools', spec: 'sentinel', icon: 'sent', label: 'Sentinel', on: on(['sentinel']) },
    { group: 'Tools', spec: 'canvas', icon: 'canvas', label: 'Canvas', on: on(['canvas']) },
  ]
  const bottom = []
  if (lab) bottom.push({ spec: 'guide', icon: 'guide', label: 'Reading guide', on: on(['guide', 'package']) })
  bottom.push({ spec: 'settings', icon: 'set', label: 'Settings', on: on(['settings']), el: 'nav-settings' })
  return { items, bottom }
}
function RailButton({ spec, icon, label, on, el, onGo }) {
  return (
    <button type="button" className={cx('rg-nav-it', on && 'on')} aria-label={label} aria-current={on ? 'page' : undefined} data-el={el}
      onClick={(e) => onGo(spec, { side: e.metaKey || e.ctrlKey })}><Icon name={icon} size={18} /><span className="lbl">{label}</span><span className="tip">{label}</span></button>
  )
}
/** The product mark: the harness (two uprights and a crossbar) around the model (the node). */
function BrandMark() {
  return <span className="mk" aria-hidden="true"><svg viewBox="0 0 24 24" width="14" height="14"><path d="M6 5v14M18 5v14M6 12h12" /><circle cx="12" cy="12" r="2.4" /></svg></span>
}
/** Top of the sidebar: which dataset the workbench is about, and a one-click way to change it. */
function DatasetSwitch({ condition, datasets }) {
  const rig = useRig()
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  const dir = condition && condition.dir
  const row = datasets.find((r) => r.name === dir)
  useEffect(() => {
    if (!open) return
    const off = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    const esc = (e) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false) } }
    document.addEventListener('mousedown', off); document.addEventListener('keydown', esc, true)
    return () => { document.removeEventListener('mousedown', off); document.removeEventListener('keydown', esc, true) }
  }, [open])
  if (!datasets.length) return null
  return (
    <div className="rg-dsw" ref={ref} data-el="dataset-switch">
      <button type="button" className="rg-dsw-b" aria-haspopup="listbox" aria-expanded={open} aria-label={`Dataset: ${dir || 'none'}. Switch dataset`} onClick={() => setOpen(!open)}>
        <span className="rg-dsw-ic" aria-hidden="true">{(dir || '?').slice(0, 1).toUpperCase()}</span>
        <span className="rg-dsw-t"><b>{dir || 'Choose a dataset'}</b><small>{row ? `${fmt.int(row.runs)} runs · ${row.kind === 'mock' ? 'mock control' : 'recorded'}` : 'no dataset in focus'}</small></span>
        <Icon name="updown" size={14} />
      </button>
      {open && (
        <div className="rg-dsw-m" role="listbox" aria-label="Datasets">
          {datasets.map((r) => (
            <button key={r.name} type="button" role="option" aria-selected={r.name === dir} className={cx('rg-dsw-o', r.name === dir && 'on')}
              onClick={() => { setOpen(false); rig.setCondition({ dir: r.name }) }}>
              <span className="rg-grow rg-ell">{r.name}</span><small>{fmt.int(r.runs)}</small>
            </button>
          ))}
          <button type="button" className="rg-dsw-o all" onClick={() => { setOpen(false); rig.openTab('home') }}>All datasets<Icon name="right" size={13} /></button>
        </div>
      )}
    </div>
  )
}

/* ---------------------------------------------------------------- pane */
function Pane({ pi, pane, focused, nPanes, lab, onSplit }) {
  const rig = useRig()
  const spec = pane.tabs[pane.active]
  const info = tabInfo(spec)
  const { def, args } = info
  const chain = parseSpec(spec).chain
  const effChain = chainOf(spec)
  const step = chainStep(spec)
  let crumbs = null
  try { crumbs = def && def.crumbs ? def.crumbs(args, env()) : null } catch { crumbs = null }
  if (!crumbs) crumbs = [[info.title]]
  let outside = false
  try { outside = !!(lab && def && def.outsideGuided && def.outsideGuided(args)) } catch { outside = false }
  const Render = def ? def.render : null
  const Actions = def && def.Actions
  const { kind } = parseSpec(spec)
  const runId = kind === 'run' || kind === 'span' ? args[0] : null
  const seq = kind === 'span' ? args[1] : null
  /* One header row per pane. With a single tab it carries the breadcrumb (the tab would only repeat
     the page title); from the second tab on it carries the tabs. Page actions and the split button
     sit at its right end either way, so no page spends a second 36px row on chrome. */
  const showTabs = pane.tabs.length > 1 && !rig.isMobile
  const acts = Actions ? <div className="acts"><Boundary spec={spec}><Actions spec={spec} args={args} chain={chain} pane={pi} /></Boundary></div> : null
  const splitBtn = pi === nPanes - 1 ? <button type="button" className="rg-splitbtn rg-hide-sm" aria-label={nPanes > 1 ? 'Close split' : 'Split right'} title={`${nPanes > 1 ? 'Close split' : 'Split right'} (${MOD}+\\)`} onClick={onSplit}><Icon name="split" size={15} /></button> : null
  const trail = effChain && step
    ? <EvidenceChain dir={effChain.dir} qid={effChain.qid} step={step} runId={runId} seq={seq} />
    : (
      <nav className="rg-crumb" aria-label="Breadcrumb" data-el="breadcrumb">
        <button type="button" onClick={() => rig.openTab('home')}>workspace</button>
        {crumbs.map(([label, to], i) => (
          <span key={i} className="rg-row" style={{ gap: 6 }}><span className="sep">›</span>
            {i === crumbs.length - 1 || !to ? <span className={i === crumbs.length - 1 ? 'cur' : ''}>{label}</span> : <button type="button" onClick={() => rig.openTab(to)}>{label}</button>}
          </span>
        ))}
      </nav>
    )
  return (
    <div className={`rg-pane${focused ? ' focus' : ''}`} data-pane={pi} onFocusCapture={() => { if (!focused) rig.focusTab(pi, pane.active) }}>
      {showTabs ? (
        <div className="rg-tabbar" role="tablist" aria-label={`Open documents${nPanes > 1 ? (pi ? ' (right pane)' : ' (left pane)') : ''}`}>
          {pane.tabs.map((t, i) => {
            const ti = tabInfo(t)
            return (
              <div key={t} className={`rg-tab${i === pane.active ? ' act' : ''}`}>
                <button type="button" className="tab-b" role="tab" aria-selected={i === pane.active} title={`${t} · double-click to ${rig.maximized ? 'restore' : 'maximize'}`} onClick={() => rig.focusTab(pi, i)} onDoubleClick={() => { rig.focusTab(pi, i); rig.toggleMax() }}>
                  <span className="k">{ti.tag}</span><span className="tt">{ti.title}</span>{parseSpec(t).chain && <span className="k" title="reached from a question">?</span>}
                </button>
                <button type="button" className="x" aria-label={`Close ${ti.title}`} title="Close (Alt+W)" onClick={() => rig.closeTab(pi, i)}>×</button>
              </div>
            )
          })}
          <div className="tb-end">{acts}{splitBtn}</div>
        </div>
      ) : (
        <div className="rg-doctb rg-panehead" onDoubleClick={(e) => { if (e.target === e.currentTarget) { rig.focusTab(pi, pane.active); rig.toggleMax() } }}>
          {trail}
          <div className="tb-end">{acts}{splitBtn}</div>
        </div>
      )}
      {showTabs && effChain && step && <div className="rg-doctb rg-chainrow">{trail}</div>}
      {outside && (
        <div className="rg-banner lab" data-el="outside-guided-state"><b>Outside guided analysis</b>
          <span>This view is not part of the Student Lab reading guide — prediction and intervention controls are not explained here. Nothing is hidden.</span>
          <button type="button" className="rg-btn ghost" onClick={() => rig.openTab('guide')}>Back to the guide</button></div>
      )}
      <div className="rg-doc" id={focused ? 'rig-doc-main' : undefined} tabIndex={-1} data-doc={pi} data-spec={spec}>
        <Boundary key={spec} spec={spec} onBack={() => rig.openTab('home')}>
          {Render ? <Render spec={spec} args={args} chain={effChain} pane={pi} /> : <NotFound spec={spec} />}
        </Boundary>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- dock */
/** The dock launchers live in the status bar, so a closed dock costs no row of its own. Each
 *  one toggles its panel (Ctrl/⌘-J for Buddy, Esc collapses whichever is open). */
function DockLaunchers({ dock, toggleDock, caseCount }) {
  const docks = listDocks()
  return (
    <div className="rg-sb-docks" role="group" aria-label="Dock panels">
      {docks.map((d) => {
        const n = d.id === 'case' && caseCount > 0 ? caseCount : null
        return (
          <button key={d.id} type="button" className={`it${dock === d.id ? ' on' : ''}`} aria-pressed={dock === d.id} aria-controls={dock === d.id ? 'rig-dock' : undefined}
            aria-label={`${d.label}${n ? ` (${n} pinned)` : ''}`} title={`${dock === d.id ? 'Hide' : 'Show'} ${d.label}${d.id === 'buddy' ? ` (${MOD}+J)` : ''}`}
            onClick={() => toggleDock(d.id)} data-el={d.id === 'buddy' ? undefined : `dock-${d.id}`}>
            <Icon name={d.icon || 'log'} size={14} /><span className="rg-sb-dl">{d.label}</span>{n && <span className="n">{n}</span>}
          </button>
        )
      })}
    </div>
  )
}
function Dock({ dock, side, setDock, ctx, onBack, caseCount = 0, width, setWidth }) {
  const docks = listDocks()
  const cur = docks.find((d) => d.id === dock)
  const Body = cur && cur.render
  // drag the left edge to resize; arrows do the same from the keyboard. The width is remembered.
  const startDrag = (e) => {
    if (!setWidth) return
    e.preventDefault()
    const x0 = e.clientX, w0 = width
    const move = (ev) => setWidth(w0 + (x0 - ev.clientX))
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); document.body.classList.remove('rg-resizing') }
    document.body.classList.add('rg-resizing')
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }
  return (
    <section className={cx('rg-dock open', side && 'side')} id="rig-dock" aria-label={cur ? cur.label : 'Panel'}>
      {side && setWidth && <div className="rg-dock-rs" role="separator" aria-orientation="vertical" aria-label="Resize panel" aria-valuenow={width} aria-valuemin={DOCK_MIN} tabIndex={0}
        onPointerDown={startDrag} onDoubleClick={() => setWidth(DOCK_DEFAULT)}
        onKeyDown={(e) => { if (e.key === 'ArrowLeft') { e.preventDefault(); setWidth(width + 24) } else if (e.key === 'ArrowRight') { e.preventDefault(); setWidth(width - 24) } }} />}
      <div className="rg-dock-h">
        <div className="rg-dock-tabs" role="tablist" aria-label="Panels">
          {docks.map((d) => (
            <button key={d.id} type="button" role="tab" aria-selected={d.id === dock} className={cx('rg-dock-tab', d.id === dock && 'on')} onClick={() => setDock(d.id)} title={d.label}>
              <Icon name={d.icon || 'log'} size={14} /><span className="lbl">{d.label}</span>{d.id === 'case' && caseCount > 0 && <span className="n">{caseCount}</span>}
            </button>
          ))}
        </div>
        <button type="button" className="dt" aria-label={dock === 'buddy' ? 'Hide Buddy' : 'Close panel'} title="Close (Esc)" data-el={dock === 'buddy' ? 'buddy-hide' : undefined} onClick={() => setDock(null)}><Icon name="x" size={14} /></button>
      </div>
      <div className="rg-dock-b"><Boundary key={dock} spec={`dock:${dock}`} onBack={onBack}>{Body ? <Body ctx={ctx} />
        : <div className="rg-dpad"><div className="rg-empty" data-el="empty-state"><div className="rg-empty-t"><b>No panel named “{dock}”.</b></div><p className="rg-empty-why">The link asks for a dock panel this workbench does not have.</p><NextSteps actions={listDocks().map((d) => ({ label: d.label, onClick: () => setDock(d.id) }))} /></div></div>}</Boundary></div>
    </section>
  )
}

/* ---------------------------------------------------------------- status popovers */
function StatusPop({ pop, close, condition, rowOf }) {
  const rig = useRig()
  const row = condition ? rowOf(condition.dir) : null
  const ref = useRef(null)
  useEffect(() => { const b = ref.current && ref.current.querySelector('button'); if (b) b.focus() }, [])
  const left = Math.max(8, Math.min(pop.left || 8, (typeof window !== 'undefined' ? window.innerWidth : 400) - 336))
  return (
    <>
      <div className="rg-scrim clear" onClick={close} aria-hidden="true" />
      <div className="rg-pop" role="dialog" aria-label={pop.id === 'oracle' ? 'Test suite and harness' : 'Model'} style={{ left, bottom: 34 }} ref={ref} onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); close() } }}>
        {pop.id === 'oracle' && <>
          <div className="rg-lbl" style={{ marginBottom: 8 }}>Test suite (oracle)</div>
          <Seg options={SUITES} value={rig.oracle} onChange={(o) => { rig.setOracle(o) }} label="Test suite" />
          <p className="rg-small rg-dim" style={{ marginTop: 8 }}>Grades every verdict glyph in the workbench. Unknown grades stay <span className="rg-vt u">? unknown</span>, never failure.</p>
          {row && <><div className="rg-lbl" style={{ margin: '12px 0 8px' }}>Harness · {row.name}</div>
            <div className="rg-row rg-wrap">{row.harnesses.map((h) => <button key={h} type="button" className={`rg-btn${h === condition.harness ? ' pri' : ''}`} onClick={() => rig.setCondition({ dir: row.name, harness: h })}>{h}</button>)}</div></>}
        </>}
        {pop.id === 'cond' && (row ? <>
          <div className="rg-lbl" style={{ marginBottom: 8 }}>Model · {row.name}</div>
          <div className="rg-col" style={{ gap: 4 }}>{row.models.map((m) => <button key={m} type="button" className={`rg-btn opt${m === condition.model ? ' pri' : ''}`} onClick={() => rig.setCondition({ dir: row.name, model: m })}>{m}</button>)}</div>
          <div className="rg-lbl" style={{ margin: '12px 0 8px' }}>Harness</div>
          <div className="rg-row rg-wrap">{row.harnesses.map((h) => <button key={h} type="button" className={`rg-btn${h === condition.harness ? ' pri' : ''}`} onClick={() => rig.setCondition({ dir: row.name, harness: h })}>{h}</button>)}</div>
        </> : <><p className="rg-small rg-dim">A condition is a model and a harness inside one dataset. Open a dataset to choose one.</p><button type="button" className="rg-btn" onClick={() => { close(); rig.openTab('home') }}>Datasets</button></>)}
      </div>
    </>
  )
}

/* ---------------------------------------------------------------- mobile tab sheet */
function TabSheet({ panes, focus, close, rail }) {
  const rig = useRig()
  const all = []
  panes.forEach((p, pi) => p.tabs.forEach((t, i) => all.push([pi, i, t, pi === focus && i === p.active])))
  return (
    <>
      <div className="rg-scrim" style={{ alignItems: 'flex-end', padding: 0 }} onClick={close} aria-hidden="true" />
      <div className="rg-sheet" role="dialog" aria-label="Open documents">
        <div className="grab" />
        <div className="rg-row" style={{ padding: '0 16px 8px' }}><span className="rg-lbl">Open documents · {all.length}</span><span className="rg-grow" /><button type="button" className="rg-btn ghost" aria-label="Close" onClick={close}><Icon name="x" size={16} /></button></div>
        <div className="rg-sheet-b">
          {all.map(([pi, i, t, on]) => {
            const ti = tabInfo(t)
            return (
              <div key={pi + ':' + t} className="rg-row" style={{ borderBottom: '1px solid var(--rig-line)' }}>
                <button type="button" className={`it${on ? ' on' : ''}`} style={{ border: 0 }} onClick={() => rig.focusTab(pi, i)}><span className="rg-tag">{ti.tag}</span><span className="rg-grow rg-ell rg-mono">{ti.title}</span></button>
                <button type="button" className="rg-btn ghost" aria-label={`Close ${ti.title}`} onClick={() => rig.closeTab(pi, i)}>×</button>
              </div>
            )
          })}
          <div className="rg-lbl" style={{ padding: '16px 12px 6px' }}>Go to</div>
          {[...rail.items, ...rail.bottom].map((r) => <button key={r.label} type="button" className="it" data-el={r.el} onClick={() => rig.openTab(r.spec)}><Icon name={r.icon} size={18} /><span>{r.label}</span></button>)}
          <div className="rg-lbl" style={{ padding: '16px 12px 6px' }}>Panels</div>
          {listDocks().map((d) => <button key={d.id} type="button" className="it" onClick={() => { close(); rig.setDock(d.id) }}><Icon name={d.icon || 'log'} size={18} /><span>{d.label}</span></button>)}
          <button type="button" className="it" onClick={() => rig.setPalette(true)}><Icon name="search" size={18} /><span>Command palette…</span></button>
        </div>
      </div>
    </>
  )
}

/* ---------------------------------------------------------------- keyboard shortcuts (?) */
const KEYS = [
  ['Find anything: dataset, question, run, task, view, setting', [MOD, 'K']],
  ['Open to the side from the palette or a link', [MOD, '↵ / click']],
  ['Ask Buddy', [MOD, 'J']],
  ['Split view on / off', [MOD, '\\']],
  ['Sidebar: collapse / expand', [MOD, 'B']],
  ['Maximize the document / restore', [MOD, 'Shift', 'Enter']],
  ['Next / previous tab', ['Alt', '] / [']],
  ['Close the tab', ['Alt', 'W']],
  ['Search inside the page', ['/']],
  ['Close a panel, popover or maximize', ['Esc']],
  ['This list', ['?']],
]
function KeySheet({ close }) {
  const ref = useRef(null)
  useEffect(() => { if (ref.current) ref.current.focus() }, [])
  return (
    <div className="rg-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) close() }}>
      <div className="rg-pal rg-keys" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts" ref={ref} tabIndex={-1} data-el="shortcuts">
        <div className="rg-row rg-keys-h"><b>Keyboard shortcuts</b><span className="rg-grow" /><button type="button" className="rg-btn ghost" aria-label="Close shortcuts" onClick={close}>esc</button></div>
        <dl className="rg-keys-l">
          {KEYS.map(([what, keys]) => (
            <div key={what} className="rg-row"><dt className="rg-grow">{what}</dt><dd>{keys.map((k, i) => <Kbd key={i}>{k}</Kbd>)}</dd></div>
          ))}
        </dl>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- command palette */
function usePaletteItems(datasets, condition, onKeys) {
  const rig = useRig()
  const all = useAllRuns(true)
  const tasks = useOverview().data
  return useMemo(() => {
    const L = []
    const add = (k, t, d, run, extra = '', o = {}) => L.push({ k, t, d, run, s: `${t} ${d} ${extra}`.toLowerCase(), ...o })
    const E = env()
    // views (each registered kind may contribute entries)
    for (const v of listViews()) {
      let entries = []
      try { entries = v.palette ? v.palette(E) || [] : [] } catch { entries = [] }
      for (const e of entries) add(e.group || (v.kind === 'an' ? 'view' : 'go'), e.title, e.detail || '', (side) => rig.openTab(e.spec, { side }), e.keywords || '', { pick: e.pick !== false && !e.group, spec: e.spec })
    }
    for (const r of datasets) {
      add('dataset', 'open ' + r.name, `${r.kind === 'mock' ? 'mock control' : 'recorded'} · ${fmt.int(r.runs)} runs`, (side) => rig.openTab(makeSpec('ds', r.name), { side }), r.models.join(' '), { pick: true })
      for (const q of QUESTIONS) {
        const c = r.name === (condition && condition.dir) ? condition : resolveCondition(r, null, null)
        const here = r.name === (condition && condition.dir)
        add('question', q.q(c), `${r.name} · ${q.short}`, (side) => rig.openTab(makeSpec('q', r.name, q.id), { side }), 'question ask why how ' + q.id, { pick: here, boost: here ? 8 : 0 })
      }
    }
    const dir = condition && condition.dir
    const row = datasets.find((r) => r.name === dir)
    for (const t of (tasks && tasks.tasks) || []) {
      const id = typeof t === 'string' ? t : t.id
      if (!row || !row.tasks.includes(id)) continue
      add('task', id, `${typeof t === 'object' && t.title ? t.title + ' · ' : ''}${short(condition.model)} · ${condition.harness}`, (side) => rig.openTab(makeSpec('task', dir, short(condition.model), condition.harness, id), { side }), typeof t === 'object' ? t.probe || '' : '')
    }
    const act = (t, d, fn, e = '') => add('action', t, d, () => fn(), e, { pick: true })
    act('toggle Student Lab', rig.lab ? 'currently on' : 'currently off', () => rig.setLab(!rig.lab), 'guided analysis teaching')
    act('theme: dark', 'appearance', () => rig.setTheme('dark'), 'appearance')
    act('theme: light', 'appearance', () => rig.setTheme('light'), 'appearance')
    act('theme: system', 'follow the operating system', () => rig.setTheme('system'), 'appearance')
    act('toggle split view', MOD + '+\\', () => rig.toggleSplit(), 'split side')
    act(rig.maximized ? 'restore layout' : 'maximize document', MOD + '+Shift+Enter', () => rig.toggleMax(), 'maximize full screen zen focus zoom')
    act('open Buddy', MOD + '+J', () => rig.setDock('buddy'), 'assistant ask')
    if (onKeys) act('keyboard shortcuts', '?', onKeys, 'keys help hotkeys cheatsheet')
    act('open case file', 'pinned evidence', () => rig.setDock('case'), 'pins export review')
    act('open event log', 'ledger of the focused run', () => rig.setDock('log'))
    act('open capture watcher', 'dock', () => rig.setDock('capture'))
    for (const o of SUITES) act('oracle: ' + o, `grade verdicts with the ${o} suite`, () => rig.setOracle(o), 'test suite')
    for (const r of all.data) {
      const h = runHandle(r, all.index)
      add('run', 'run ' + h, `${r.task} · ${short(r.model)} · ${r.harness} · ${r.ds} · ${r.hid === true ? '✓' : r.hid === false ? '×' : '?'}`, (side) => rig.openTab(makeSpec('run', h), { side }), `${r.id} ${sfx(r.id)}`, { boost: -2 })
    }
    return L
  }, [datasets, condition, all, tasks, rig, onKeys])
}

function Palette({ close, datasets, condition, onKeys }) {
  const items = usePaletteItems(datasets, condition, onKeys)
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const input = useRef(null)
  const listRef = useRef(null)
  const res = useMemo(() => paletteSearch(items, q), [items, q])
  const cur = Math.min(sel, Math.max(0, res.length - 1))
  useEffect(() => { input.current && input.current.focus() }, [])
  useEffect(() => { const el = listRef.current && listRef.current.querySelector('[aria-selected="true"]'); if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' }) }, [cur])
  const run = (i, side) => { const r = res[i]; if (!r) return; close(); r.i.run(side) }
  const onKey = (e) => {
    const mod = e.metaKey || e.ctrlKey
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close() }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setSel(Math.min(cur + 1, res.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSel(Math.max(cur - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); run(cur, mod) }
  }
  let group = null
  return (
    <div className="rg-scrim pal" onMouseDown={(e) => { if (e.target === e.currentTarget) close() }}>
      <div className="rg-pal" role="dialog" aria-modal="true" aria-label="Command palette" onKeyDown={onKey}>
        <div className="rg-pal-in"><Icon name="search" size={18} />
          <input ref={input} role="combobox" aria-expanded="true" aria-controls="rg-pal-list" aria-autocomplete="list" aria-label="Search datasets, questions, runs, tasks, views and settings"
            aria-activedescendant={res.length ? `rg-po-${cur}` : undefined} value={q} autoComplete="off" spellCheck="false"
            placeholder="open llma4se_live · how do runs fail · run c95aff · t03 · oracle" onChange={(e) => { setQ(e.target.value); setSel(0) }} />
          <span className="rg-pal-chip rg-hide-sm">{MOD} K</span>
          <button type="button" className="rg-btn ghost" aria-label="Close palette" onClick={close}>esc</button>
        </div>
        <div className="rg-pal-list" id="rg-pal-list" role="listbox" aria-label="Results" ref={listRef}>
          {!res.length
            ? <div className="rg-pal-empty" data-el="empty-state">Nothing matches “{q}”. Try a run suffix (c95aff), a task (t07), a model (qwen), a question (“fail”) or “oracle”.</div>
            : res.map((r, i) => {
              const head = !q.trim() && r.i.k !== group ? (group = r.i.k) : null
              return (
                <div key={i + r.i.t} role="presentation">
                  {head && <div className="rg-pal-g" role="presentation">{GROUPS[head] || head}</div>}
                  <button type="button" role="option" id={`rg-po-${i}`} aria-selected={i === cur} className={`rg-pal-it${i === cur ? ' on' : ''}`}
                    onMouseEnter={() => setSel(i)} onClick={(e) => run(i, e.metaKey || e.ctrlKey)}>
                    <span className="k">{r.i.k}</span><span className="t"><Hl t={r.i.t} pos={r.pos} /></span><span className="d">{r.i.d}</span>
                  </button>
                </div>
              )
            })}
        </div>
        <div className="rg-pal-f"><span><Kbd>↑</Kbd><Kbd>↓</Kbd> move</span><span><Kbd>↵</Kbd> open</span><span><Kbd>{MOD} ↵</Kbd> open to the side</span><span><Kbd>esc</Kbd> close</span><span className="rg-grow" /><span>{q.trim() ? `${res.length}${res.length === 60 ? '+' : ''} results` : `${fmt.int(items.length)} commands indexed`}</span></div>
      </div>
    </div>
  )
}
