import { expect, it, vi } from 'vitest'
import { askBuddy, buddyEvidence, redactBuddy } from './buddyClient'

const run={run_id:'run-a',ordinal:1,task_id:'sort',model:'experiment/model',harness_id:'baseline',hidden_pass:false}
const group={task:'sort',runs:[run],passed:0,failed:1,unknown:0}
const data={model:'experiment/model',harness:'baseline',groups:[group],total:1,passed:0,failed:1,unknown:0}
const full={summary:run,issue:'Sort intervals.',patch:'+sort()',spans:Array.from({length:100},(_,i)=>({seq:i*2,span:'chat',text:'x'.repeat(2000),api_key:'not-allowed'})),messages:[{role:'user',content:'PRIVATE FULL TRANSCRIPT'}],privateNotes:'PRIVATE NOTES',replay:{predicted_failure:'NOT AN OBSERVATION'}}
const evidence=()=>buddyEvidence({dataset:'recordings',data,scope:{view:'tasks',event:100},group,run,record:full})

it('bounds evidence, preserves a selected middle event, and excludes private or predicted fields',()=>{
 const pack=evidence(),wire=JSON.stringify(pack)
 expect(pack.runs[0].coverage.events_in_record).toBe(100)
 expect(pack.runs[0].coverage.events_included).toBe(61)
 expect(pack.runs[0].events.find(e=>e.seq===100).event_number).toBe(51)
 expect(pack.runs[0].events.every(e=>e.excerpt.text.length<=700)).toBe(true)
 expect(wire).not.toMatch(/PRIVATE FULL|PRIVATE NOTES|NOT AN OBSERVATION|not-allowed/)
 expect(pack.runs[0].events[0].excerpt.truncated).toBe(true)
})
it('gives each recording its UI run label rather than leaving array position to be mistaken for a run number',()=>{
 const selected={...run,ordinal:3,repeat_index:2}
 const pack=buddyEvidence({dataset:'recordings',data,scope:{},group,run:selected,record:{...full,summary:selected}})
 expect(pack.runs[0].label).toBe('Run 3')
 expect(pack.runs[0].summary.repeat_index).toBeUndefined()
})
it('refuses records whose task or condition does not match the indexed selection',()=>{
 for(const mismatch of [{run_id:'different'},{task_id:'other-task'},{model:'other-model'},{harness_id:'other-harness'}]){
  expect(()=>buddyEvidence({dataset:'recordings',data,scope:{},group,run,record:{...full,summary:{...run,...mismatch}}})).toThrow(/identity/)
 }
})
it('redacts common credential patterns and the current key from evidence, questions, and conversation',async()=>{
 const key='current-test-credential',secret='sk-or-secret-123456789'
 let sent
 const fetchImpl=vi.fn(async(_url,options)=>{sent=JSON.parse(options.body);return {ok:true,json:async()=>({choices:[{message:{content:`Never echo ${key} or ${secret}`}}]})}})
 const result=await askBuddy({key,model:'assistant/model',question:`What is ${key}?`,evidence:{patch:`Bearer my-secret-token api_key=abcde ${secret}`},history:[{role:'system',content:'INJECTED SYSTEM'},{role:'user',content:key}],fetchImpl})
 const content=JSON.stringify(sent.messages)
 expect(content).not.toMatch(/current-test-credential|sk-or-secret|my-secret-token|abcde|INJECTED SYSTEM/)
 expect(result.answer).not.toMatch(/current-test-credential|sk-or-secret/)
 expect(result.cost).toBe(null)
 expect(sent.model).toBe('assistant/model')
 expect(sent.tools).toBeUndefined()
 expect(redactBuddy('password="my-password"')).not.toContain('my-password')
})
it('does not leak upstream error bodies and does not treat empty or failed completions as answers',async()=>{
 const request={key:'test-key',model:'assistant/model',question:'Explain',evidence:{}}
 await expect(askBuddy({...request,fetchImpl:async()=>({ok:false,status:401,json:async()=>({error:'SENSITIVE BODY'})})})).rejects.toThrow('OpenRouter rejected this key')
 for(const body of [{error:{message:'SENSITIVE BODY'}},{choices:[]},{choices:[{message:{content:''}}]}])await expect(askBuddy({...request,fetchImpl:async()=>({ok:true,json:async()=>body})})).rejects.toThrow('no answer')
})
it('refuses a missing key and an already-cancelled request before network access',async()=>{
 const fetchImpl=vi.fn(),controller=new AbortController();controller.abort()
 await expect(askBuddy({key:'',model:'x',question:'x',fetchImpl})).rejects.toThrow(/key/)
 await expect(askBuddy({key:'valid-key',model:'x',question:'x',signal:controller.signal,fetchImpl})).rejects.toThrow(/cancelled/)
 expect(fetchImpl).not.toHaveBeenCalled()
})
it('gives a reasoning model room to answer and reports how much of the budget went to reasoning',async()=>{
 let sent
 const fetchImpl=vi.fn(async(_url,options)=>{sent=JSON.parse(options.body);return {ok:true,json:async()=>({choices:[{finish_reason:'length',message:{content:'Partial answer'}}],usage:{prompt_tokens:6551,completion_tokens:1196,completion_tokens_details:{reasoning_tokens:1140}}})}})
 const result=await askBuddy({key:'test-key',model:'google/gemini-3.8-flash',question:'What evidence explains the failure?',evidence:{},fetchImpl})
 // The old cap (1200) let hidden reasoning eat the whole answer: the reported bug.
 expect(sent.max_tokens).toBeGreaterThanOrEqual(4000)
 expect(sent.reasoning).toEqual({effort:'low'})
 expect(result.incomplete).toBe(true)
 expect(result.reasoning).toBe(1140)
})
it('explains an answer lost entirely to reasoning instead of calling it an empty reply',async()=>{
 const fetchImpl=async()=>({ok:true,json:async()=>({choices:[{finish_reason:'length',message:{content:''}}],usage:{completion_tokens:4000,completion_tokens_details:{reasoning_tokens:4000}}})})
 await expect(askBuddy({key:'test-key',model:'m',question:'Explain',evidence:{},fetchImpl})).rejects.toThrow(/hidden reasoning/)
})
