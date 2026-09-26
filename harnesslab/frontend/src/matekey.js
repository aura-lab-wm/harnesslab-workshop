/* Where the narrator's OpenRouter key lives, and what that honestly costs.

   The design put the key in the page so a beat can stream without a backend relay. A
   key in a page is not a secret from anything running in that page: any script on this
   origin, any extension with host permissions, and the devtools console can read it. So
   this module promises only the narrower set it can actually keep:

     - tab-only sessionStorage is the default; explicit Remember opts into this
       browser origin's localStorage, outside the repository and exported files;
     - it is never sent to the harnesslab backend — POST /api/settings/key is the lab's
       OWN key and the two never mix;
     - forgetKey() removes it, and the settings pane offers that as a button.

   The slug and the consent are not secrets and are stored on deliberately opposite
   lifetimes: the slug persists on the machine (retyping it each session is friction for
   nothing), the consent does not, because sending private transcript text is a
   per-session act by design. */

export const KEY_SLOT = 'hs.mate.key'
export const MODEL_SLOT = 'hs.mate.model'
export const CAPTURED_SLOT = 'hs.mate.captured'
const CAPTURED_KEY_SLOT = 'hs.mate.capturedKey'

/* Unverified on purpose. The design ships the slug as a configurable default and makes
   the first call surface the real API error, so a wrong slug is a one-line fix on the
   settings pane rather than a debugging session. */
export const DEFAULT_MODEL = 'google/gemini-flash-3.8'

/* The OpenRouter URL is deliberately NOT here. openrouter.js owns it, along with the headers
   and the error mapping, and validateKey lives there too -- one module that knows what an
   OpenRouter request and an OpenRouter error body look like, so the settings pane and the dock
   cannot classify the same failure two different ways. */

const NOSTORE = { getItem: () => null, setItem: () => {}, removeItem: () => {} }

/** Read through a Storage that may not work. A browser with site data blocked, or a
 *  private window, throws on access; a student there gets a narrator that does not
 *  narrate, never a white screen. */
const get = (s, k) => { try { return s.getItem(k) } catch { return null } }
const put = (s, k, v) => { try { s.setItem(k, v) } catch { /* storage blocked */ } }
const del = (s, k) => { try { s.removeItem(k) } catch { /* storage blocked */ } }

/** The two real Storages, or inert stand-ins where they are unavailable — node during a
 *  test, or a browser with site data blocked. */
export function browserStores() {
  const pick = (name) => {
    try { const s = globalThis[name]; s.getItem(KEY_SLOT); return s } catch { return NOSTORE }
  }
  return { session: pick('sessionStorage'), local: pick('localStorage') }
}

/** The same shape the backend already shows for the lab's own key (app.py get_settings), so the
 *  two lines on the settings pane read alike. */
export function keyHint(key) {
  const k = String(key || '')
  return k.length > 14 ? k.slice(0, 7) + '…' + k.slice(-4) : ''
}

export function readMate(stores) {
  const key = String(get(stores.local, KEY_SLOT) || get(stores.session, KEY_SLOT) || '')
  const model = String(get(stores.local, MODEL_SLOT) || '') || DEFAULT_MODEL
  return { key, keyHint: keyHint(key), keyPresent: key.length > 0, model,
           captured: get(stores.session, CAPTURED_SLOT) === '1' && get(stores.session, CAPTURED_KEY_SLOT) === key }
}

export function isKeyRemembered(stores) { return !!get(stores.local, KEY_SLOT) }

export function writeKey(stores, raw, remember = isKeyRemembered(stores)) {
  const key = String(raw || '').trim()
  // A DIFFERENT key is a different consent. The captured opt-in is granted against the key that was
  // in the page when it was ticked -- forgetKey has always said so and cleared it -- but replacing
  // the key left the tick standing, so a key pasted later inherited permission nobody gave it.
  // Re-pasting the same key is not a change and keeps it.
  const previous = readMate(stores).key
  if (!key) { forgetKey(stores); return '' }
  const target = remember ? stores.local : stores.session
  put(target, KEY_SLOT, key)
  // Keep the old copy when the target rejects a write. The UI verifies storage
  // before claiming success; a failed persistence change must not lose the key.
  if (get(target, KEY_SLOT) !== key) return key
  del(remember ? stores.session : stores.local, KEY_SLOT)
  if (key !== previous) { del(stores.session, CAPTURED_SLOT); del(stores.session, CAPTURED_KEY_SLOT) }
  return key
}

export function forgetKey(stores) {
  del(stores.local, KEY_SLOT)
  del(stores.session, KEY_SLOT)
  del(stores.session, CAPTURED_SLOT)   // consent does not outlive the key it was given with
  del(stores.session, CAPTURED_KEY_SLOT)
}

export function writeModel(stores, raw) {
  const model = String(raw || '').trim()
  if (!model) { del(stores.local, MODEL_SLOT); return DEFAULT_MODEL }
  put(stores.local, MODEL_SLOT, model)
  return model
}

export function setCaptured(stores, on) {
  // Bind consent to the exact key, so a remembered-key change in another tab
  // cannot silently inherit this tab's permission to send captured text.
  if (on) { put(stores.session, CAPTURED_KEY_SLOT, readMate(stores).key); put(stores.session, CAPTURED_SLOT, '1') }
  else { del(stores.session, CAPTURED_SLOT); del(stores.session, CAPTURED_KEY_SLOT) }
  return !!on
}
