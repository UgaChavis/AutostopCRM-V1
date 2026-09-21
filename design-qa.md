# Design QA — Центр автоматизаций

- Source visual truth: `/root/.codex/attachments/2d4e600a-c050-4ec8-b93b-7ca2443fdcef/codex-clipboard-c31a5e78-8869-4531-8e30-85474e20739c.png`
- Implementation screenshots: `/tmp/autostop-automation-map.png`, `/tmp/autostop-automation-drawer-desktop.png`, `/tmp/autostop-automation-drawer-mobile.png`
- Combined comparison: `/tmp/autostop-automation-style-comparison.png`
- Desktop viewport / screenshot: 1440 × 900 CSS px / 1440 × 900 px, device scale factor 1
- Mobile viewport / screenshot: 390 × 844 CSS px / 390 × 844 px, device scale factor 1
- States: authenticated map; healthy admin drawer with enabled and disabled jobs; mobile fullscreen drawer

## Full-view comparison evidence

The reference is a cropped map rather than a complete viewport, so exact topology scale is not a fidelity target. The combined comparison verifies the requested visual language: dark graphite canvas, restrained single-color module outlines, compact outlined ID badges, thin orthogonal wires, Segoe UI typography, and muted secondary labels. G1 uses the same card, badge, icon, wire, and lamp grammar as the existing map.

## Focused region comparison evidence

The desktop drawer preserves the map palette and border treatment while keeping the diagram visible beneath a dark backdrop. The 390 × 844 capture confirms the same hierarchy becomes a true fullscreen panel without horizontal clipping. The reference has no drawer state, so this focused comparison checks consistency with its tokens rather than pixel identity.

## Required fidelity surfaces

- Fonts and typography: existing Segoe UI stack, weights, line heights, and compact label hierarchy are preserved; mobile headings wrap without clipping.
- Spacing and layout rhythm: G1 aligns with nearby modules; desktop cards use the map's compact density; mobile controls retain usable spacing and the drawer scrolls vertically.
- Colors and visual tokens: existing background, panel, border, group-color, muted-text, green, yellow, and red semantics are reused without a new decorative palette.
- Image and icon fidelity: no raster replacement or generated asset was introduced; G1 reuses the map's existing bell icon system.
- Copy and content: Russian labels clearly separate required mode from actual state and keep system timers read-only.

## Findings

No actionable P0, P1, or P2 visual differences were found. The full-map implementation intentionally shows more topology than the cropped reference and adds the requested G1 module and drawer.

## Comparison history

- Pass 1: no actionable P0/P1/P2 mismatch. Desktop and mobile interaction states remained legible and consistent with the reference, so no visual correction loop was required.

## Interaction and runtime evidence

- Opening G1, toggling a job, editing its interval, creating a disabled job from a typed template, Escape close, backdrop close, and focus restoration passed in Playwright.
- Admin, non-admin, stale, applying, offline, enabled, disabled, and mobile fullscreen states were exercised by focused browser tests.
- Console assertion in the authenticated browser regression suite remained clean.

final result: passed
