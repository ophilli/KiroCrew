/**
 * Screenshot harness + geometry check for "hover is a HOVER" (fix/hover-no-row-scale).
 *
 * The branch removed hover-triggered scale/translate from 22 call sites: a rail
 * row, a Badge, a StatCard, a colour swatch and friends no longer change SIZE OR
 * POSITION under the cursor. They still change COLOUR, and press feedback
 * (whileTap / active:scale-95) was deliberately kept.
 *
 * Why a real browser rather than a unit test: jsdom mocks framer-motion
 * wholesale, so `whileHover={{ scale: 1.02 }}` renders as nothing at all there,
 * and jsdom computes no layout — `getBoundingClientRect()` returns zeros and CSS
 * `hover:scale-110` is never applied because there is no hover engine and no
 * transform resolution. A vitest assertion on this diff can therefore only
 * confirm that a className string no longer contains a token, which is the same
 * evidence as reading the diff. The claim under review is geometric, so it needs
 * a real compositor, a real pointer, and a measured rect.
 *
 * What each scene claims:
 *   1 nav-row       — hovering a rail row does not move or resize it, AND the row
 *                     still visibly reacts (its background paints). This is the
 *                     motivating case: the old 1.02 scale grew the row ~4px and
 *                     nudged its neighbours.
 *   2 nav-row-press — NEGATIVE CONTROL. Holding the pointer down on the SAME row
 *                     still shrinks it (whileTap 0.97). Without this scene the PR
 *                     reads as "all motion deleted"; with it, the frames show the
 *                     removal was scoped to hover.
 *   3 badge         — a Badge is a non-interactive status label; it no longer
 *                     grows (5%) under a cursor that cannot click it. It has no
 *                     hover paint by design, so no affordance is asserted here.
 *   4 stat-card     — a StatCard no longer lifts 2px on hover, but its border
 *                     still brightens, so the card is still legibly pointed at.
 *   5 color-swatch  — a Display-settings colour swatch no longer grows 10%; the
 *                     static scale-110 on the SELECTED swatch is untouched, so
 *                     this scene hovers an UNSELECTED one (rest scale 1).
 *
 * What makes a frame invalid — every one of these exits non-zero rather than
 * writing a PNG:
 *   - the app never rendered the surface (blank page, error boundary, stub gap);
 *   - the surface was still moving BEFORE the hover (async data reflowing the
 *     page), which would make any delta unattributable;
 *   - the rect moved during or after the hover beyond EPSILON;
 *   - the hover affordance did NOT appear where one is claimed (otherwise
 *     "geometry unchanged" is equally satisfied by a dead element);
 *   - the press control did NOT move (a mis-aimed pointer would otherwise read
 *     as "press feedback removed too").
 *
 * Usage:
 *   npm run build            # serveDist() serves website/dist, not src
 *   node scripts/capture-hover-no-geometry.mjs [outDir]
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

import { serveDist } from './lib/serve-dist.mjs'
import { logPageProblems, stubDashboardApi } from './lib/stub-dashboard-api.mjs'

/**
 * Allowed rect drift, in CSS pixels.
 *
 * 0.25px is half a DEVICE pixel at the deviceScaleFactor 2 these frames are
 * captured at, so anything under it cannot be rendered, let alone seen — it is
 * sub-pixel rounding in the compositor, not motion. It is also far below every
 * effect this branch removed, so the assertion is not merely tight, it is
 * decisive in both directions:
 *   rail row   202px x 1.02  -> +4.04px wide, x -2.02px
 *   badge       ~85px x 1.05 -> +4.2px wide
 *   swatch       28px x 1.10 -> +2.8px wide
 *   stat card   -translate-y-0.5 -> y -2.0px
 * The smallest of those is 8x the epsilon, so a restored hover transform cannot
 * hide under it, and sub-pixel noise cannot fail the run.
 */
const EPSILON = 0.25

/** Press feedback must be VISIBLE, not just non-zero: 0.97 on a 202px row is ~6px. */
const PRESS_MIN_DELTA = 1.5

const OUT = process.argv[2]
  || (process.env.KIROCREW_SCRATCH ? `${process.env.KIROCREW_SCRATCH}/evidence` : '../temp-screenshots/hover-no-geometry')

// Without a slot the chat route's own shell throws on an undefined field and the
// ErrorBoundary replaces the whole app, rail included — so the rail scenes need
// one even though chat itself is not the subject.
const SLOTS = [
  {
    key: 's1',
    title: 'Hover geometry evidence',
    messages: 2,
    running: false,
    agent: 'kirocrew',
    mode: '',
    created: '2026-09-06T01:00:00Z',
    last_ts: '2026-09-06T04:00:00Z',
    folder_id: '',
  },
]

const SCENES = [
  {
    name: '01-nav-row-hovered',
    url: '/chat',
    // The Schedule row, not Sessions: Sessions is the ACTIVE row on /chat and
    // already carries bg-accent-subtle, so a background-colour affordance check
    // there could not tell hover paint from active paint.
    selector: '.nav-item[data-onboarding-nav="schedule"]',
    claim: 'hovering a rail row paints it and does NOT move or resize it',
    affordance: 'backgroundColor',
    pad: 28,
  },
  {
    name: '02-nav-row-pressed',
    url: '/chat',
    selector: '.nav-item[data-onboarding-nav="schedule"]',
    claim: 'NEGATIVE CONTROL — pressing the same row still scales it down (whileTap 0.97)',
    press: true,
    pad: 28,
  },
  {
    name: '03-badge-hovered',
    url: '/connections',
    // The one shared Badge the stubbed world renders ("N available"). Matched on
    // the class pair the component emits rather than on its text, which is i18n.
    selector: 'span[class*="rounded-full"][class*="font-mono"]',
    claim: 'a Badge no longer grows under the cursor (it is not clickable)',
    // No affordance: Badge deliberately has NO hover paint — it is a label, and
    // announcing an affordance was the defect. Asserting one would fail here for
    // the right reason on the wrong branch.
    affordance: null,
    pad: 16,
  },
  {
    name: '04-stat-card-hovered',
    url: '/settings/overview',
    selector: '.stat-accent',
    claim: 'a StatCard no longer lifts on hover, but its border still brightens',
    affordance: 'borderTopColor',
    pad: 18,
  },
  {
    name: '05-color-swatch-hovered',
    url: '/settings/display',
    // "Color 1" is unselected in the default state, so its rest transform is
    // identity and a 10% hover growth would be unambiguous. The SELECTED
    // swatch's static scale-110 is a different, kept, effect.
    selector: 'button[aria-label="Color 1"]',
    claim: 'a colour swatch brightens but does not grow; the selected-state scale is untouched',
    // The review lanes were right that removing the scale here left these
    // buttons with NO hover cue at all, since the scale WAS the cue. They now
    // paint with `hover:brightness-110`. Asserting it is what makes this frame
    // evidence of "hover paints" rather than evidence of "hover does nothing".
    affordance: 'filter',
    pad: 46,
  },
]

mkdirSync(OUT, { recursive: true })

let failed = 0
const check = (label, ok, detail) => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${label}${detail ? ` — ${detail}` : ''}`)
  if (!ok) failed += 1
  return ok
}

/** Rect + the computed properties an affordance claim reads, in one round trip. */
const probe = (page, selector) => page.evaluate(sel => {
  const el = document.querySelector(sel)
  if (!el) return null
  const r = el.getBoundingClientRect()
  const cs = getComputedStyle(el)
  return {
    x: r.x, y: r.y, width: r.width, height: r.height,
    backgroundColor: cs.backgroundColor,
    borderTopColor: cs.borderTopColor,
    // The swatch/dot family paints its hover with a `brightness()` FILTER rather
    // than a colour class, because those buttons set `background`/`borderColor`
    // inline and a `hover:border-*` class would be dead on them. So `filter` is
    // the property that carries "you are pointing at this" there.
    filter: cs.filter,
    transform: cs.transform,
    hovered: el.matches(':hover'),
  }
}, selector)

const worstDrift = (a, b) => Math.max(
  Math.abs(a.x - b.x), Math.abs(a.y - b.y),
  Math.abs(a.width - b.width), Math.abs(a.height - b.height),
)

const fmt = r => `x=${r.x.toFixed(2)} y=${r.y.toFixed(2)} w=${r.width.toFixed(2)} h=${r.height.toFixed(2)}`

/**
 * Sample the rect repeatedly over a window and return the reading that deviates
 * MOST from `ref`.
 *
 * A single post-hover measurement would pass a surface that scaled up and
 * settled back, and "wait until it stops moving" would pass one that moved and
 * stayed moved for less than the settle window. Taking the worst reading across
 * the whole window means ANY transform, transient or persistent, fails — which
 * is the property the branch actually claims.
 */
async function worstOver(page, selector, ref, ms, step = 60) {
  let worst = ref
  let drift = 0
  for (let waited = 0; waited < ms; waited += step) {
    await page.waitForTimeout(step)
    const now = await probe(page, selector)
    if (!now) return { now: null, drift: Infinity }
    const d = worstDrift(ref, now)
    if (d >= drift) { drift = d; worst = now }
  }
  return { now: worst, drift }
}

async function openScene(browser, scene) {
  const ctx = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: 2,
    colorScheme: 'dark',
  })
  const page = await ctx.newPage()
  logPageProblems(page)
  await stubDashboardApi(page, {
    slots: SLOTS,
    // Seeded through the stub's own init script: a second addInitScript would
    // race its localStorage.clear(). mc-nav '0' keeps the rail EXPANDED, which
    // matters — the old whileHover was skipped when collapsed, so a collapsed
    // rail would make scene 1 vacuous.
    localStorageEntries: {
      'mc-color-theme': 'kiro-dark',
      'mc-privacy-notice-v1': '1',
      'mc-nav': '0',
    },
  })
  await page.goto(base + scene.url, { waitUntil: 'domcontentloaded' })
  await page.waitForFunction(
    () => document.documentElement.getAttribute('data-theme') === 'kiro-dark',
    undefined,
    { timeout: 20000 },
  )
  await page.locator(scene.selector).first().waitFor({ state: 'visible', timeout: 20000 })
  // Bring the surface into view BEFORE anything is measured. page.hover() scrolls
  // an off-screen target itself, and that scroll moves the rect by hundreds of
  // pixels — a real detection (the Display swatches sit ~2000px down) but the
  // wrong signal: it would fail the geometry assertion for a reason that has
  // nothing to do with a transform.
  await page.locator(scene.selector).first().scrollIntoViewIfNeeded()
  // Park the pointer far from every surface: an accidental hover at rest would
  // make the "at rest" baseline already-hovered and the comparison meaningless.
  await page.mouse.move(1390, 930)
  // Entry animations (animate-rise / stagger) and the first data round trip both
  // move layout; measure only once they are done.
  await page.waitForTimeout(1500)
  return { ctx, page }
}

const VIEWPORT = { width: 1400, height: 940 }

/**
 * Clip a padded box around the element, so a frame shows it plus its neighbours.
 * Clamped to the viewport: Playwright throws "Clipped area is ... outside the
 * resulting image" for a box that runs past the edge, which would abort the run
 * on a scene whose assertions had all passed.
 */
const clipFor = (rect, pad) => {
  const x = Math.max(0, Math.round(rect.x - pad))
  const y = Math.max(0, Math.round(rect.y - pad))
  return {
    x,
    y,
    width: Math.min(Math.round(rect.width + pad * 2), VIEWPORT.width - x),
    height: Math.min(Math.round(rect.height + pad * 2), VIEWPORT.height - y),
  }
}

const { srv, base } = await serveDist()
const browser = await chromium.launch({ args: ['--no-sandbox'] })

try {
  for (const scene of SCENES) {
    const { ctx, page } = await openScene(browser, scene)
    try {
      const rest = await probe(page, scene.selector)
      if (!check(`${scene.name}: surface rendered`, !!rest, scene.selector)) continue
      if (!check(`${scene.name}: not already hovered at rest`, !rest.hovered)) continue

      // Is the page itself still? A surface that drifts on its own would make
      // any post-hover delta unattributable, so refuse rather than measure it.
      const settle = await worstOver(page, scene.selector, rest, 300)
      if (!check(`${scene.name}: geometry is stable at rest`,
        settle.drift <= EPSILON, `drift ${settle.drift.toFixed(3)}px, ${fmt(rest)}`)) continue

      if (scene.press) {
        const box = await page.locator(scene.selector).first().boundingBox()
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
        await page.mouse.down()
        const held = await worstOver(page, scene.selector, rest, 600)
        check(`${scene.name}: pressed row DOES change geometry (press feedback kept)`,
          held.drift >= PRESS_MIN_DELTA,
          `drift ${held.drift.toFixed(3)}px (needs >= ${PRESS_MIN_DELTA}), rest ${fmt(rest)} -> held ${fmt(held.now)}`)
        // The direction matters: whileTap SHRINKS. A press that grew the row
        // would satisfy a bare "it moved" check while being a different bug.
        check(`${scene.name}: press shrinks rather than grows`,
          held.now.width < rest.width - EPSILON,
          `w ${rest.width.toFixed(2)} -> ${held.now.width.toFixed(2)}`)
        await page.screenshot({ path: `${OUT}/${scene.name}.png`, clip: clipFor(rest, scene.pad) })
        await page.mouse.up()
        console.log(`  ${scene.name} -> ${scene.claim}`)
        continue
      }

      await page.hover(scene.selector)
      const after = await worstOver(page, scene.selector, rest, 700)
      if (!check(`${scene.name}: still present while hovered`, !!after.now)) continue
      // `continue` rather than a bare check: a frame of a surface that DID move is
      // a screenshot of the bug, and shipping it as evidence of the fix is worse
      // than having no frame at all.
      if (!check(`${scene.name}: hovered geometry unchanged (<= ${EPSILON}px)`,
        after.drift <= EPSILON,
        `worst drift ${after.drift.toFixed(3)}px, rest ${fmt(rest)} -> hovered ${fmt(after.now)}, transform ${after.now.transform}`)) continue

      const live = await probe(page, scene.selector)
      check(`${scene.name}: pointer really is on the surface`, live.hovered,
        `:hover=${live.hovered}`)
      if (scene.affordance) {
        // The two-way half of the evidence: without this, "nothing moved" is
        // equally true of an element the cursor never reached, and of a hover
        // that was deleted outright instead of demoted to paint.
        check(`${scene.name}: hover still reads (${scene.affordance} changes)`,
          live[scene.affordance] !== rest[scene.affordance],
          `${rest[scene.affordance]} -> ${live[scene.affordance]}`)
      }

      await page.screenshot({ path: `${OUT}/${scene.name}.png`, clip: clipFor(rest, scene.pad) })
      console.log(`  ${scene.name} -> ${scene.claim}`)
    } finally {
      await ctx.close()
    }
  }
} finally {
  await browser.close()
  srv.close()
}

if (failed) {
  console.error(`${failed} assertion(s) failed — no frame here is trustworthy, not shipping these`)
  process.exit(1)
}
console.log(`\n${SCENES.length} scenes captured to ${OUT}`)
