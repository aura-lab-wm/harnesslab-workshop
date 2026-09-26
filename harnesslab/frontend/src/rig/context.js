/* ====================================================================================
   Rig · context.js — the shell's React context. Views reach the shell ONLY through useRig().

   useRig() returns (full list in /home/claude/rigspec/CONTRACT.md):
     state                      parsed route state (route.js parseHash shape)
     openTab(spec, opts?)       opts: { side: bool (open in the other pane), replace: bool
                                (replace the active tab), chain: {dir,qid}|null (evidence chain;
                                default: a run:/span: tab opened from a chained tab inherits it) }
     replaceTab(spec)           closeTab(pane, i)      focusTab(pane, i)
     setDock(id|null)           toggleDock(id)         setPalette(bool)
     maximized                  bool: rail/status bar folded, focused pane only (hash max=1)
     setMax(bool)               toggleMax()            (browser full screen follows on a gesture)
     oracle                     'visible'|'hidden'|'strengthened'       setOracle(suite)
     condition                  {dir, model, harness} of the focused tab (resolved) | null
     conditionFor(dir)          {dir, model, harness} remembered for dir, else the default
     setCondition({dir?, model?, harness?})   drives status bar, Asking… sentence and the
                                focused tab (via its view's retarget)
     lab / setLab(bool)         Student Lab (stored as 'hs.studentLab')
     theme ('dark'|'light')     themePref ('dark'|'light'|'system')    setTheme(pref)
     isMobile                   true at <= 760px
     focusCtx                   ctx() of the focused tab, condition filled in
     toast(message)             transient status line
   useTabState(spec, key, init) per-tab UI state (search boxes, filters, pages) that survives
                                switching tabs but is NOT deep-linked.
   ==================================================================================== */
import { createContext, useCallback, useContext, useState } from 'react'

export const RigContext = createContext(null)

const FALLBACK = {
  state: null, openTab: () => {}, replaceTab: () => {}, closeTab: () => {}, focusTab: () => {},
  maximized: false, setMax: () => {}, toggleMax: () => {},
  setDock: () => {}, toggleDock: () => {}, setPalette: () => {}, oracle: 'hidden', setOracle: () => {},
  condition: null, conditionFor: (dir) => ({ dir, model: null, harness: null }), setCondition: () => {},
  lab: false, setLab: () => {}, theme: 'dark', themePref: 'dark', setTheme: () => {}, isMobile: false,
  focusCtx: {}, toast: () => {},
}

export function useRig() { return useContext(RigContext) || FALLBACK }

const tabStore = new Map()
export function useTabState(spec, key, init) {
  const k = spec + '\u0000' + key
  const [v, setV] = useState(() => (tabStore.has(k) ? tabStore.get(k) : typeof init === 'function' ? init() : init))
  const set = useCallback((next) => {
    setV((prev) => {
      const val = typeof next === 'function' ? next(prev) : next
      tabStore.set(k, val)
      return val
    })
  }, [k])
  return [v, set]
}
export function resetTabState() { tabStore.clear() }
