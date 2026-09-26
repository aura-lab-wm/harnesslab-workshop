import { useEffect, useId, useRef, useState } from 'react'
import { Mark } from './WorkspaceUI'

// Editorial starting points, not measured HarnessLab rankings. IDs verified against
// https://openrouter.ai/api/v1/models on 2026-09-22; only live catalog matches appear.
// Intent: help investigators choose a reader for patches, runs and failure evidence.
// Retain Paper/Graphite surfaces, quiet borders, Plex type and the existing 4px grid.
// Reusable picker pattern: task-focused suggestions first, full catalog searchable
// by name, provider and use case; show an exact ID and a short rationale per candidate.
// Intersect suggestions with the live text catalog and never duplicate list entries.
// Keep the saved selection unchanged until the user chooses; never couple this
// reader selection to the experiment model or to credential storage.
const candidates = new Map([
 ['google/gemini-3.8-flash','Try for everyday trajectory explanations and run comparisons.'],
 ['deepseek/deepseek-v4.1-flash','Try for repeated code and reasoning checks.'],
 ['openai/gpt-6-sol','Try for detailed patch review and evidence synthesis.'],
 ['anthropic/claude-opus-5.5','Try for difficult failures and multi-step code review.'],
 ['anthropic/claude-fable-5.1','Try for complex code changes and concise explanations.'],
 ['openai/gpt-6-astra','Try for deep analysis of competing failure hypotheses.'],
 ['qwen/qwen3.8-flash','Try for a second opinion on code and agent decisions.'],
 ['qwen/qwen3.8-27b:free','Try for exploratory code review; free endpoint limits apply.'],
 ['openai/gpt-4.1-mini','Try for focused questions and short patch explanations.'],
])

/* Intent: choose the reader independently of the experiment. Existing paper,
 * ink and selection tokens; inset search, raised popover, Plex type, 4px grid.
 * Catalog fetched only on explicit opening. No key or evidence is sent. */
export default function BuddyModelPicker({value,onChange,disabled=false,variant='compact'}) {
 const [open,setOpen]=useState(false),[query,setQuery]=useState(''),[models,setModels]=useState(null),[error,setError]=useState(''),[attempt,setAttempt]=useState(0),[limit,setLimit]=useState(40)
 const root=useRef(null),input=useRef(null),id=useId()
 useEffect(()=>{
  if(!open)return
  input.current?.focus()
  const close=e=>{if(!root.current?.contains(e.target))setOpen(false)}
  document.addEventListener('pointerdown',close)
  return()=>document.removeEventListener('pointerdown',close)
 },[open])
 useEffect(()=>{
  if(!open||models)return
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000)
  let live=true
  fetch('https://openrouter.ai/api/v1/models',{signal:controller.signal}).then(async response=>{
   if(!response.ok)throw Error('Catalog unavailable')
   const body=await response.json()
   if(!Array.isArray(body.data))throw Error('Invalid catalog')
   const seen=new Set()
   const rows=body.data.filter(m=>typeof m?.id==='string'&&m.id.includes('/')&&!seen.has(m.id)&&seen.add(m.id)&&m.architecture?.input_modalities?.includes('text')&&m.architecture?.output_modalities?.includes('text')).map(m=>({id:m.id,name:typeof m.name==='string'?m.name:m.id})).sort((a,b)=>a.name.localeCompare(b.name))
   if(live)setModels(rows)
  }).catch(()=>{if(live)setError('Could not load the OpenRouter catalog. Retry, or enter an exact model ID.')}).finally(()=>clearTimeout(timer))
  return()=>{live=false;clearTimeout(timer);controller.abort()}
 },[open,attempt,models])
 const matches=(models||[]).filter(m=>`${m.name} ${m.id} ${candidates.get(m.id)||''}`.toLowerCase().includes(query.trim().toLowerCase()))
 const suggested=[...candidates.keys()].flatMap(key=>matches.filter(m=>m.id===key))
 const other=matches.filter(m=>!candidates.has(m.id)).slice(0,Math.max(0,limit-suggested.length))
 const selected=models?.find(m=>m.id===value),custom=query.trim(),validCustom=/^[a-zA-Z0-9._-]+\/[a-zA-Z0-9._:/-]+$/.test(custom)
 const close=()=>{setOpen(false);root.current?.querySelector('button')?.focus()}
 const choose=model=>{if(onChange(model)!==false)close()}
 const option=m=><button type="button" role="option" aria-selected={m.id===value} key={m.id} onClick={()=>choose(m.id)}><span><strong>{m.name}</strong><small>{m.id}</small>{candidates.has(m.id)&&<small className="pw-buddy-model-fit">{candidates.get(m.id)}</small>}</span>{m.id===value&&<Mark name="check" size={16}/>}</button>
 return <div className={`pw-buddy-model-picker ${variant==='field'?'pw-buddy-model-picker-field':''}`} ref={root} onKeyDown={e=>{
  if(open&&e.key==='Enter'&&e.target===input.current){e.preventDefault();return}
  if(e.key==='Escape'&&open){e.preventDefault();e.stopPropagation();close()}
  if(open&&['ArrowDown','ArrowUp','Home','End'].includes(e.key)){
   const options=[...root.current.querySelectorAll('[role=option]')]
   if(!options.length||e.target===input.current&&['Home','End'].includes(e.key))return
   e.preventDefault();const index=options.indexOf(document.activeElement)
   options[e.key==='Home'?0:e.key==='End'?options.length-1:index<0?(e.key==='ArrowDown'?0:options.length-1):(index+(e.key==='ArrowDown'?1:-1)+options.length)%options.length]?.focus()
  }
 }} onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))setOpen(false)}}>
  <button type="button" disabled={disabled} className="pw-buddy-model-trigger" aria-label={`Choose Buddy model: ${selected?.name||value}`} aria-haspopup="dialog" aria-expanded={open} onClick={()=>{setOpen(!open);setQuery('');setLimit(40);setError('')}}><span>{selected?.name||(variant==='field'?value:value.split('/').slice(1).join('/'))}</span><Mark name="chevron" size={14}/></button>
  {open&&<section className="pw-buddy-model-menu" role="dialog" aria-label="Choose Buddy model"><header><strong>Buddy’s model</strong><small>Your experiment model stays unchanged.</small></header><input ref={input} role="combobox" aria-label="Search OpenRouter models" aria-expanded="true" aria-controls={id} aria-autocomplete="list" value={query} placeholder="Search model or provider…" onChange={e=>{setQuery(e.target.value);setLimit(40)}}/>
   <div id={id} role="listbox" aria-label="OpenRouter text models">
    {!!suggested.length&&<div role="group" aria-label="Suggested for trajectory analysis"><div className="pw-buddy-model-group-title" aria-hidden="true">Suggested for trajectory analysis</div>{suggested.map(option)}</div>}
    {!!other.length&&<div role="group" aria-label="Other text models">{!!suggested.length&&<div className="pw-buddy-model-group-title" aria-hidden="true">Other text models</div>}{other.map(option)}</div>}
   </div>
   {!models&&!error&&<p role="status">Loading available models…</p>}
   {error&&<p role="alert">{error}<button type="button" onClick={()=>{setError('');setAttempt(n=>n+1)}}>Retry catalog</button></p>}
   {models&&<small className="pw-buddy-model-count">{matches.length?`${Math.min(limit,matches.length)} of ${matches.length} matching models`:'No matching text models.'}</small>}
   {matches.length>limit&&<button type="button" onClick={()=>setLimit(n=>n+40)}>Show more models</button>}
   {validCustom&&!models?.some(m=>m.id===custom)&&<button type="button" className="pw-buddy-custom-model" onClick={()=>choose(custom)}>Use model ID: {custom}<small>Not verified in the catalog</small></button>}
   <footer>{!!suggested.length&&<>Suggestions based on model descriptions, not HarnessLab evaluations. </>}OpenRouter catalog · availability and charges depend on your account. <a href="https://openrouter.ai/models" target="_blank" rel="noreferrer">Model details and pricing</a></footer>
  </section>}
 </div>
}
