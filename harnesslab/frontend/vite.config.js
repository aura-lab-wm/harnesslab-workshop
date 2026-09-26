import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

/* Stamp the source the bundle was built from into dist/build-source.json. The server serves dist/
   as committed, so an edit to src/ without a rebuild silently ships the old UI;
   tests_agentlab/test_dist_current.py recomputes this hash and fails when they disagree.
   Same file set and framing as the test: every non-test file under src/, plus index.html and
   package.json, sorted by POSIX path, each hashed as  path \0 bytes \0. */
function sourceFiles(root) {
  const out = []
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name)
      if (statSync(p).isDirectory()) walk(p)
      else if (!/\.test\.[jt]sx?$/.test(name) && name !== '.DS_Store') out.push(p)
    }
  }
  walk(join(root, 'src'))
  out.push(join(root, 'index.html'), join(root, 'package.json'))
  return out.map((p) => relative(root, p).split(sep).join('/')).sort()
}
export function sourceHash(root) {
  const h = createHash('sha256')
  const files = sourceFiles(root)
  for (const f of files) { h.update(f + '\0'); h.update(readFileSync(join(root, f))); h.update('\0') }
  return { sha256: h.digest('hex'), files: files.length }
}
const stampSource = () => ({
  name: 'harnesslab-stamp-source',
  apply: 'build',
  writeBundle(opts) {
    const root = process.cwd()
    writeFileSync(join(opts.dir || join(root, 'dist'), 'build-source.json'), JSON.stringify(sourceHash(root), null, 1) + '\n')
  },
})

export default defineConfig({
  // the bundle knows which source it was built from, so an open tab can tell when the server has a newer one
  define: { __HL_BUILD__: JSON.stringify(sourceHash(process.cwd()).sha256) },
  plugins: [react(), tailwindcss(), stampSource()],
  server: { port: 5173, proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true }, '/field': { target: 'http://127.0.0.1:8765', changeOrigin: true } } },
  build: { outDir: 'dist', emptyOutDir: true },
  test: { environment: 'node', include: ['src/**/*.test.{js,jsx}'] },
})
