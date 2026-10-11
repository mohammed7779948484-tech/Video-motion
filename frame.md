---
name: SYRAZEN — Open All Night
canvas: 1080x1920
colors:
  night-900: "#0B0E18"   # top of night sky
  night-800: "#121728"   # night sky
  night-700: "#1C2236"   # far skyline, bubble fill
  night-600: "#262C42"   # neighbour buildings (lit side)
  dusk-glow: "#C2643E"   # horizon glow at 0s (sunset)
  dawn-sky:  "#34456E"   # upper sky at sunrise (cream text must stay AA on it)
  dawn-glow: "#F2A65A"   # horizon glow at sunrise
  facade:    "#6B4A3C"   # the shop's warm terracotta-brown front
  facade-dk: "#4E352B"   # facade shading / door frame
  amber:     "#FFB547"   # interior light, lamps, phone glow, competitor glow
  cream:     "#F6EFE3"   # headlines, website surface, end-card background
  ink:       "#14161F"   # text on cream
  ink-dim:   "#5E5A66"   # secondary text on cream
  vermilion: "#FF4A1C"   # SYRAZEN accent: the line, CTA, OPEN 24/7, underline (only appears with the website / brand)
typography:
  display:  { family: "Archivo", src: "assets/fonts/Archivo-Variable.woff2", stretch: "112%", weight: 800, tracking: "-0.025em", lineHeight: 1.0 }
  wordmark: { family: "Archivo", stretch: "125%", weight: 800, tracking: "0.02em" }
  ui:       { family: "Archivo", stretch: "100%", weight: "500-700" }
  data:     { family: "JetBrains Mono", src: "assets/fonts/JetBrainsMono-Variable.woff2", weight: 500, note: "clock, timestamps, service line only" }
spacing: { margin: 96, titleSafeX: [108, 972], titleSafeY: [192, 1728], radiusCard: 28, radiusPill: 999 }
---

# SYRAZEN — "Open All Night" frame spec

## Overview
A premium, flat-vector night street in a vertical frame. One warm shopfront ("YOUR BUSINESS")
closes at 6pm, misses the customers who keep searching at night, then a vermilion line turns the
shop into its own website — which stays open, catches those customers, and greets the owner with
new orders at sunrise. Audience: non-technical owners of businesses with no website. Sell the
outcome (found, open 24/7, customers while you sleep), never the process (no code, no wireframes).

## Look
- **Flat vector illustration with light.** Solid shapes, no outlines, no gradients on large flat
  areas except the sky (radial glows, never full-screen linear gradients — they band in H.264).
  Depth comes from light: warm amber interior spill, soft radial glows (`filter: blur` on glow
  shapes, or radial-gradient), and soft contact shadows. Subtle grain allowed on the sky only.
- **Color story.** The world is night blues + warm terracotta/amber. **Vermilion is SYRAZEN's
  colour and only appears when the website / brand appears** (the line, CTA buttons, OPEN 24/7
  badge, wordmark underline). Never vermilion on the physical shop.
- **Type.** Archivo (local woff2, OFL) for everything people read; JetBrains Mono only for
  clock/timestamps/service line. Headlines: 100px, stretch 112%, weight 800, cream, left-aligned in
  the headline zone (x 96, y 300, two lines). Minimum text size 26px.
- **Safe zones.** Key text inside x 108–972 / y 192–1728; nothing important below y 1640 (platform UI).

## Composition rules
- Every sub-composition is prefixed: ids and CSS selectors start with its scene prefix
  (`env-`, `sh-`, `wb-`, `sy-`, `lk-`) because all scene CSS is global after assembly. Each scene root uses a
  unique id (`#env-root`, `#sh-root`, `#wb-root`, `#sy-root`, `#lk-root`), never `#root`.
- `@font-face` must be declared inside each sub-composition `<template>` `<style>` with
  `url("../assets/fonts/…")` (lint requires in-file font-face).
- Timing comes only from `window.SYRAZEN.cues` (assets/js/cues.js, generated from tools/cues.json).
- Motion: one paused GSAP timeline per scene, `fromTo` only, absolute positions, no
  `Math.random`/`Date.now` (seeded PRNG in `window.SYRAZEN_KIT`), ambient loops finite.
