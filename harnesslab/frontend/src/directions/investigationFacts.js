export function taskGroups(rows,model,harness) {
 const groups=new Map()
 for(const row of rows.filter(r=>r.model===model&&r.harness_id===harness)) {
  const task=row.task_id||'Task not recorded'
  if(!groups.has(task))groups.set(task,[])
  groups.get(task).push(row)
 }
 return [...groups].sort(([a],[b])=>a.localeCompare(b)).map(([task,records])=>{
  const runs=[...records].sort((a,b)=>(a.repeat_index??Infinity)-(b.repeat_index??Infinity)||a.run_id.localeCompare(b.run_id)).map((r,i)=>({...r,ordinal:i+1}))
  return {task,runs,passed:runs.filter(r=>r.hidden_pass===true).length,failed:runs.filter(r=>r.hidden_pass===false).length,unknown:runs.filter(r=>typeof r.hidden_pass!=='boolean').length}
 })
}
export function findings(record) {
 const result=[],spans=(record.spans||[]).filter(Boolean),summary=record.summary||{}
 const add=(kind,title,detail,index)=>result.push({kind,title,detail,index})
 const lastChatIndex=spans.findLastIndex(s=>s.span==='chat'),lastChat=spans[lastChatIndex]
 if(summary.exit_reason==='no_action'&&lastChat?.['gen_ai.response.finish_reasons']?.includes('length')&&Array.isArray(lastChat.tool_calls)&&lastChat.tool_calls.length===0)add('response-cutoff','Response cut off without a tool action','The last response reached its output-length limit and recorded no tool call. The harness then stopped with no_action. This establishes how execution stopped, not whether a longer response would have solved the task.',lastChatIndex)
 if(summary.visible_pass===true&&summary.hidden_pass===false)add('mismatch','Visible tests passed; hidden tests failed','The two evaluations disagree. Inspect the task requirements and patch; this grade alone does not identify the failing assertion.',spans.findIndex(s=>s.span==='grade'))
 spans.forEach((s,index)=>{
  if(s.span!=='execute_tool')return
  if(['blocked','sentinel_blocked'].includes(s.status))add('blocked','A tool action was blocked','The recorder marked this action as blocked. Inspect its policy message and requested arguments.',index)
  else if(s.status==='error')add('tool-error','Tool execution error','The tool recorded an error status. Later events may show a recovery.',index)
  else if(s.tests_passed===false||/\bexit\s*[=:]\s*[1-9]\d*\b/i.test(String(s.result_preview||'')))add('command-failure','Command reported failure','A nonzero exit or failed test result was recorded. Tool delivery can still have status “ok”.',index)
  else if(/Traceback \(most recent call last\)|\b(?:Error|Exception):|Directory .+ not found/i.test(String(s.result_preview||'')))add('output-signal','Error text in tool output','Text signature in the recorded output, not a verified root cause. Read the surrounding events.',index)
 })
 return result
}
