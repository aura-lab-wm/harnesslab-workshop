import { findings, taskGroups } from '../directions/investigationFacts'
import { summarize, verdict } from '../directions/shared'

export const tokenValue=r=>Number.isFinite(r.input_tokens)&&Number.isFinite(r.output_tokens)?r.input_tokens+r.output_tokens:null
export const text=value=>typeof value==='string'?value:JSON.stringify(value,null,2)

export function workspaceUrl(scope={}) {
 const params=new URLSearchParams()
 for(const key of ['dataset','model','harness','view','task','run','compare','tab','event','filter','search','sort','panel']) {
  if(scope[key]!==undefined&&scope[key]!==null&&scope[key]!=='')params.set(key,scope[key])
 }
 return `#/workspace${params.size?'?'+params:''}`
}
export function condition(rows,scope={}) {
 const models=[...new Set(rows.map(r=>r.model).filter(Boolean))].sort()
 const model=scope.model||models[0]||''
 const harnesses=[...new Set(rows.filter(r=>r.model===model).map(r=>r.harness_id).filter(Boolean))].sort()
 const harness=scope.harness||(harnesses.includes('baseline')?'baseline':harnesses[0])||''
 const selected=rows.filter(r=>r.model===model&&r.harness_id===harness)
 const groups=taskGroups(rows,model,harness),stats=summarize(selected)
 return {models,model,harnesses,harness,rows:selected,groups,...stats,failed:stats.known-stats.passed,mixed:groups.filter(g=>g.passed&&g.failed).length}
}
export function taskPriority(groups) {
 const rank=g=>g.failed&&g.passed?0:g.failed?1:g.unknown?2:3
 return [...groups].sort((a,b)=>rank(a)-rank(b)||b.failed-a.failed||a.task.localeCompare(b.task))
}
export function preferredComparison(group,runId) {
 const run=group?.runs.find(r=>r.run_id===runId)
 if(!run)return null
 return group.runs.find(r=>r.run_id!==runId&&typeof r.hidden_pass==='boolean'&&r.hidden_pass!==run.hidden_pass)||group.runs.find(r=>r.run_id!==runId)||null
}
export function reviewKey(dataset,runId) { return `hs.review.${JSON.stringify([dataset,runId])}` }
export function reviewReport({dataset,run,record,note,url}) {
 const summary={...run,...record.summary}
 return [
  '# HarnessLab investigation',`Dataset: ${dataset}`,`Task: ${run.task_id}`,`Model: ${run.model}`,`Harness: ${run.harness_id}`,`Run: ${run.run_id}`,
  `Hidden tests: ${verdict(summary.hidden_pass)}`,`Visible tests: ${verdict(summary.visible_pass)}`,`Exit: ${summary.exit_reason||'Not recorded'}`,
  '', '## Recorded checkpoints',...findings({...record,summary}).map(f=>`- ${f.title}${f.index>=0?` (event ${f.index+1})`:''}: ${f.detail}`),
  'Root cause is not established by these checkpoints alone.', '', '## Researcher note',`Review status: ${note.status||'unreviewed'}`,note.text||'No note saved.',
  '',`Open recording: ${url}`, '', 'Descriptive evidence only; no causal or intervention-effectiveness claim.',
 ].join('\n')
}
