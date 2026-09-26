import { expect, it } from 'vitest'
import { findings, taskGroups } from './investigationFacts'

it('fixes model and harness, groups repeats and keeps unknown distinct from failure',()=>{
 const rows=[{run_id:'b',task_id:'task',model:'m',harness_id:'h',repeat_index:1,hidden_pass:false},{run_id:'a',task_id:'task',model:'m',harness_id:'h',repeat_index:0,hidden_pass:true},{run_id:'c',task_id:'task',model:'m',harness_id:'h',hidden_pass:null},{run_id:'d',task_id:'task',model:'other',harness_id:'h',hidden_pass:false},{run_id:'e',task_id:'task',model:'m',harness_id:'other',hidden_pass:false}]
 const [group]=taskGroups(rows,'m','h')
 expect(group).toMatchObject({task:'task',passed:1,failed:1,unknown:1})
 expect(group.runs.map(r=>[r.run_id,r.ordinal])).toEqual([['a',1],['b',2],['c',3]])
})
it('links the visible/hidden mismatch to the recorded evaluation, not an invented root cause',()=>{
 expect(findings({summary:{visible_pass:true,hidden_pass:false},spans:[{seq:10,span:'grade',visible:true,hidden:false}]})).toEqual(expect.arrayContaining([expect.objectContaining({kind:'mismatch',index:0})]))
})
it('distinguishes tool errors, blocked actions and output signatures from model claims',()=>{
 const result=findings({spans:[{span:'chat',text:'Traceback error'}, {span:'execute_tool',status:'error'}, {span:'execute_tool',status:'blocked'}, {span:'execute_tool',status:'ok',result_preview:'Traceback (most recent call last): failure'}]})
 expect(result.map(f=>f.index)).toEqual([1,2,3])
 expect(result.map(f=>f.kind)).toEqual(['tool-error','blocked','output-signal'])
})
it('does not turn a budget exit or repeated calls into a causal failure diagnosis',()=>{
 expect(findings({summary:{exit_reason:'budget_exceeded',hidden_pass:false},spans:[]})).toEqual([])
})
it('links a recorded response cutoff without calling it the cause of task failure',()=>{
 const spans=[{seq:3,span:'chat','gen_ai.response.finish_reasons':['length'],tool_calls:[]}]
 expect(findings({summary:{exit_reason:'no_action'},spans})).toEqual([expect.objectContaining({kind:'response-cutoff',index:0})])
 expect(findings({summary:{exit_reason:'no_action'},spans:[{span:'chat',tool_calls:[]}]})).toEqual([])
})
