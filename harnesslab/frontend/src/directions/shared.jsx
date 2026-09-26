import { createContext, useContext, useEffect, useId, useMemo, useState } from 'react'
import { api } from '../api'
import { eventDescription, takeaway } from '../studentFacts'

/* Intent: one researcher follows a measured outcome to recorded evidence.
 * Palette: graphite bench, chalk facts, teal selection; border-only depth and
 * inset raw records. Plex Sans for reading, Mono for identifiers, 4px rhythm.
 * Shared evidence is factual; the three directions own different navigation. */
export const shortModel = value => (value || 'Unrecorded model').split('/').pop()
export const verdict = value => value===true?'Passed':value===false?'Failed':'Unknown'
export const count = value => Number.isFinite(value)?value.toLocaleString('en-US'):'—'
export const rateLabel = value => value===null?'—':`${(value*100).toFixed(1)}%`
export const EvidenceSelection = createContext(null)
export function summarize(rows) {
  const known=rows.filter(r=>typeof r.hidden_pass==='boolean'),passed=known.filter(r=>r.hidden_pass).length
  const costs=rows.map(r=>r.cost_usd).filter(Number.isFinite)
  const tokens=rows.filter(r=>Number.isFinite(r.input_tokens)&&Number.isFinite(r.output_tokens)).map(r=>r.input_tokens+r.output_tokens)
  return {total:rows.length,known:known.length,passed,unknown:rows.length-known.length,rate:known.length?passed/known.length:null,cost:costs.length?costs.reduce((a,b)=>a+b,0)/costs.length:null,costCount:costs.length,tokens:tokens.length?tokens.reduce((a,b)=>a+b,0)/tokens.length:null,tokenCount:tokens.length}
}

export function useResource(path) {
  const [attempt,retry]=useState(0),[state,setState]=useState({path:null,data:null,error:null,loading:false})
  useEffect(()=>{
    if(!path)return
    let active=true
    setState({path,data:null,error:null,loading:true})
    api(path).then(data=>{if(active)setState({path,data,error:null,loading:false})}).catch(error=>{if(active)setState({path,data:null,error,loading:false})})
    return()=>{active=false}
  },[path,attempt])
  return {...(path&&state.path===path?state:{data:null,error:null,loading:!!path}),reload:()=>retry(n=>n+1)}
}

export function DatasetPicker({datasets,value,onChange}) {
  const id=useId()
  return <label className="studio-dataset-picker" htmlFor={id}><span>Dataset</span><select id={id} value={value||''} onChange={e=>onChange(e.target.value)}><option value="">Choose a dataset</option>{datasets.map(d=><option key={d.name} value={d.name}>{d.name} · {count(d.runs)} runs</option>)}</select></label>
}

export function RunQueue({runs,selected,onSelect}) {
  const [page,setPage]=useState(1)
  const shown=runs.slice(0,page*40)
  return <div className="studio-run-queue">{shown.map(r=><button key={r.run_id} className="studio-run" aria-current={selected===r.run_id?'true':undefined} onClick={()=>onSelect(r.run_id)}><span className="studio-run-top"><strong>{r.task_id||'Task not recorded'}</strong><span className={`studio-verdict ${verdict(r.hidden_pass).toLowerCase()}`}>{verdict(r.hidden_pass)}</span></span><span className="studio-run-bottom"><code>{r.run_id}</code><span>{Number.isFinite(r.steps)?`${r.steps} calls`:'Calls unknown'}</span></span></button>)}{shown.length<runs.length&&<button className="studio-button studio-more" onClick={()=>setPage(n=>n+1)}>Show more · {runs.length-shown.length} remaining</button>}{!runs.length&&<p className="studio-empty">No runs match this selection.</p>}</div>
}

export function CohortGrid({runs,scope={},onSelect,compact=false}) {
  const groups=useMemo(()=>{
    const map=new Map()
    for(const run of runs){const key=JSON.stringify([run.model,run.harness_id]);if(!map.has(key))map.set(key,[]);map.get(key).push(run)}
    return [...map.values()].map(rows=>({model:rows[0].model,harness:rows[0].harness_id,...summarize(rows)}))
  },[runs])
  return <div className={`studio-cohorts${compact?' compact':''}`}>{groups.map(g=><button key={JSON.stringify([g.model,g.harness])} disabled={!g.model||!g.harness} className="studio-cohort" aria-pressed={scope.model===g.model&&scope.harness===g.harness} aria-label={`${g.model} / ${g.harness} · ${rateLabel(g.rate)} · ${g.total} runs`} onClick={()=>onSelect(g.model,g.harness)}><span>{shortModel(g.model)}</span><strong>{g.harness||'Harness not recorded'}</strong><div><b>{rateLabel(g.rate)}</b><small>{g.passed}/{g.known} known · {g.unknown} unknown</small></div><span className="studio-rate-track" aria-hidden="true"><i style={{width:`${(g.rate||0)*100}%`}} /></span></button>)}</div>
}

function EvidenceBody({record,runId,studentMode}) {
  const [tab,setTab]=useState('Events'),[localIndex,setLocalIndex]=useState(0)
  const selection=useContext(EvidenceSelection)
  const summary=record.summary||{},spans=Array.isArray(record.spans)?record.spans.filter(Boolean):[]
  const requested=selection?.event!==null&&selection?.event!==undefined?spans.findIndex(s=>s.seq===Number(selection.event)):-1
  const eventIndex=requested>=0?requested:Math.min(localIndex,Math.max(0,spans.length-1))
  const setEventIndex=next=>{const index=typeof next==='function'?next(eventIndex):next;setLocalIndex(index);if(Number.isInteger(spans[index]?.seq))selection?.onEvent(spans[index].seq)}
  const selected=spans[eventIndex],note=takeaway(summary,spans)
  const tokens=Number.isFinite(summary.input_tokens)&&Number.isFinite(summary.output_tokens)?summary.input_tokens+summary.output_tokens:null
  const output=selected?.result_preview??selected?.text
  const text=value=>typeof value==='string'?value:JSON.stringify(value,null,2)
  return <article className="studio-evidence" aria-label="Run evidence">
    <header className="studio-evidence-heading"><div><p className="studio-kicker">RECORDED TRAJECTORY</p><h2>{summary.task_id||runId}</h2><code>{runId}</code></div><span className={`studio-verdict ${verdict(summary.hidden_pass).toLowerCase()}`}>{verdict(summary.hidden_pass)}</span></header>
    <p className="studio-evidence-family">{summary.model||'Model not recorded'}<span> / </span>{summary.harness_id||'Harness not recorded'}</p>
    <dl className="studio-evidence-facts">{[['Hidden tests',verdict(summary.hidden_pass)],['Visible tests',verdict(summary.visible_pass)],['Tokens',tokens===null?'Not recorded':count(tokens)],['Recorded cost',Number.isFinite(summary.cost_usd)?`$${summary.cost_usd.toFixed(4)}`:'Not recorded']].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    {studentMode&&<section className="studio-lesson" aria-label="Analysis guidance"><p className="studio-kicker">GUIDED ANALYSIS / READING THE EVIDENCE</p><h3>{note.title}</h3><p>{note.text}</p><details><summary>Question to investigate</summary><p>{note.question}</p><p>{note.why}</p></details></section>}
    <div className="studio-evidence-tabs" role="group" aria-label="Evidence view">{['Events','Patch','Task'].map(name=><button key={name} aria-pressed={tab===name} onClick={()=>setTab(name)}>{name}{name==='Events'&&<small>{spans.length}</small>}</button>)}</div>
    {tab==='Events'?<><div className="studio-trace-legend"><span>One segment = one recorded event</span><span>{spans.length} events</span></div><div className="studio-trace" role="group" aria-label="Recorded event sequence">{spans.map((s,i)=><button key={i} className={`studio-trace-event kind-${['chat','execute_tool','edit','grade'].includes(s.span)?s.span:'other'}`} aria-label={`Event ${i+1}: ${eventDescription(s)}`} aria-pressed={eventIndex===i} title={`${i+1} · ${s.span}`} onClick={()=>setEventIndex(i)}><span>{i+1}</span></button>)}</div>
      {selected?<section className="studio-event-detail"><header><span className="studio-kicker">EVENT {String(eventIndex+1).padStart(2,'0')}</span><span>{selected.span}</span></header><h3>{eventDescription(selected)}</h3>{selected.span==='grade'&&<p>Visible: {verdict(selected.visible)} · Hidden: {verdict(selected.hidden)}</p>}{output!=null&&output!==''?<pre>{text(output)}</pre>:!selected.tool_calls?.length&&<p className="studio-muted">No textual output recorded. Inspect the raw event for its fields.</p>}{selected.tool_calls?.length>0&&<><p className="studio-muted">Requested tool calls</p><pre>{text(selected.tool_calls)}</pre></>}<details><summary>Raw event fields</summary><pre>{JSON.stringify(selected,null,2)}</pre></details><footer><button className="studio-button" disabled={eventIndex===0} onClick={()=>setEventIndex(i=>i-1)}>← Previous event</button><button className="studio-button" disabled={eventIndex>=spans.length-1} onClick={()=>setEventIndex(i=>i+1)}>Next event →</button></footer></section>:<p className="studio-empty">No recorded events in this run.</p>}</>
      :tab==='Patch'?<div className="studio-evidence-content">{record.patch?<pre>{text(record.patch)}</pre>:<p>No patch was recorded.</p>}</div>
        :<div className="studio-evidence-content">{(record.issue??record.task)?<pre>{text(record.issue??record.task)}</pre>:<p>No task description was included in this recording.</p>}</div>}
    <footer className="studio-evidence-foot"><span>Exit: {summary.exit_reason||'not recorded'}</span><span>Recorded evidence · no prediction</span></footer>
  </article>
}
export function Evidence({dataset,runId,studentMode=false}) {
  const resource=useResource(dataset&&runId?`/results/${encodeURIComponent(dataset)}/runs/${encodeURIComponent(runId)}`:null)
  if(!runId)return <div className="studio-evidence-empty"><span className="studio-crosshair" aria-hidden="true">⌖</span><h2>Follow the evidence.</h2><p>Select a trajectory to inspect its recorded decisions, tool calls, and outcome.</p><span className="studio-kicker">NOTHING GENERATED. EVERY EVENT RECORDED.</span></div>
  if(resource.loading)return <div className="studio-empty" role="status">Reading the recorded trajectory…</div>
  if(resource.error)return <div className="studio-empty" role="alert"><h2>Recording unavailable</h2><p>{resource.error.message}</p><button className="studio-button" onClick={resource.reload}>Retry recording</button></div>
  return resource.data?<EvidenceBody key={`${dataset}/${runId}`} record={resource.data} runId={runId} studentMode={studentMode}/>:null
}
