// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Evidence, summarize, CohortGrid, RunQueue } from './shared'
afterEach(()=>{cleanup();vi.unstubAllGlobals()})
const rows=[{run_id:'a',model:'model',harness_id:'base',task_id:'task',hidden_pass:true,cost_usd:0,input_tokens:10,output_tokens:5},{run_id:'b',model:'model',harness_id:'base',task_id:'task',hidden_pass:null,cost_usd:null}]
it('excludes unknown grades and unmeasured cost instead of converting them to failures or zero',()=>{
  expect(summarize(rows)).toMatchObject({total:2,known:1,passed:1,unknown:1,rate:1,cost:0,costCount:1,tokens:15,tokenCount:1})
  expect(summarize([])).toMatchObject({rate:null,cost:null,tokens:null})
})
it('selects the precise cohort from its labeled measurement cell',()=>{
  let selected
  render(<CohortGrid runs={rows} scope={{}} onSelect={(model,harness)=>{selected=[model,harness]}} />)
  fireEvent.click(screen.getByRole('button',{name:/model.*base.*100/}))
  expect(selected).toEqual(['model','base'])
  expect(screen.getByText(/1 unknown/)).toBeTruthy()
})
it('keeps all runs reachable beyond its first page',()=>{
  const many=Array.from({length:65},(_,i)=>({...rows[0],run_id:`r${i}`,task_id:`task${i}`}))
  render(<RunQueue runs={many} selected="" onSelect={()=>{}} />)
  expect(screen.queryByRole('button',{name:/task64/})).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:/Show more/}))
  expect(screen.getByRole('button',{name:/task64/})).toBeTruthy()
})
it('shows actual event evidence, measured zero, and teaching only when enabled',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,headers:{get:()=> 'application/json'},json:async()=>({summary:{...rows[0],visible_pass:true,hidden_pass:false},spans:[{seq:0,span:'chat',text:'Actual recorded response'},{seq:1,span:'grade',visible:true,hidden:false}],patch:'actual diff',issue:'Original task request'})})))
  const view=render(<Evidence dataset="live" runId="a" studentMode={false} />)
  await screen.findByRole('heading',{name:'task'})
  expect(screen.queryByRole('region',{name:'Analysis guidance'})).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:/Event 1/}))
  expect(screen.getByText('Actual recorded response')).toBeTruthy()
  expect(screen.getByText('$0.0000')).toBeTruthy()
  fireEvent.click(screen.getByRole('button',{name:'Patch'}))
  expect(screen.getByText('actual diff')).toBeTruthy()
  fireEvent.click(screen.getByRole('button',{name:'Task'}))
  expect(screen.getByText('Original task request')).toBeTruthy()
  view.rerender(<Evidence dataset="live" runId="a" studentMode />)
  expect(screen.getByRole('region',{name:'Analysis guidance'})).toBeTruthy()
})
it('shows recorded tool calls when the model response has empty text',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,headers:{get:()=> 'application/json'},json:async()=>({summary:rows[0],spans:[{seq:0,span:'chat',text:'',tool_calls:[{name:'read_file',arguments:{path:'solution.py'}}]}]})})))
 render(<Evidence dataset="live" runId="a" />)
 expect(await screen.findByText('Requested tool calls')).toBeTruthy()
 expect(screen.getAllByText(/read_file/).length).toBeGreaterThan(0)
})
