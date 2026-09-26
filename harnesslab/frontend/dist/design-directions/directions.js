/* Design review only. The production application is unchanged.
 * Recorded sample: GET /api/results/live/metrics, inspected 2026-09-21.
 * Values below are observations from the local study, never illustrative data.
 * The 576-run study includes 144 sentinel runs; views marked "core" show its
 * six original harnesses (432 runs). Paired deltas aggregate all three models.
 *
 * Component intent checkpoints:
 * Notebook: a researcher explaining a result; paper/ink/cyanotype, ruled depth,
 *   paper and inset paper controls, Georgia editorial titles/Plex data, 4px grid.
 * Console: an engineer comparing cells; graphite/blue/oxide, borders only,
 *   graphite levels 0–2, Plex Sans and Mono for instruments, 4px grid.
 * Atlas: an experiment designer tracing variables; drafting paper/blueprint,
 *   borders only, neutral paper levels 0–2, Plex Sans/Mono, 4px grid.
 * Desk: an analyst reviewing evidence; archive paper/charcoal/oxide, borders only,
 *   neutral document levels 0–2, system sans/Plex Mono, 4px grid.
 * Studio: a researcher presenting a finding; chalk/carbon/oxide, borders only,
 *   chalk canvas/inset controls, Helvetica-style grotesk/Plex Mono, 4px grid.
 */
const concepts = [
  { id: 'notebook', name: 'Lab Notebook', line: 'A research instrument with the soul of a scientific journal.', fit: 'For understanding and teaching', trade: 'More reading space; less simultaneous data.' },
  { id: 'console', name: 'Instrument Console', line: 'The entire experiment, tuned into one precise instrument.', fit: 'For daily technical work', trade: 'High density; requires some familiarity with experiments.' },
  { id: 'atlas', name: 'Experiment Atlas', line: 'See the experiment as a system. Follow every connection.', fit: 'For discovery and exploration', trade: 'Memorable relationships; a table is faster for exact comparisons.' },
  { id: 'desk', name: 'Evidence Desk', line: 'Every claim beside its evidence. Everything within reach.', fit: 'For reviewing and investigating', trade: 'Excellent continuity; needs room for multiple panes.' },
  { id: 'studio', name: 'Signal Studio', line: 'A bold, legible point of view on what the experiment found.', fit: 'For first impressions and presenting', trade: 'Strong narrative focus; detailed controls live one level deeper.' },
]
const harnesses = ['baseline', 'no_test_tool', 'permissive', 'short_context', 'terse_prompt', 'tight_budget']
const labels = { baseline: 'Baseline', no_test_tool: 'No test tool', permissive: 'Permissive', short_context: 'Short context', terse_prompt: 'Terse prompt', tight_budget: 'Tight budget' }
const models = [
  { name: 'Claude Haiku 4.5', provider: 'Anthropic', short: 'Haiku 4.5', code: 'anthropic/claude-haiku-4.5', rates: [1, 1, 1, 2/3, 1, 1], ci: [[1,1],[1,1],[1,1],[.375,11/12],[1,1],[1,1]], costs: [.0288573,.0205324,.0293253,.0497428,.0262246,.0174263] },
  { name: 'Gemini 2.5 Flash', provider: 'Google', short: 'Gemini 2.5', code: 'google/gemini-2.5-flash', rates: [.75, 1, .75, 7/12, .875, .625], ci: [[.375,1],[1,1],[.375,1],[.25,.875],[.625,1],[.25,.875]], costs: [.0054299,.0027546,.0049291,.0048079,.0034015,.0032171] },
  { name: 'GPT-5 mini', provider: 'OpenAI', short: 'GPT-5 mini', code: 'openai/gpt-5-mini', rates: [1, 1, 23/24, 19/24, 1, 11/12], ci: [[1,1],[1,1],[.875,1],[13/24,23/24],[1,1],[.75,1]], costs: [.0037172,.0049879,.0037184,.0064697,.0040185,.0028984] },
]
const paired = [0, 100/12, -100/72, -1700/72, 100/24, -500/72]
const pairCI = [[0,0],[0,20.8333],[-4.1667,0],[-40.2778,-8.3333],[-8.3333,16.6667],[-16.6667,0]]
const taskDelta = [0,-1/3,0,-2/9,0,-2/3,-2/9,-4/9]
const tasks = ['Slugify', 'Intervals', 'Rate limit', 'CSV split', 'Duration', 'Injected config', 'Cache cleanup', 'Ambiguous handler']
const state = { model: 1, harness: 3 }
const pct = n => (n * 100).toFixed(1) + '%'
const pp = n => (n > 0 ? '+' : n < 0 ? '−' : '') + Math.abs(n).toFixed(1)
const arrow = '<span aria-hidden="true">↗</span>'
const icon = '<svg viewBox="0 0 28 28" fill="none" aria-hidden="true"><path d="M5 5v18M23 5v18M5 14h18M10 5v5M18 18v5" stroke="currentColor" stroke-width="2"/><circle cx="14" cy="14" r="3" fill="currentColor"/></svg>'
const route = (step='attribute') => `../../#/s/live/${step}`
const appLink = (label, step='attribute', cls='text-link') => `<a class="${cls}" href="${route(step)}">${label} ${arrow}</a>`
function logo(extra='') { return `<a class="logo ${extra}" href="../../#/">${icon}<span>harnesslab<span class="logo-period">.</span></span></a>` }
function modelPicker() { return `<div class="model-picker" role="group" aria-label="Select model">${models.map((m,i)=>`<button data-model="${i}" aria-pressed="${state.model===i}">${m.short}</button>`).join('')}</div>` }
function plotRows({ compact=false }={}) {
  const m = models[state.model]
  return `<div class="interval-plot ${compact?'compact':''}">
    <div class="plot-axis"><span>Harness</span><div><span>0</span><span>25</span><span>50</span><span>75</span><span>100%</span></div><span>Pass rate</span></div>
    ${harnesses.map((h,i)=>`<button class="plot-row ${i===state.harness?'selected':''}" data-harness="${i}" aria-pressed="${i===state.harness}"><span>${labels[h]}${i===0?'<small>REFERENCE</small>':''}</span><span class="plot-track"><span class="whisker" style="left:${m.ci[i][0]*100}%;width:${(m.ci[i][1]-m.ci[i][0])*100}%"></span><span class="plot-point" style="left:${m.rates[i]*100}%"></span></span><strong>${pct(m.rates[i])}</strong></button>`).join('')}
    <div class="plot-legend"><span><i class="legend-dot"></i>Observed pass rate</span><span><i class="legend-line"></i>95% task-bootstrap interval</span><span>24 runs / cell</span></div>
  </div>`
}
function claimText() {
  if (state.harness===0) return 'The reference condition for this experiment. Each comparison changes the harness while holding the models and tasks fixed.'
  const clear = pairCI[state.harness][0] > 0 || pairCI[state.harness][1] < 0
  return `${labels[harnesses[state.harness]]} ${paired[state.harness]<0?'reduced':'increased'} the observed pass rate by ${Math.abs(paired[state.harness]).toFixed(1)} points across the three models. ${clear?'The paired interval excludes zero.':'The paired interval includes zero; the direction is uncertain.'}`
}
function evidenceFacts() {
  const i = state.harness, m = models[state.model]
  return `<dl class="evidence-facts"><div><dt>Selected model</dt><dd>${m.name}</dd></div><div><dt>Harness</dt><dd>${labels[harnesses[i]]}</dd></div><div><dt>Observed pass rate</dt><dd>${pct(m.rates[i])}</dd></div><div><dt>95% interval</dt><dd>${pct(m.ci[i][0])} – ${pct(m.ci[i][1])}</dd></div><div><dt>Mean cost / run</dt><dd>$${m.costs[i].toFixed(4)}</dd></div><div><dt>Sample</dt><dd>8 tasks × 3 repeats</dd></div></dl>`
}
function matrix() {
  return `<div class="matrix-wrap"><table class="matrix"><thead><tr><th>HARNESS / MODEL</th>${models.map((m,i)=>`<th><button data-model="${i}" aria-pressed="${state.model===i}"><span>${m.provider}</span>${m.short}</button></th>`).join('')}<th>PAIRED Δ</th></tr></thead><tbody>${harnesses.map((h,i)=>`<tr class="${state.harness===i?'selected':''}"><th><button data-harness="${i}" aria-pressed="${state.harness===i}"><span class="row-index">0${i+1}</span>${labels[h]}${i===0?'<small>REF</small>':''}</button></th>${models.map((m,j)=>`<td><button class="matrix-cell ${state.harness===i&&state.model===j?'cell-selected':''}" data-cell="${j},${i}" style="--intensity:${.025+m.rates[i]*.12}" aria-label="${m.name}, ${labels[h]}, ${pct(m.rates[i])}" aria-pressed="${state.harness===i&&state.model===j}"><strong>${pct(m.rates[i])}</strong><span class="tiny-track"><i style="width:${m.rates[i]*100}%"></i></span></button></td>`).join('')}<td class="delta ${i===3?'attention':''}">${i===0?'—':pp(paired[i])+'<small> pp</small>'}</td></tr>`).join('')}</tbody></table></div>`
}
function notebook() {
  return `<section class="prototype notebook"><header class="product-top">${logo()}<nav aria-label="Notebook navigation"><span class="active">Research notebook</span><a href="../../#/studies">All studies</a><a href="../../#/start">Field guide</a></nav><span class="mono muted">SCHOOL COLLECTION <span class="small-dot"></span></span></header>
    <div class="notebook-body"><aside class="notebook-index"><div class="eyebrow">THE NOTEBOOK</div><span class="notebook-volume">Vol. 01</span><div class="index-rule"></div><a class="index-active" href="${route('overview')}"><small>01 / CURRENT STUDY</small>Three model families</a><a href="../../#/s/llma4se_live"><small>02 / STUDY</small>Five-model replication</a><a href="../../#/s/real_swe_agent_500"><small>03 / COLLECTION</small>In the wild</a><div class="notebook-index-bottom"><span class="stamp">REPRODUCIBLE<br>BY DESIGN</span><p>Every number has a record.<br>Every claim has a limit.</p></div></aside>
    <article class="notebook-paper"><div class="paper-kicker"><span class="eyebrow">RESEARCH NOTE / 001</span><span class="mono muted">live · 576 recorded runs</span></div><h1>The harness changes<br><em>the answer.</em></h1><p class="notebook-lede">Same models. Same tasks. Change the apparatus around the agent, and the result moves with it.</p>
    <div class="notebook-summary"><div><span class="eyebrow">A PAIRED OBSERVATION</span><strong>−23.6<span>pp</span></strong></div><p>Shortening the context reduced the pass rate across three model families.<br><span class="muted">95% paired interval: −40.3 to −8.3 points.</span></p>${appLink('Read the comparison')}</div>
    <div class="section-line"><div><span class="figure-label">FIG. 01</span><h2>One model. Six conditions.</h2></div>${modelPicker()}</div>${plotRows()}
    <div class="paper-bottom"><div><span class="eyebrow">METHOD</span><p>Eight tasks, three repeats per cell. Hidden-test oracle.<br>Intervals describe uncertainty across tasks.</p></div>${appLink('Inspect the recorded runs','attribute')}</div></article>
    <aside class="margin-notes"><div class="eyebrow">IN THE MARGIN</div><span class="note-number">01</span><h3>What changed?</h3><p>The harness: tools, permissions, context, and stopping rules. The model is only one part of the system.</p><div class="margin-rule"></div><span class="note-number">02</span><h3>Read the interval.</h3><p>A point estimate is a start. The line around it tells you how much the task set leaves uncertain.</p><div class="margin-rule"></div><span class="note-number">03 / SELECTED CONDITION</span><h3>${labels[harnesses[state.harness]]}</h3><p>${claimText()}</p>${appLink('Follow the evidence')}</aside></div></section>`
}
function consoleView() {
  return `<section class="prototype console"><header class="product-top">${logo()}<nav aria-label="Console navigation"><span class="active">Overview</span><a href="../../#/studies">Experiments</a><a href="../../#/field/live">Runs</a><a href="../../#/sentinel">Sentinel</a></nav><span class="recorded"><span class="small-dot"></span> RECORDED DATA</span><a class="quiet-button" href="../../#/start">Quick start ${arrow}</a></header>
    <div class="console-body"><div class="console-heading"><div><div class="eyebrow">WORKSPACE / SCHOOL PACKAGE / LIVE</div><h1>Experiment control.</h1><p>Three model families. Eight harnesses. Every run accounted for.</p></div><div class="console-heading-right"><span class="eyebrow">EXPERIMENT 003</span>${appLink('Open study','overview','solid-button')}</div></div>
    <div class="instrument-strip"><div><span class="eyebrow">RECORDED RUNS</span><strong>576<span> / complete</span></strong></div><div><span class="eyebrow">EXPERIMENT DESIGN</span><strong>3 <small>×</small> 8 <small>×</small> 8<span>models · harnesses · tasks</span></strong></div><div><span class="eyebrow">ORACLE</span><strong class="text-stat">Hidden tests<span>held out from the agent</span></strong></div><div class="attention"><span class="eyebrow">PAIRED CONTEXT EFFECT</span><strong>−23.6<span>percentage points</span></strong></div></div>
    <div class="console-grid"><section class="matrix-panel"><div class="panel-title"><div><span class="eyebrow">01 / OUTCOME MATRIX</span><h2>The harness is a variable.</h2></div><span class="tag">6 core harnesses · 432 runs</span></div>${matrix()}<div class="matrix-bottom"><span><i class="legend-dot"></i> Hidden-test pass rate · 24 runs per cell</span><span>Click any cell to inspect →</span></div></section>
    <aside class="console-inspector"><div class="eyebrow">02 / CELL INSPECTOR</div><div class="inspector-title"><h2>${models[state.model].name}</h2><span>${labels[harnesses[state.harness]]}</span></div><div class="inspector-number">${pct(models[state.model].rates[state.harness])}<small>observed pass rate</small></div>${evidenceFacts()}${appLink('Open trajectories','attribute','solid-button')}</aside></div>
    <div class="console-bottom"><div><span class="eyebrow">03 / THE FINDING</span><h3>Context is not free.</h3></div><p>The short-context condition scored lower for all three families. Its paired interval excludes zero; the underlying task differences still matter.</p>${appLink('Examine paired differences')}</div><div class="status-line"><span>LEDGER → METRICS → EVIDENCE</span><span>Data on disk · no model calls needed</span><span>LOCAL / READ ONLY</span></div></div></section>`
}
function atlas() {
  const m=models[state.model]
  return `<section class="prototype atlas"><header class="atlas-top">${logo()}<nav aria-label="Atlas navigation"><span class="active">Experiment map</span><a href="../../#/studies">Study library</a><a href="../../#/start">Guide</a></nav><span class="tag">School collection</span></header>
    <div class="atlas-heading"><div class="eyebrow">A MAP OF THE EXPERIMENT</div><h1>Follow the variables.</h1><p>Select a model. Trace its harnesses. Open the evidence.</p></div><div class="atlas-layout"><div class="map-scroll"><div class="map-stage">
    <svg class="map-wires" viewBox="0 0 940 510" preserveAspectRatio="none" aria-hidden="true"><path d="M470 106 V134 H160 V158 M470 134 V158 M470 134 H780 V158"/><path class="wire-active" d="M470 106 V134 H${160+state.model*310} V158"/><path d="M${160+state.model*310} 254 V302 H85 V350 M${160+state.model*310} 302 H239 V350 M${160+state.model*310} 302 H393 V350 M${160+state.model*310} 302 H547 V350 M${160+state.model*310} 302 H701 V350 M${160+state.model*310} 302 H855 V350"/><path class="wire-active" d="M${160+state.model*310} 254 V302 H${85+state.harness*154} V350"/></svg>
    <div class="map-layer-label l1">01 / STUDY</div><div class="map-root"><span class="eyebrow">CONTROLLED EXPERIMENT</span><strong>Three model families</strong><span>576 runs · 8 tasks · hidden oracle</span></div>
    <div class="map-layer-label l2">02 / HOLD THE MODEL FIXED</div><div class="map-models">${models.map((x,i)=>`<button class="model-node ${state.model===i?'chosen':''}" data-model="${i}" aria-pressed="${state.model===i}"><span class="node-provider">${x.provider}<span>0${i+1}</span></span><strong>${x.name}</strong><small>6 core conditions · 144 runs</small><i class="node-port"></i></button>`).join('')}</div>
    <div class="map-layer-label l3">03 / CHANGE THE HARNESS</div><div class="map-cells">${harnesses.map((h,i)=>`<button class="cell-node ${i===state.harness?'chosen':''}" data-harness="${i}" aria-pressed="${i===state.harness}"><span>${labels[h]}</span><strong>${pct(m.rates[i])}</strong><span class="node-track"><i style="width:${m.rates[i]*100}%"></i></span><small>24 runs</small></button>`).join('')}</div></div></div>
    <aside class="atlas-detail"><div class="eyebrow">SELECTED BRANCH</div><div class="branch-breadcrumb">live <span>→</span> ${m.short}</div><h2>${labels[harnesses[state.harness]]}</h2><div class="atlas-rate">${pct(m.rates[state.harness])}</div><span class="muted">Hidden-test pass rate</span><div class="atlas-ci"><span>95% confidence interval</span><strong>${pct(m.ci[state.harness][0])} – ${pct(m.ci[state.harness][1])}</strong></div><p>One model, one harness, eight tasks repeated three times. Every branch leads back to its recorded runs.</p>${appLink('Inspect this study','attribute','solid-button')}${appLink('Open experiment design','experiment')}</aside></div><div class="atlas-bottom"><span><i class="legend-dot"></i> Selected path</span><span><i class="legend-line"></i> Experimental relationship</span><p>Click a model or harness to follow another path.</p><a href="../../#/canvas">Open existing canvas ${arrow}</a></div></section>`
}
function desk() {
  return `<section class="prototype desk"><header class="product-top">${logo()}<div class="desk-breadcrumb">School package <span>/</span> live <span>/</span> Harness comparison</div><a href="../../#/studies" class="quiet-button">Study library ${arrow}</a></header>
    <div class="desk-layout"><aside class="desk-sidebar"><div class="eyebrow">COLLECTION</div><h2>Your evidence.</h2><label class="desk-search"><span aria-hidden="true">⌕</span><input type="search" aria-label="Find a study" placeholder="Find a study…" id="study-search" /></label><div class="desk-study-list"><a class="desk-study current" href="${route('overview')}"><span class="file-icon">▤</span><span><strong>Three model families</strong><small>live · 576 runs</small></span><span class="study-dot"></span></a><a class="desk-study" href="../../#/s/llma4se_live"><span class="file-icon">▤</span><span><strong>Five-model replication</strong><small>llma4se_live · 1,632 runs</small></span></a><a class="desk-study" href="../../#/s/real_swe_agent_500"><span class="file-icon">▤</span><span><strong>Agent trajectories</strong><small>real_swe_agent_500 · 500 runs</small></span></a><p class="search-empty" hidden>No matching studies.</p></div><div class="desk-outline"><div class="eyebrow">IN THIS STUDY</div><a href="${route('overview')}"><span>01</span>Overview</a><a href="${route('measure')}"><span>02</span>Outcomes</a><span class="outline-active"><span>03</span>Harness comparison</span><a href="${route('judge')}"><span>04</span>Judge & integrity</a><a href="${route('experiment')}"><span>05</span>Experiment</a><a href="${route('close')}"><span>06</span>Report card</a></div><div class="desk-sidebar-foot">School replication package<br><span>Recorded evidence. Ready to explore.</span></div></aside>
    <article class="desk-document"><div class="document-top"><span class="eyebrow">03 / HARNESS COMPARISON</span><span class="tag">Hidden oracle</span></div><h1>What did the<br>harness change?</h1><p class="desk-lede">Hold the model fixed. Compare the conditions.<br>Then ask whether the difference holds up.</p><div class="document-tabs"><span class="active">Paired comparison</span><a href="../../#/field/live">Trajectories ${arrow}</a><a href="${route('experiment')}">Factorial analysis ${arrow}</a></div>
    <div class="desk-plot-heading"><h2>Pass rate by condition</h2>${modelPicker()}</div>${plotRows({compact:true})}<section class="desk-finding"><div class="eyebrow">OBSERVATION / ALL THREE MODELS</div><h3>${state.harness===0?'Start from the reference.':labels[harnesses[state.harness]]+' vs. baseline.'}</h3><p>${claimText()}</p></section><div class="document-foot"><span>Source: live / ledger → task-paired bootstrap</span>${appLink('Open full analysis')}</div></article>
    <aside class="desk-evidence"><div class="eyebrow">EVIDENCE INSPECTOR</div><div class="evidence-index">E<span>0${state.harness+1}</span></div><h2>${labels[harnesses[state.harness]]}</h2><p>Paired change vs. baseline<br>Across all three model families</p><div class="desk-delta">${state.harness===0?'—':pp(paired[state.harness])}<span> pp</span></div><div class="evidence-interval"><span>95% paired interval</span><strong>${pp(pairCI[state.harness][0])} to ${pp(pairCI[state.harness][1])} pp</strong></div><div class="evidence-verdict">${state.harness===0?'Reference condition':state.harness===3?'Interval excludes zero':'Interval includes zero'}</div><hr/><div class="eyebrow">THE CELL YOU SELECTED</div>${evidenceFacts()}${appLink('Review the report','close','quiet-button')}</aside></div></section>`
}
function slopeChart() {
  return `<div class="slope-chart"><div class="slope-heading"><span>Baseline</span><span>Short context</span></div><svg viewBox="0 0 660 310" role="img" aria-label="Hidden-test pass rates from baseline to short context: Haiku 100 to 66.7 percent, Gemini 75 to 58.3 percent, GPT-5 mini 100 to 79.2 percent."><g class="slope-grid">${[0,25,50,75,100].map(n=>`<path d="M96 ${260-n*2.1} H562"/><text x="62" y="${264-n*2.1}">${n}</text>`).join('')}</g>${[0,1,2].filter(i=>i!==state.model).concat(state.model).map(i=>{const m=models[i],y1=260-m.rates[0]*210,y2=260-m.rates[3]*210;return `<g class="slope-series ${i===state.model?'selected':''}"><path d="M110 ${y1} L540 ${y2}"/><circle cx="110" cy="${y1}" r="5"/><circle cx="540" cy="${y2}" r="5"/><text x="555" y="${y2+4}">${pct(m.rates[3])}</text></g>`}).join('')}</svg><div class="slope-foot">${modelPicker()}<span>Observed rates · 24 runs / cell</span></div></div>`
}
function studio() {
  return `<section class="prototype studio"><header class="studio-top">${logo()}<nav aria-label="Studio navigation"><span class="active">Discover</span><a href="../../#/studies">Studies</a><a href="../../#/field/live">Trajectories</a><a href="../../#/start">Learn</a></nav><a class="quiet-button" href="${route('overview')}">Open the lab ${arrow}</a></header>
    <div class="studio-body"><div class="studio-kicker"><span class="eyebrow">THE AGENT IS MORE THAN THE MODEL.</span><span class="mono">STUDY 003 / LIVE</span></div><div class="studio-hero"><h1>Same model.<br><span>Different outcome.</span></h1><div class="studio-intro"><span class="studio-asterisk" aria-hidden="true">✳</span><p>Tools. Context. Permissions.<br>The harness changes what your agent can do. Here’s the evidence.</p></div></div>
    <div class="studio-rule"><span>01 / THE CONTEXT EXPERIMENT</span><span>3 MODEL FAMILIES · 8 TASKS · 3 REPEATS</span></div><div class="studio-result"><div class="studio-measure"><span class="eyebrow">SHORT CONTEXT VS. BASELINE</span><div class="big-delta">−23.6<span>pp</span></div><h2>A smaller context. <br>A measurable difference.</h2><p>The paired pass-rate change across three model families. The 95% interval is −40.3 to −8.3 points.</p>${appLink('Explore the comparison','attribute','solid-button')}</div>${slopeChart()}</div>
    <div class="studio-task-head"><h2>Where does the difference come from?</h2><span class="muted">Paired change by task, across the three models</span></div><div class="task-strip">${tasks.map((t,i)=>`<a href="${route('attribute')}" class="task-tile ${taskDelta[i]===0?'unchanged':''}"><span>0${i+1} / ${t}</span><strong>${pp(taskDelta[i]*100)}<small> pp</small></strong><span class="task-track"><i style="width:${Math.abs(taskDelta[i])*100}%"></i></span></a>`).join('')}</div><div class="studio-bottom"><span>Understand the apparatus.<br><strong>Then trust the measurement.</strong></span><a href="../../#/start">A field guide to HarnessLab ${arrow}</a><span class="mono">576 RECORDED RUNS<br>ONE INSPECTABLE LEDGER</span></div></div></section>`
}
const renderers = { notebook, console: consoleView, atlas, desk, studio }
function activeConcept() { return concepts.find(c=>c.id===location.hash.slice(1)) || concepts[0] }
function revealActiveDirection() {
  requestAnimationFrame(() => {
    const nav=document.querySelector('#directions'), current=nav.querySelector('[aria-current]')
    if (window.innerWidth <= 640 && current) nav.scrollLeft += current.getBoundingClientRect().right-nav.getBoundingClientRect().right
  })
}
function render(keepFocus=false) {
  const c=activeConcept(), index=concepts.indexOf(c)
  const active=document.activeElement
  const focusKey=keepFocus&&active?.dataset ? ['model','harness','cell'].find(k=>active.dataset[k]!==undefined) : null
  const focusValue=focusKey?active.dataset[focusKey]:null
  document.body.dataset.direction=c.id
  document.title=`${c.name} — HarnessLab design directions`
  document.querySelector('#directions').innerHTML=concepts.map((x,i)=>`<a href="#${x.id}" ${x.id===c.id?'aria-current="page"':''}><span>0${i+1}</span>${x.name}</a>`).join('')
  document.querySelector('#concept').innerHTML=renderers[c.id]()
  document.querySelector('#review-footer').innerHTML=`<div><span class="review-number">0${index+1}</span><div><strong>${c.name}</strong><p>${c.line}</p></div></div><div class="review-trade"><strong>${c.fit}</strong><p>${c.trade}</p></div><span class="prototype-note">Interactive direction preview<br>Recorded data · not the production app</span>`
  if (!keepFocus) revealActiveDirection()
  if (focusKey) document.querySelector(`[data-${focusKey}="${focusValue}"]`)?.focus({preventScroll:true})
}
document.addEventListener('click',e=>{
  const target=e.target.closest('button[data-model],button[data-harness],button[data-cell]')
  if(!target)return
  if(target.dataset.model!==undefined)state.model=Number(target.dataset.model)
  if(target.dataset.harness!==undefined)state.harness=Number(target.dataset.harness)
  if(target.dataset.cell!==undefined)[state.model,state.harness]=target.dataset.cell.split(',').map(Number)
  render(true)
})
document.addEventListener('input',e=>{
  if(e.target.id!=='study-search')return
  const query=e.target.value.trim().toLowerCase()
  const rows=[...document.querySelectorAll('.desk-study')]
  rows.forEach(row=>{row.hidden=!row.textContent.toLowerCase().includes(query)})
  document.querySelector('.search-empty').hidden=rows.some(row=>!row.hidden)
})
window.addEventListener('hashchange',()=>{render();window.scrollTo(0,0)})
window.addEventListener('resize',revealActiveDirection)
render()
document.fonts.ready.then(revealActiveDirection)
