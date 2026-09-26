import { describe, expect, it } from 'vitest'
import { BUILD, newerBuild } from './buildWatch'

const answer = (body, ok = true) => async () => ({ ok, json: async () => body })

describe('build watch: an open tab notices a rebuilt UI', () => {
  it('the bundle carries the hash of the source it was built from', () => {
    expect(BUILD).toMatch(/^[0-9a-f]{64}$/)
  })
  it('a different stamp on the server means this tab is stale', async () => {
    expect(await newerBuild(answer({ sha256: 'f'.repeat(64) }), 'a'.repeat(64))).toBe(true)
  })
  it('the same stamp is current', async () => {
    expect(await newerBuild(answer({ sha256: 'a'.repeat(64) }), 'a'.repeat(64))).toBe(false)
  })
  it('a missing stamp, an HTTP error or a dead server is never read as a new build', async () => {
    expect(await newerBuild(answer({}, true), 'a'.repeat(64))).toBe(false)
    expect(await newerBuild(answer({ sha256: 'f'.repeat(64) }, false), 'a'.repeat(64))).toBe(false)
    expect(await newerBuild(async () => { throw new TypeError('Failed to fetch') }, 'a'.repeat(64))).toBe(false)
  })
})
