// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { Tag } from './ui.jsx'

describe('Tag', () => {
  it('wraps plain text in .rg-ell, so a long label truncates inside the chip instead of spilling out of its cell', () => {
    const { container } = render(<><Tag tone="amb" nc>destructive temptation</Tag><Tag><b>x</b> y</Tag></>)
    const [plain, mixed] = container.querySelectorAll('.rg-tag')
    expect(plain.querySelector(':scope > .rg-ell').textContent).toBe('destructive temptation')
    expect(mixed.querySelector(':scope > .rg-ell')).toBeNull()
  })
})
