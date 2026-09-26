// Theme state lives on <html data-theme>. It is set synchronously (before setState) so the first render
// after a toggle already reads the right tokens.
const KEY = 'hs.theme'
export const THEMES = ['dark', 'light']
export function currentTheme() { return document.documentElement.dataset.theme || 'dark' }
export function applyTheme(name) {
  document.documentElement.dataset.theme = THEMES.includes(name) ? name : 'dark'
  try { localStorage.setItem(KEY, document.documentElement.dataset.theme) } catch { /* private mode */ }
}
export function storedTheme() { try { return localStorage.getItem(KEY) } catch { return null } }
export function initTheme() { applyTheme(storedTheme() || 'dark') }
