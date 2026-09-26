/* ====================================================================================
   Rig · buildWatch.js — an open tab notices when the server has a newer UI.

   The workbench is one page: moving between views changes the hash, never the bundle. A tab left
   open across a rebuild therefore keeps running the old interface indefinitely, and "I changed it
   but nothing changed" is the result. The bundle carries the hash of the source it was built from
   (__HL_BUILD__, vite.config.js); the server serves the current one at /build-source.json. When
   they differ, the tab is stale: it offers a reload while you are looking, and reloads by itself
   the next time it is hidden (the address holds all the workbench state, so nothing is lost).
   ==================================================================================== */
import { useEffect, useState } from 'react'
import { IS_STATIC } from '../api'

/* global __HL_BUILD__ */
export const BUILD = typeof __HL_BUILD__ !== 'undefined' ? __HL_BUILD__ : null

/** True when the server is serving a UI built from different source than this tab's. */
export async function newerBuild(fetchImpl = typeof fetch !== 'undefined' ? fetch : null, mine = BUILD) {
  if (IS_STATIC || !mine || !fetchImpl) return false
  try {
    const r = await fetchImpl('/build-source.json', { cache: 'no-store' })
    if (!r || !r.ok) return false
    const j = await r.json()
    return !!(j && typeof j.sha256 === 'string' && j.sha256 !== mine)
  } catch { return false }   // server down or no stamp: not evidence of a new build
}

/** { stale } — polls every `ms`, and on focus; reloads a stale tab when it is hidden. */
export function useBuildWatch(ms = 20000) {
  const [stale, setStale] = useState(false)
  useEffect(() => {
    if (IS_STATIC || !BUILD) return undefined
    let alive = true
    const check = () => newerBuild().then((s) => { if (alive && s) setStale(true) })
    const t = setInterval(check, ms)
    const onVis = () => { if (document.visibilityState === 'visible') check() }
    window.addEventListener('focus', check)
    document.addEventListener('visibilitychange', onVis)
    return () => { alive = false; clearInterval(t); window.removeEventListener('focus', check); document.removeEventListener('visibilitychange', onVis) }
  }, [ms])
  useEffect(() => {
    if (!stale) return undefined
    const onHide = () => { if (document.visibilityState === 'hidden') location.reload() }
    document.addEventListener('visibilitychange', onHide)
    return () => document.removeEventListener('visibilitychange', onHide)
  }, [stale])
  return { stale }
}
