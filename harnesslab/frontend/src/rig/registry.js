/* ====================================================================================
   Rig · registry.js — collects every view and dock panel from ./views/*.jsx.

   CONTRACT
   --------
   A view module (src/rig/views/<family>.jsx) exports any of:

     export const views = {
       <kind>: {
         tag: 'exp',                                   // 2–5 char tab badge
         title: (args, env) => 'llma4se_live · luna',  // sync; env = { peek, dataset(dir) }
         render: Component,                            // props: { spec, args, chain, pane }
         Actions?: Component,                          // right side of the doc toolbar, same props
         crumbs?: (args, env) => [[label, spec?], …],  // breadcrumb after "workspace ›"
         ctx?: (args, env) => ({ dir, model, harness, task, run, seq }),  // what the tab is about;
                                                       // feeds status bar, Buddy, event log
         retarget?: (args, { dir, model, harness }) => spec,   // how a condition change rewrites
                                                       // this tab (status bar / Asking… sentence)
         outsideGuided?: (args) => bool,               // Student Lab banner "outside guided analysis"
         palette?: (env) => [{ title, detail, spec, keywords? }],   // command-palette entries
       },
     }
     export const docks = { <id>: { label, icon, order, render: Component } }  // dock tabs

   Precedence: modules whose file name starts with `_` (views/_pending.jsx) register first and
   are OVERRIDDEN by any other module defining the same kind or dock. So a Phase 2 view file
   that exports `views.run` replaces the placeholder without editing _pending.jsx. Two
   non-underscore modules defining the same kind is a conflict (registry.test.js fails).
   ==================================================================================== */

// Test files may live beside the views (views/home.dom.test.jsx); they are never registered.
// Explicit imports, not import.meta.glob: tests_agentlab/test_no_dead_surface.py proves every .jsx is
// reachable by following import statements, and a glob is invisible to it. A new view file is added here.
import * as m_pending from './views/_pending.jsx'
import * as m_analysis from './views/analysis.jsx'
import * as m_buddy from './views/buddy.jsx'
import * as m_casefile from './views/casefile.jsx'
import * as m_dataset from './views/dataset.jsx'
import * as m_home from './views/home.jsx'
import * as m_questions from './views/questions.jsx'
import * as m_trajectory from './views/trajectory.jsx'
import * as m_workspace from './views/workspace.jsx'
export const modules = {
  './views/_pending.jsx': m_pending,
  './views/analysis.jsx': m_analysis,
  './views/buddy.jsx': m_buddy,
  './views/casefile.jsx': m_casefile,
  './views/dataset.jsx': m_dataset,
  './views/home.jsx': m_home,
  './views/questions.jsx': m_questions,
  './views/trajectory.jsx': m_trajectory,
  './views/workspace.jsx': m_workspace,
}

export function buildRegistry(mods) {
  const views = {}, docks = {}, sources = {}, dockSources = {}, conflicts = []
  const files = Object.keys(mods).sort((a, b) => {
    const ua = /\/_[^/]*$/.test(a), ub = /\/_[^/]*$/.test(b)
    return ua === ub ? a.localeCompare(b) : ua ? -1 : 1
  })
  for (const file of files) {
    const m = mods[file] || {}
    const pending = /\/_[^/]*$/.test(file)
    for (const [kind, def] of Object.entries(m.views || {})) {
      if (!def || typeof def.render !== 'function') { conflicts.push(`${file}: views.${kind} has no render component`); continue }
      if (views[kind] && !views[kind].pending && !pending) conflicts.push(`${kind}: defined by ${sources[kind]} and ${file}`)
      views[kind] = { ...def, kind, pending, file }
      sources[kind] = file
    }
    for (const [id, def] of Object.entries(m.docks || {})) {
      if (!def || typeof def.render !== 'function') { conflicts.push(`${file}: docks.${id} has no render component`); continue }
      if (docks[id] && !docks[id].pending && !pending) conflicts.push(`dock ${id}: defined by ${dockSources[id]} and ${file}`)
      docks[id] = { order: 50, ...def, id, pending, file }
      dockSources[id] = file
    }
  }
  return { views, docks, sources, conflicts }
}

export const registry = buildRegistry(modules)
export const getView = (kind) => registry.views[kind] || null
export const listViews = () => Object.values(registry.views)
export const getDock = (id) => registry.docks[id] || null
export const listDocks = () => Object.values(registry.docks).sort((a, b) => a.order - b.order || a.id.localeCompare(b.id))
