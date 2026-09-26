/* Small, text-only Markdown renderer. Model output never becomes HTML, links,
 * attributes, or executable content. Preserve unsupported syntax as plain text. */
function inline(text) {
 return text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g).map((part,i)=>part.startsWith('`')&&part.endsWith('`')?<code key={i}>{part.slice(1,-1)}</code>:part.startsWith('**')&&part.endsWith('**')?<strong key={i}>{part.slice(2,-2)}</strong>:part)
}
export default function BuddyMessage({text}) {
 const lines=String(text||'').split('\n'),blocks=[]
 for(let i=0;i<lines.length;){
  const line=lines[i],key=i
  if(!line.trim()){i++;continue}
  if(/^\s*```/.test(line)){
   const code=[];i++
   while(i<lines.length&&!/^\s*```/.test(lines[i]))code.push(lines[i++])
   if(i<lines.length)i++
   blocks.push(<pre key={key}><code>{code.join('\n')}</code></pre>);continue
  }
  const heading=line.match(/^#{1,6}\s+(.+)/)
  if(heading){blocks.push(<h3 key={i}>{inline(heading[1])}</h3>);i++;continue}
  const item=line.match(/^\s*(?:[-*]|(\d+)\.)\s+(.+)/)
  if(item){
   const ordered=!!item[1],items=[],start=ordered?Number(item[1]):undefined
   while(i<lines.length){
    const next=lines[i].match(/^\s*(?:[-*]|(\d+)\.)\s+(.+)/)
    if(!next||!!next[1]!==ordered)break
    items.push(<li key={i}>{inline(next[2])}</li>);i++
   }
   blocks.push(ordered?<ol key={key} start={start}>{items}</ol>:<ul key={key}>{items}</ul>);continue
  }
  const paragraph=[line];i++
  while(i<lines.length&&lines[i].trim()&&!/^\s*(?:```|#{1,6}\s|[-*]\s|\d+\.\s)/.test(lines[i]))paragraph.push(lines[i++])
  blocks.push(<p key={key}>{inline(paragraph.join('\n'))}</p>)
 }
 return <div className="pw-buddy-prose">{blocks}</div>
}
