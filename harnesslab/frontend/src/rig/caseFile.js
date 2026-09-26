/* ====================================================================================
   Rig · caseFile.js — the case file: things a reader pinned while investigating.

   CONTRACT
   --------
   Item  { id,        // stable identity; pinning the same id twice is a no-op. Use pinId():
                      //   run:<runId> · event:<runId>:<seq> · cell:<dir>:<model>:<harness>[:<task>]
                      //   · figure:<dir>:<name> · answer:<dir>:<qid>
           kind,      // 'run' | 'event' | 'cell' | 'figure' | 'answer'
           label,     // one line a human reads in the dock ("run c95aff · t01_slugify")
           spec,      // the tab spec that reopens it (may carry a ~q: chain)
           dir?, run?, seq?, model?, harness?, task?,   // whatever identifies it
           value?,    // the number/verdict shown when pinned, as a string ("95.3% · 61/64")
           note?,     // free text the reader added
           at }       // ISO time pinned (set by pin())
   API   useCaseFile() -> Item[]      (React, re-renders on change, any tab)
         getItems() · pin(item) · unpin(id) · togglePin(item) · isPinned(id) · setNote(id, text)
         clearCase() · pinId(kind, ...parts) · caseMarkdown(items, meta) · downloadText(name, text, type)
   Persisted to localStorage 'rig.case' inside try/catch: blocked storage keeps the case file
   for this visit only (storageOk() tells the UI so) and never throws.
   ==================================================================================== */
import { useSyncExternalStore } from 'react'

const KEY = 'rig.case'
const listeners = new Set()
let items = read()
let ok = true

function read() {
  try {
    const raw = globalThis.localStorage && globalThis.localStorage.getItem(KEY)
    const v = raw ? JSON.parse(raw) : []
    return Array.isArray(v) ? v.filter((x) => x && typeof x.id === 'string') : []
  } catch { return [] }
}
function write() {
  try { globalThis.localStorage.setItem(KEY, JSON.stringify(items)); ok = true } catch { ok = false }
}
function set(next) { items = next; write(); for (const l of [...listeners]) l() }

export const storageOk = () => ok
export const getItems = () => items
export const isPinned = (id) => items.some((x) => x.id === id)
export const pinId = (kind, ...parts) => [kind, ...parts.map((p) => String(p ?? ''))].join(':')

export function pin(item) {
  if (!item || !item.id || isPinned(item.id)) return
  set([...items, { ...item, at: item.at || new Date().toISOString() }])
}
export function unpin(id) { set(items.filter((x) => x.id !== id)) }
export function togglePin(item) { if (isPinned(item.id)) unpin(item.id); else pin(item) }
export function setNote(id, note) { set(items.map((x) => (x.id === id ? { ...x, note } : x))) }
export function clearCase() { set([]) }
/** Tests: reload from storage (after localStorage was changed underneath). */
export function reloadCase() { items = read(); for (const l of [...listeners]) l() }

const subscribe = (l) => { listeners.add(l); return () => listeners.delete(l) }
export function useCaseFile() { return useSyncExternalStore(subscribe, getItems, getItems) }

const KIND_TITLE = { answer: 'Answers', figure: 'Figures', cell: 'Cells', run: 'Runs', event: 'Events' }

/** The Markdown review a reader exports. Only what was pinned, verbatim; nothing inferred. */
export function caseMarkdown(list = items, meta = {}) {
  const out = ['# Case file', '']
  out.push(`Exported ${meta.at || new Date().toISOString()} from HarnessLab · Rig${meta.oracle ? ` · graded by the ${meta.oracle} suite` : ''}.`)
  out.push('Pinned evidence only: every value below is what the workbench showed when it was pinned.', '')
  if (!list.length) { out.push('_Nothing pinned._'); return out.join('\n') + '\n' }
  for (const kind of ['answer', 'figure', 'cell', 'run', 'event']) {
    const xs = list.filter((x) => x.kind === kind)
    if (!xs.length) continue
    out.push(`## ${KIND_TITLE[kind]} (${xs.length})`, '')
    for (const x of xs) {
      const bits = [x.dir && `dataset \`${x.dir}\``, x.model && `model \`${x.model}\``, x.harness && `harness \`${x.harness}\``,
        x.task && `task \`${x.task}\``, x.run && `run \`${x.run}\``, x.seq != null && x.seq !== '' && `event #${x.seq}`].filter(Boolean)
      out.push(`- **${x.label}**${x.value ? ` — ${x.value}` : ''}`)
      if (bits.length) out.push(`  - ${bits.join(' · ')}`)
      if (x.spec) out.push(`  - open: \`#/rig/${x.spec}\``)
      if (x.note) out.push(`  - note: ${x.note}`)
    }
    out.push('')
  }
  const other = list.filter((x) => !KIND_TITLE[x.kind])
  if (other.length) { out.push('## Other', ''); for (const x of other) out.push(`- **${x.label}**${x.value ? ` — ${x.value}` : ''}`); out.push('') }
  return out.join('\n')
}

/** Save text as a file via a Blob link. Returns true when the browser accepted it. */
export function downloadText(name, text, type = 'text/markdown') {
  try {
    const url = URL.createObjectURL(new Blob([text], { type }))
    const a = document.createElement('a')
    a.href = url; a.download = name
    document.body.appendChild(a); a.click(); a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    return true
  } catch { return false }
}
