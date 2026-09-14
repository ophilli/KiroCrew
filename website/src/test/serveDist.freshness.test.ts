/**
 * `assertDistFresherThanSources` in `scripts/lib/serve-dist.mjs` — the guard
 * that refuses to screenshot a `dist` older than the sources it was built from.
 *
 * The guard exists because a stale bundle does not fail: it photographs the
 * previous UI with perfectly plausible frames, and that once cost hours of
 * review chasing a "missing" surface that was simply not built yet. What this
 * file pins is the guard's SUBJECT: only files that can change `npm run build`'s
 * output may trip it. The first version compared against the newest file
 * anywhere under `src/`, so saving a `*.test.tsx` — which vite never bundles
 * (tsconfig.app.json excludes it) — aborted every capture until a pointless
 * rebuild. A guard that fires on non-inputs gets worked around, and a
 * worked-around guard protects nothing.
 *
 * Both directions are locked, so neither can regress silently:
 *  (1) a test-only / fixture / style-guide edit newer than dist is NOT stale;
 *  (2) a real bundle input (component, locale JSON, CSS, asset) newer than dist
 *      IS stale, and the error names the offending file.
 *
 * Driven through a real `node` child process for the same reason
 * `serveDist.routes.test.ts` is: the module resolves its defaults from
 * `import.meta.url`, which the test runner's transform rewrites into a URL
 * `fileURLToPath` rejects. The child builds a throwaway dist + src tree and
 * sets mtimes explicitly with `utimesSync`, so the assertions do not depend on
 * how fast the filesystem is or what the real `website/dist` looks like.
 */
import { describe, it, expect } from 'vitest'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const WEBSITE = resolve(__dirname, '..', '..')

/**
 * One scenario = files (relative to a fresh src root) that are made NEWER than
 * dist/index.html. Everything else in the tree is older than the build.
 */
const SCENARIOS: Record<string, string[]> = {
  'all-older': [],
  'test-dir-only': ['test/setup.ts', 'test/fixtures/skills.json', 'test/__mocks__/api.ts'],
  'colocated-test-only': ['pages/overview/SkillsTab.test.tsx', 'lib/util.test.ts'],
  'style-guide-only': ['i18n/style/de.md', 'i18n/TRANSLATION-PROMPT.md'],
  'component-edited': ['pages/overview/SkillsTab.tsx'],
  'locale-json-edited': ['i18n/locales/de.json'],
  'css-edited': ['index.css'],
  'asset-edited': ['assets/logo.svg'],
  'component-and-test-edited': ['pages/overview/SkillsTab.test.tsx', 'pages/overview/SkillsTab.tsx'],
}

interface Outcome {
  error: string | null
}

/** Run every scenario in one child process; return {name: {error}}. */
function run(): Record<string, Outcome> {
  const dir = mkdtempSync(join(tmpdir(), 'serve-dist-fresh-'))
  const script = join(dir, 'probe.mjs')
  const moduleUrl = pathToFileURL(join(WEBSITE, 'scripts/lib/serve-dist.mjs')).href
  // Baseline tree: one of everything the guard must SKIP and one of everything
  // it must COUNT, all stamped older than the build. A scenario then bumps its
  // listed files to newer-than-build, so a false positive shows up as a stale
  // verdict on a non-input and a false negative as silence on a real one.
  const BASELINE = [
    'main.tsx', 'index.css', 'pages/overview/SkillsTab.tsx', 'pages/overview/SkillsTab.test.tsx',
    'lib/util.ts', 'lib/util.test.ts', 'i18n/locales/de.json', 'i18n/style/de.md',
    'i18n/TRANSLATION-PROMPT.md', 'assets/logo.svg', 'test/setup.ts',
    'test/fixtures/skills.json', 'test/__mocks__/api.ts',
  ]
  writeFileSync(script, `
import { mkdirSync, writeFileSync, utimesSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { assertDistFresherThanSources } from ${JSON.stringify(moduleUrl)}
const root = ${JSON.stringify(dir)}
const scenarios = ${JSON.stringify(SCENARIOS)}
const baseline = ${JSON.stringify(BASELINE)}
const BUILT = 1_700_000_000        // seconds; arbitrary fixed epoch
const OLDER = BUILT - 3600
const NEWER = BUILT + 3600
const touch = (path, at) => {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, '')
  utimesSync(path, at, at)
}
const out = {}
for (const [name, newer] of Object.entries(scenarios)) {
  const dist = join(root, name, 'dist')
  const src = join(root, name, 'src')
  touch(join(dist, 'index.html'), BUILT)
  for (const rel of baseline) touch(join(src, rel), OLDER)
  for (const rel of newer) touch(join(src, rel), NEWER)
  try {
    assertDistFresherThanSources(dist, src)
    out[name] = { error: null }
  } catch (e) {
    out[name] = { error: String(e && e.message || e) }
  }
}
process.stdout.write(JSON.stringify(out))
`)
  try {
    return JSON.parse(execFileSync(process.execPath, [script], { encoding: 'utf8', timeout: 30_000 }))
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

const RESULTS = run()

describe('assertDistFresherThanSources — only bundle inputs count', () => {
  it('serves when nothing under src is newer than the build', () => {
    expect(RESULTS['all-older'].error).toBeNull()
  })

  it('does NOT trip on edits under src/test (setup, fixtures, __mocks__)', () => {
    // tsconfig.app.json excludes src/test wholesale; vite never bundles it.
    expect(RESULTS['test-dir-only'].error).toBeNull()
  })

  it('does NOT trip on a colocated *.test.ts(x) edit', () => {
    // The original false positive: saving a spec aborted every capture.
    expect(RESULTS['colocated-test-only'].error).toBeNull()
  })

  it('does NOT trip on translator style guides (*.md under src/i18n)', () => {
    expect(RESULTS['style-guide-only'].error).toBeNull()
  })

  it('DOES trip on a component newer than the build, naming the file', () => {
    const { error } = RESULTS['component-edited']
    expect(error).toMatch(/STALE BUILD/)
    expect(error).toMatch(/SkillsTab\.tsx/)
    expect(error).not.toMatch(/SkillsTab\.test\.tsx/)
  })

  it('DOES trip on a locale catalogue, a stylesheet and an asset', () => {
    // Every non-code bundle input still counts: locale JSON is imported, CSS is
    // processed, SVG is emitted. Narrowing must not have quietly become
    // "TypeScript only".
    expect(RESULTS['locale-json-edited'].error).toMatch(/STALE BUILD/)
    expect(RESULTS['css-edited'].error).toMatch(/STALE BUILD/)
    expect(RESULTS['asset-edited'].error).toMatch(/STALE BUILD/)
  })

  it('names the bundled file, not the newer test beside it', () => {
    // Both are newer than the build; only the component is a reason to rebuild,
    // and the message must point at it so the reader rebuilds for the right
    // cause rather than concluding the guard fires on tests.
    const { error } = RESULTS['component-and-test-edited']
    expect(error).toMatch(/STALE BUILD/)
    expect(error).toMatch(/SkillsTab\.tsx/)
    expect(error).not.toMatch(/SkillsTab\.test\.tsx/)
  })
})
