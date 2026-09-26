import { describe, it, expect } from 'vitest'
import { KEY_SLOT, MODEL_SLOT, CAPTURED_SLOT, DEFAULT_MODEL, browserStores,
         keyHint, readMate, writeKey, forgetKey, writeModel, setCaptured } from './matekey'

/* A Storage the tests can look inside. This is not a mock of the unit under test:
   every assertion below is about which slot the module CHOSE and what it put there,
   which is the whole question this module exists to answer. */
function fakeStore() {
  const bag = new Map()
  return { bag,
    getItem: (k) => (bag.has(k) ? bag.get(k) : null),
    setItem: (k, v) => { bag.set(k, String(v)) },
    removeItem: (k) => { bag.delete(k) } }
}
const stores = () => ({ session: fakeStore(), local: fakeStore() })

describe('where the narrator key lives', () => {
  it('remembers an explicitly opted-in key across a new tab without preserving consent', () => {
    const s=stores()
    writeKey(s,'dummy-persistent-key',true)
    setCaptured(s,true)
    const next={session:fakeStore(),local:s.local}
    expect(readMate(next).key).toBe('dummy-persistent-key')
    expect(readMate(next).captured).toBe(false)
    expect(s.session.bag.has(KEY_SLOT)).toBe(false)
  })
  it('moves a remembered key back to tab storage when persistence is disabled', () => {
    const s=stores()
    writeKey(s,'dummy-persistent-key',true)
    writeKey(s,'dummy-persistent-key',false)
    expect(readMate(s).key).toBe('dummy-persistent-key')
    expect(s.local.bag.has(KEY_SLOT)).toBe(false)
    expect(readMate({session:fakeStore(),local:s.local}).keyPresent).toBe(false)
  })
  it('keeps the existing tab key if persistent storage rejects the write', () => {
    const s=stores()
    writeKey(s,'dummy-tab-key')
    s.local.setItem=()=>{throw Error('blocked')}
    writeKey(s,'dummy-new-key',true)
    expect(readMate(s).key).toBe('dummy-tab-key')
  })
  it('replaces a remembered key in its existing storage and forget removes both copies', () => {
    const s=stores()
    writeKey(s,'dummy-first-key',true)
    writeKey(s,'dummy-second-key')
    expect(readMate({session:fakeStore(),local:s.local}).key).toBe('dummy-second-key')
    s.session.setItem(KEY_SLOT,'dummy-stale-copy')
    forgetKey(s)
    expect(s.local.bag.has(KEY_SLOT)).toBe(false)
    expect(s.session.bag.has(KEY_SLOT)).toBe(false)
    expect(readMate(s).keyPresent).toBe(false)
  })
  it('holds the key for the tab and never for the machine', () => {
    const s = stores()
    writeKey(s, 'sk-or-v1-abcdefghijklmnop')
    expect(s.session.bag.get(KEY_SLOT)).toBe('sk-or-v1-abcdefghijklmnop')
    expect(s.local.bag.size).toBe(0)
  })
  it('trims what was pasted', () => {
    const s = stores()
    expect(writeKey(s, '  sk-or-v1-abcdefghijklmnop \n')).toBe('sk-or-v1-abcdefghijklmnop')
    expect(s.session.bag.get(KEY_SLOT)).toBe('sk-or-v1-abcdefghijklmnop')
  })
  it('an empty paste clears rather than storing a blank', () => {
    const s = stores()
    writeKey(s, 'sk-or-v1-abcdefghijklmnop')
    expect(writeKey(s, '   ')).toBe('')
    expect(s.session.bag.has(KEY_SLOT)).toBe(false)
  })
  it('masks the way the backend already masks the lab key', () => {
    expect(keyHint('sk-or-v1-abcdefghijklmnop')).toBe('sk-or-v…mnop')
    expect(keyHint('short')).toBe('')
    expect(keyHint(null)).toBe('')
  })
  it('reads empty storage as no key, the default slug and no consent', () => {
    expect(readMate(stores())).toEqual({ key: '', keyHint: '', keyPresent: false,
                                         model: DEFAULT_MODEL, captured: false })
  })
  it('never throws when storage is unavailable, so a blocked browser sees no narrator, not a blank page', () => {
    const dead = { getItem: () => { throw new Error('blocked') },
                   setItem: () => { throw new Error('blocked') },
                   removeItem: () => { throw new Error('blocked') } }
    expect(() => readMate({ session: dead, local: dead })).not.toThrow()
    expect(readMate({ session: dead, local: dead }).keyPresent).toBe(false)
    expect(() => writeKey({ session: dead, local: dead }, 'sk-or-v1-abcdefghijklmnop')).not.toThrow()
    expect(() => forgetKey({ session: dead, local: dead })).not.toThrow()
  })
  it('hands back inert stores where there is no browser at all, rather than throwing on import', () => {
    /* This is the default App.jsx uses. Under node there is no sessionStorage, which is the same
       shape of failure as a browser with site data blocked. */
    const s = browserStores()
    expect(s.session.getItem(KEY_SLOT)).toBe(null)
    expect(readMate(s).keyPresent).toBe(false)
  })
})

describe('the slug and the consent are stored on opposite lifetimes', () => {
  it('keeps the slug on the machine, since it is not a secret', () => {
    const s = stores()
    expect(writeModel(s, ' deepseek/deepseek-chat ')).toBe('deepseek/deepseek-chat')
    expect(s.local.bag.get(MODEL_SLOT)).toBe('deepseek/deepseek-chat')
    expect(s.session.bag.size).toBe(0)
    expect(readMate(s).model).toBe('deepseek/deepseek-chat')
  })
  it('an emptied slug falls back to the shipped default', () => {
    const s = stores()
    writeModel(s, 'deepseek/deepseek-chat')
    expect(writeModel(s, '')).toBe(DEFAULT_MODEL)
    expect(s.local.bag.has(MODEL_SLOT)).toBe(false)
  })
  it('keeps the captured consent for the tab only, and off by default', () => {
    const s = stores()
    expect(readMate(s).captured).toBe(false)
    setCaptured(s, true)
    expect(s.session.bag.get(CAPTURED_SLOT)).toBe('1')
    expect(s.local.bag.size).toBe(0)
    expect(readMate(s).captured).toBe(true)
    setCaptured(s, false)
    expect(readMate(s).captured).toBe(false)
  })
  it('forgetting the key also withdraws the consent it was given with', () => {
    const s = stores()
    writeKey(s, 'sk-or-v1-abcdefghijklmnop')
    setCaptured(s, true)
    forgetKey(s)
    expect(readMate(s).keyPresent).toBe(false)
    expect(readMate(s).captured).toBe(false)
  })
})

describe('review: consent does not outlive the key it was given with', () => {
  it('does not reuse consent after a different tab replaces the remembered key', () => {
    const s=stores()
    writeKey(s,'dummy-first-key',true)
    setCaptured(s,true)
    writeKey({session:fakeStore(),local:s.local},'dummy-second-key',true)
    expect(readMate(s).key).toBe('dummy-second-key')
    expect(readMate(s).captured).toBe(false)
  })
  it('clears the captured opt-in when the key is replaced', () => {
    const s = stores()
    writeKey(s, 'sk-or-v1-first')
    setCaptured(s, true)
    expect(readMate(s).captured).toBe(true)
    writeKey(s, 'sk-or-v1-second')          // a different key, given no consent of its own
    expect(readMate(s).captured).toBe(false)
  })

  it('clears it when the key is cleared, as forgetKey already did', () => {
    const s = stores()
    writeKey(s, 'sk-or-v1-first')
    setCaptured(s, true)
    writeKey(s, '')
    expect(readMate(s).captured).toBe(false)
  })

  it('leaves it alone when the same key is written again', () => {
    const s = stores()
    writeKey(s, 'sk-or-v1-same')
    setCaptured(s, true)
    writeKey(s, '  sk-or-v1-same  ')        // the same key, re-pasted
    expect(readMate(s).captured).toBe(true)
  })
})
