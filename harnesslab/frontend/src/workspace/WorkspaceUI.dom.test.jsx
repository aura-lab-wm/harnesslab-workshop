// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { Choice } from './WorkspaceUI'
afterEach(cleanup)
function Example(){const [value,setValue]=useState('one');return <><Choice label="Model" value={value} onChange={setValue} options={[{value:'one',label:'One'},{value:'two',label:'Two'}]}/><button>Outside</button></>}
it('closes the custom picker when keyboard focus leaves the control',()=>{
 render(<Example/>);
 fireEvent.click(screen.getByRole('button',{name:'Model: One'}))
 const option=screen.getByRole('option',{name:'Two'})
 fireEvent.blur(option,{relatedTarget:screen.getByRole('button',{name:'Outside'})})
 expect(screen.queryByRole('listbox')).toBeNull()
})
