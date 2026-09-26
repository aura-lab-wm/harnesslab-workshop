import { expect, it } from 'vitest'
import { condition, preferredComparison, reviewKey, reviewReport, taskPriority, workspaceUrl } from './workspaceFacts'

const rows=[
 {run_id:'a',task_id:'mixed',model:'m',harness_id:'baseline',hidden_pass:true},
 {run_id:'b',task_id:'mixed',model:'m',harness_id:'baseline',hidden_pass:false},
 {run_id:'c',task_id:'failed',model:'m',harness_id:'baseline',hidden_pass:false},
 {run_id:'d',task_id:'unknown',model:'m',harness_id:'baseline',hidden_pass:null},
 {run_id:'e',task_id:'mixed',model:'n',harness_id:'strict',hidden_pass:true},
]
it('makes the full investigation state bookmarkable, including event zero',()=>{
 expect(workspaceUrl({dataset:'my set',model:'vendor/m',task:'x/y',run:'b',compare:'a',tab:'timeline',event:0,filter:'mixed'})).toBe('#/workspace?dataset=my+set&model=vendor%2Fm&task=x%2Fy&run=b&compare=a&tab=timeline&event=0&filter=mixed')
})
it('keeps an invalid explicit condition empty rather than substituting another model',()=>{
 expect(condition(rows,{model:'missing',harness:'baseline'}).groups).toEqual([])
 expect(condition(rows,{model:'m'})).toMatchObject({model:'m',harness:'baseline',total:4,passed:1,failed:2,unknown:1})
})
it('prioritizes mixed outcomes and scopes candidate comparison to the exact task and condition',()=>{
 const scope=condition(rows,{model:'m'})
 expect(taskPriority(scope.groups).map(g=>g.task)).toEqual(['mixed','failed','unknown'])
 const group=scope.groups.find(g=>g.task==='mixed')
 expect(preferredComparison(group,'b').run_id).toBe('a')
 expect(preferredComparison(scope.groups.find(g=>g.task==='failed'),'c')).toBeNull()
})
it('namespaces notes by dataset and run to avoid collisions',()=>{
 expect(reviewKey('ab','c')).not.toBe(reviewKey('a','bc'))
 expect(reviewKey('set','a')).not.toBe(reviewKey('other','a'))
})
it('exports evidence and explicitly user-authored notes without claiming a cause or savings',()=>{
 const report=reviewReport({dataset:'set',run:{run_id:'b',task_id:'mixed',model:'m',harness_id:'baseline',hidden_pass:false},record:{summary:{exit_reason:'budget_exceeded'},spans:[]},note:{text:'Check missing sort',status:'reviewed'},url:'http://localhost/#/workspace?run=b'})
 expect(report).toContain('Check missing sort')
 expect(report).toContain('budget_exceeded')
 expect(report).toContain('Researcher note')
 expect(report).toContain('Root cause is not established')
 expect(report).not.toMatch(/tokens saved/i)
})
