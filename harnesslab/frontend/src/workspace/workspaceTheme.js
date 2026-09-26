import { useSyncExternalStore } from 'react'

// Independent of the legacy analysis shell's data-theme attribute. The current
// workspace uses semantic pw tokens, including in offline exports.

const KEY='hs.workspaceTheme',EVENT='harnesslab:appearance'
const valid=value=>value==='dark'||value==='light'
const snapshot=()=>document.documentElement.dataset.workspaceTheme==='dark'?'dark':'light'
let notice=''
const notify=()=>window.dispatchEvent(new Event(EVENT))
export function initWorkspaceTheme() {
 let stored
 try{stored=localStorage.getItem(KEY)}catch{/* Keep the light default when storage is unavailable. */}
 document.documentElement.dataset.workspaceTheme=valid(stored)?stored:'light'
 notice=''
 notify()
}
function subscribe(listener) {
 const changed=e=>{
  if(e.key!==KEY&&e.key!==null)return
  try{if(e.storageArea&&e.storageArea!==localStorage)return}catch{return}
  document.documentElement.dataset.workspaceTheme=valid(e.newValue)?e.newValue:'light'
  notice='';notify()
 }
 window.addEventListener(EVENT,listener);window.addEventListener('storage',changed)
 return()=>{window.removeEventListener(EVENT,listener);window.removeEventListener('storage',changed)}
}
export function setWorkspaceTheme(theme) {
 if(!valid(theme))return
 document.documentElement.dataset.workspaceTheme=theme
 try{localStorage.setItem(KEY,theme);notice=''}catch{notice='Theme changed for this visit only. Browser storage is unavailable.'}
 notify()
}
export function useWorkspaceTheme() {
 const theme=useSyncExternalStore(subscribe,snapshot,()=> 'light')
 const message=useSyncExternalStore(subscribe,()=>notice,()=> '')
 return {theme,message,setTheme:setWorkspaceTheme}
}
