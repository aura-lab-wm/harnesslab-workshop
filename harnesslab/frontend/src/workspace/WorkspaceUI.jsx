import { useEffect, useId, useRef, useState } from 'react'
import { count, verdict } from '../directions/shared'
import { text, tokenValue } from './workspaceFacts'

/* Intent: a researcher navigates a recorded experiment without losing scope.
 * Ivory paper, cobalt selection, ink text, teal/rust outcomes. Small shadows
 * lift controls; Plex Sans/Mono distinguish prose from identifiers. 4px rhythm. */
export function Mark({name='grid',size=18}) {
 const paths={grid:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',trace:'M3 12h4l3-7 4 14 3-7h4',layers:'m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5',arrow:'M5 12h14m-6-6 6 6-6 6',search:'m16 16 5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',chevron:'m8 10 4 4 4-4',note:'M5 3h14v18H5zM8 8h8M8 12h8M8 16h5',settings:'M4 6h4m4 0h8M4 12h10m4 0h2M4 18h2m4 0h10M8 3v6m6 0v6M6 15v6',check:'m5 12 4 4L19 6',compare:'M8 3v18M16 3v18M3 7h10m-3-3 3 3-3 3M21 17H11m3-3-3 3 3 3',close:'m6 6 12 12M6 18 18 6',back:'M19 12H5m6-6-6 6 6 6'}
 return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]||paths.grid}/></svg>
}
export function Choice({label,value,options,onChange,disabled=false,compact=false}) {
 const [open,setOpen]=useState(false),ref=useRef(null),id=useId()
 const selected=options.find(o=>o.value===value)
 useEffect(()=>{
  if(!open)return
  const close=e=>{if(!ref.current?.contains(e.target))setOpen(false)}
  document.addEventListener('pointerdown',close)
  return()=>document.removeEventListener('pointerdown',close)
 },[open])
 const focusOption=(index=0)=>requestAnimationFrame(()=>ref.current?.querySelectorAll('[role=option]')[Math.max(0,index)]?.focus())
 return <div className={`pw-choice ${compact?'compact':''}`} ref={ref} onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))setOpen(false)}} onKeyDown={e=>{
  if(e.key==='Escape'){setOpen(false);ref.current?.querySelector('button')?.focus()}
  if(open&&['ArrowDown','ArrowUp','Home','End'].includes(e.key)){
   e.preventDefault();const all=[...ref.current.querySelectorAll('[role=option]')],i=all.indexOf(document.activeElement)
   focusOption(e.key==='Home'?0:e.key==='End'?all.length-1:(i+(e.key==='ArrowDown'?1:-1)+all.length)%all.length)
  }
 }}>
  <button className="pw-choice-trigger" aria-label={`${label}: ${selected?.short||selected?.label||'Choose'}`} aria-haspopup="listbox" aria-expanded={open} aria-controls={open?id:undefined} disabled={disabled} onClick={()=>{setOpen(!open);if(!open)focusOption(Math.max(0,options.findIndex(o=>o.value===value)))}} onKeyDown={e=>{if(!open&&e.key==='ArrowDown'){e.preventDefault();setOpen(true);focusOption()}}}>
   <span><small>{label}</small><strong>{selected?.short||selected?.label||'Choose'}</strong></span><Mark name="chevron" size={16}/>
  </button>
  {open&&<div className="pw-choice-menu" role="listbox" aria-label={label} id={id}>{options.length?options.map(o=><button role="option" aria-selected={o.value===value} key={o.value} onClick={()=>{onChange(o.value);setOpen(false);ref.current?.querySelector('button')?.focus()}}><span>{o.label}</span>{o.value===value&&<Mark name="check" size={16}/>}</button>):<p>No options available.</p>}</div>}
 </div>
}
export function Badge({value}) { return <span className={`pw-badge ${verdict(value).toLowerCase()}`}><span aria-hidden="true">{value===true?'✓':value===false?'×':'?'}</span>{verdict(value)}</span> }
export function OutcomeBar({passed,failed,unknown}) {
 const total=passed+failed+unknown
 return <div className="pw-outcome-bar" role="img" aria-label={`${passed} passed, ${failed} failed, ${unknown} unknown`}>
  {total?[['passed',passed],['failed',failed],['unknown',unknown]].map(([key,n])=>n>0&&<span key={key} className={key} style={{width:`${n/total*100}%`}}/>):<span className="unknown" style={{width:'100%'}}/>}
 </div>
}
export function RunFacts({run}) {
 return <dl className="pw-run-facts">{[['Hidden tests',verdict(run.hidden_pass)],['Visible tests',verdict(run.visible_pass)],['Tokens',count(tokenValue(run))],['Model calls',count(run.steps)],['Exit reason',run.exit_reason||'Not recorded']].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
}
export function Empty({title,children,action}) { return <div className="pw-empty"><div className="pw-empty-mark"><Mark name="trace" size={28}/></div><h2>{title}</h2><p>{children}</p>{action}</div> }
export function Patch({value}) {
 return value?<pre className="pw-patch">{text(value).split('\n').map((line,i)=><span className={line.startsWith('+')?'added':line.startsWith('-')?'removed':line.startsWith('@@')?'hunk':''} key={i}>{line||' '}<br/></span>)}</pre>:<p className="pw-empty-inline">No patch was recorded.</p>
}
