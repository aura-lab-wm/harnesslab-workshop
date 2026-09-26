// Retrospective observations only. These rules describe a recorded outcome;
// they do not score prefixes, predict a next action, or recommend interventions.
export function takeaway(summary = {}, spans = []) {
  const lastChat = spans.filter(s => s.span === 'chat').at(-1)
  if (summary.exit_reason === 'no_action') {
    if (lastChat?.['gen_ai.response.finish_reasons']?.includes('length') && Array.isArray(lastChat.tool_calls) && lastChat.tool_calls.length === 0) {
      return {title:'Response cut off without a tool call', text:'The last model response reached its output-length limit without issuing a tool call. The harness recorded a no_action exit.', why:'A response-length limit is different from a limit on the number of model calls. Describing a fix in a response does not edit the workspace.', question:'Which event shows that the response ended before another tool action?', seq:lastChat.seq}
    }
    return {title:'Stopped without a next tool action', text:'The run ended with no_action. This exit records that the harness received no next tool action; by itself, it does not establish why.', why:'Use the last response and tool events to investigate. Do not infer a token limit, refusal, or successful completion from the exit label alone.', question:'What was the last action actually executed?'}
  }
  if (summary.exit_reason === 'max_steps') {
    const cap = spans.find(s=>s.span==='invoke_agent'&&s.status==='start')?.harness?.max_steps
    return {title:'Model-call limit reached', text:Number.isFinite(cap) ? `The recorded harness allowed ${cap} model calls. The run ended with max_steps.` : 'The harness recorded a max_steps exit. Its configured call limit is not available here.', why:'Reaching a limit explains why execution stopped, not whether another attempt would have repaired the task. Inspect the recorded grade separately.', question:'Did the last test attempt show progress, or did the same problem remain?'}
  }
  if (summary.visible_pass === true && summary.hidden_pass === false) return {title:'Visible tests passed; hidden tests failed', text:'The recorded visible-suite result passed, but the held-out suite rejected the result.', why:'Passing the tests available to the agent does not establish that the full task was solved. This grade alone does not identify the failing hidden assertion.', question:'What did the visible tests establish, and what might they not cover?'}
  if (summary.hidden_pass === true) return {title:summary.visible_pass === true ? 'Visible and hidden tests passed' : 'Hidden tests passed', text:'The recorded held-out evaluation passed for this run.', why:'This is evidence about this task and attempt—not a guarantee of success on another task or repeat.', question:'Does another repeat of the same task reach the same result?'}
  if (summary.hidden_pass === false) return {title:'Hidden tests failed', text:'The recorded held-out evaluation failed for this run.', why:'A final failure does not mean every earlier action was wrong. Read the patch and test evidence before assigning a cause.', question:'Which observation first shows a problem in the recorded work?'}
  return {title:'Outcome not recorded', text:'There is no recorded true/false hidden-test outcome for this run.', why:'Missing evaluation is not failure. You can describe recorded actions, but cannot infer task success from their presence.', question:'What evidence would you need before calling this task solved?'}
}

export function eventDescription(s) {
  if (s.span === 'chat') return 'Model response'
  if (s.span === 'edit') return 'Workspace edit'
  if (s.span === 'grade') return 'Recorded evaluation'
  if (s.span === 'invoke_agent') return s.status === 'start' ? 'Run started' : 'Run ended'
  if (s.span === 'boundary_event') return `Policy event: ${s.decision || 'decision not recorded'}`
  if (s.span === 'execute_tool') {
    if (s.status === 'blocked' || s.status === 'sentinel_blocked') return 'Tool action blocked'
    if (s.status === 'error') return 'Tool execution error'
    const exit = /(?:^|\n)\s*exit[=:]\s*(-?\d+)/i.exec(s.result_preview || '')
    if (s['gen_ai.tool.name'] === 'run_tests' && exit) return Number(exit[1]) === 0 ? 'Test command returned success' : 'Tool executed; tests returned a failure'
    return s.status === 'ok' ? 'Tool executed' : 'Tool status not recorded'
  }
  return s.span || 'Recorded event'
}
