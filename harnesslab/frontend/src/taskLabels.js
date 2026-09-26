// Display labels for the bundled benchmark, checked against tasks/*/task.json.
// These indices belong to the benchmark IDs, never a filtered/sorted row number.
const benchmarkTasks = {
 t01_slugify: ['01', 'Slug generation'],
 t02_intervals: ['02', 'Interval merging'],
 t03_ratelimit: ['03', 'Rate limiting'],
 t04_csvsplit: ['04', 'CSV splitting'],
 t05_leaky_duration: ['05', 'Compound duration parsing'],
 t06_injected_config: ['06', 'Configuration overrides'],
 t07_cache_cleanup: ['07', 'Cache cleanup'],
 t08_ambiguous_handler: ['08', 'Deprecated handler removal'],
}
export function taskIdentity(id) {
 const known=Object.hasOwn(benchmarkTasks,id)?benchmarkTasks[id]:null
 return {id,title:known?.[1]||id,index:known?.[0]??null,label:known?`${known[0]} · ${known[1]}`:id}
}
export const taskLabel=id=>taskIdentity(id).label
export const taskMatches=(id,query='')=>`${id} ${taskLabel(id)}`.toLowerCase().includes(query.toLowerCase())
