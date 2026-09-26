// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import BuddyModelPicker from './BuddyModelPicker'
import BuddyMessage from './BuddyMessage'

afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks()})
const rows=['Alpha','Beta','Gamma'].map(name=>({id:`vendor/${name.toLowerCase()}`,name,architecture:{input_modalities:['text'],output_modalities:['text']}}))
const open=()=>fireEvent.click(screen.getByRole('button',{name:/Choose Buddy model/}))

it('puts available analysis candidates first without duplicating them or changing the saved model',async()=>{
 const candidate={id:'google/gemini-3.8-flash',name:'Google: Gemini 3.8 Flash',architecture:{input_modalities:['text'],output_modalities:['text']}}
 const choose=vi.fn()
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({data:[...rows,candidate,candidate]})})))
 render(<BuddyModelPicker value="vendor/alpha" onChange={choose}/>);open()
 const suggested=await screen.findByRole('group',{name:'Suggested for trajectory analysis'})
 expect(within(suggested).getAllByRole('option')).toHaveLength(1)
 expect(screen.getAllByRole('option')).toHaveLength(4)
 expect(screen.getAllByRole('option')[0].textContent).toContain('Gemini 3.8 Flash')
 expect(screen.getByRole('option',{name:/Alpha/}).getAttribute('aria-selected')).toBe('true')
 expect(choose).not.toHaveBeenCalled()
 fireEvent.click(within(suggested).getByRole('option'))
 expect(choose).toHaveBeenCalledWith('google/gemini-3.8-flash')
 expect(screen.queryByRole('dialog')).toBeNull()
})

it('searches analysis use cases as well as the full catalog',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({data:[...rows,{id:'anthropic/claude-opus-5.5',name:'Claude Opus 5.5',architecture:{input_modalities:['text'],output_modalities:['text']}}]})})))
 render(<BuddyModelPicker value="vendor/alpha" onChange={()=>{}}/>);open()
 await screen.findByRole('group',{name:'Suggested for trajectory analysis'})
 fireEvent.change(screen.getByRole('combobox'),{target:{value:'failure'}})
 expect(screen.getAllByRole('option')).toHaveLength(1)
 expect(screen.getByRole('option').textContent).toContain('Claude Opus 5.5')
 fireEvent.change(screen.getByRole('combobox'),{target:{value:'Beta'}})
 expect(screen.getAllByRole('option')).toHaveLength(1)
 expect(screen.getByRole('option').textContent).toContain('Beta')
 expect(screen.queryByRole('group',{name:'Suggested for trajectory analysis'})).toBeNull()
})

it('loads only on opening, excludes non-text models, and never attaches credentials',async()=>{
 const fetcher=vi.fn(async()=>({ok:true,json:async()=>({data:[...rows,{id:'vendor/image',name:'Image',architecture:{input_modalities:['text'],output_modalities:['image']}}]})}))
 vi.stubGlobal('fetch',fetcher)
 render(<BuddyModelPicker value="vendor/alpha" onChange={()=>{}}/>)
 expect(fetcher).not.toHaveBeenCalled();open()
 expect(await screen.findAllByRole('option')).toHaveLength(3)
 expect(fetcher.mock.calls[0][1].headers).toBeUndefined()
 expect(fetcher.mock.calls[0][1].body).toBeUndefined()
 fireEvent.keyDown(screen.getByRole('combobox'),{key:'ArrowUp'})
 expect(document.activeElement.textContent).toContain('Gamma')
 fireEvent.keyDown(document.activeElement,{key:'Escape'})
 expect(screen.queryByRole('dialog')).toBeNull()
 expect(document.activeElement).toBe(screen.getByRole('button',{name:/Choose Buddy model/}))
})

it('allows an explicit model ID when the catalog fails without making a completion request',async()=>{
 const choose=vi.fn(),fetcher=vi.fn(async()=>({ok:false}))
 vi.stubGlobal('fetch',fetcher)
 render(<BuddyModelPicker value="vendor/alpha" onChange={choose}/>);open()
 expect(await screen.findByRole('alert')).toHaveProperty('textContent',expect.stringContaining('Could not load'))
 fireEvent.change(screen.getByRole('combobox'),{target:{value:'vendor/private-reader'}})
 fireEvent.click(screen.getByRole('button',{name:/Use model ID/}))
 expect(choose).toHaveBeenCalledWith('vendor/private-reader')
 expect(fetcher).toHaveBeenCalledTimes(1)
})

it('keeps the picker open if persisting a selection fails',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({data:rows})})))
 render(<BuddyModelPicker value="vendor/alpha" onChange={()=>false}/>);open()
 fireEvent.click(await screen.findByRole('option',{name:/Beta/}))
 expect(screen.getByRole('dialog')).toBeTruthy()
})

it('does not submit settings when Enter is pressed in the model search',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({data:rows})})))
 render(<form><BuddyModelPicker variant="field" value="vendor/alpha" onChange={()=>{}}/></form>);open()
 await screen.findByRole('option',{name:/Beta/})
 const search=screen.getByRole('combobox')
 fireEvent.change(search,{target:{value:'Beta'}})
 expect(fireEvent.keyDown(search,{key:'Enter'})).toBe(false)
 expect(screen.getByRole('dialog')).toBeTruthy()
})

it('retries catalog failures and exposes empty results honestly',async()=>{
 const fetcher=vi.fn().mockResolvedValueOnce({ok:false}).mockResolvedValueOnce({ok:true,json:async()=>({data:[]})})
 vi.stubGlobal('fetch',fetcher)
 render(<BuddyModelPicker value="vendor/alpha" onChange={()=>{}}/>);open()
 fireEvent.click(await screen.findByRole('button',{name:'Retry catalog'}))
 await waitFor(()=>expect(screen.getByText('No matching text models.')).toBeTruthy())
 expect(fetcher).toHaveBeenCalledTimes(2)
})

it('renders adjacent Markdown blocks without duplicate keys or executable HTML',()=>{
 const errors=vi.spyOn(console,'error').mockImplementation(()=>{})
 const {container}=render(<BuddyMessage text={'A paragraph\n## Heading\n- **Run 3:** `sort()`\n<script>alert(1)</script>\n```js\nconst ok = true\n```'}/>)
 expect(screen.getByRole('heading',{name:'Heading'})).toBeTruthy()
 expect(container.querySelector('li strong').textContent).toBe('Run 3:')
 expect(container.querySelector('script')).toBeNull()
 expect(container.querySelector('pre code').textContent).toBe('const ok = true')
 expect(errors).not.toHaveBeenCalled()
})
