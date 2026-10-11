# Video-motion — Claude Code Guidance

## Project
- This repository is a **code-driven motion graphics studio** built on HyperFrames. The existing root project was scaffolded with `hyperframes init blank`; **do not re-initialize or overwrite it**.
- Each new request supplies its own subject, brand, audience, and constraints. Do not inherit a concept, storyboard, palette, claims, or creative choices from previous advertisements.
- The purpose is an **original, professionally art-directed motion graphics film**, not a live-action, stock-footage, or photo-montage advertisement.

## Framework and environment
- HyperFrames is the composition/rendering framework. **Always start with `/hyperframes`** (or read `.claude/skills/hyperframes/SKILL.md` if slash invocation is unavailable). Let its router select the appropriate workflow; do not force `/motion-graphics` solely because the deliverable is a motion-graphics ad. Before authoring, load `hyperframes-core` and the relevant animation/creative/CLI skills. Consult their contracts rather than coding from generic web-animation assumptions.
- Work within this existing repository and preserve its `index.html`, `hyperframes.json`, `meta.json`, and pinned `package.json` setup unless a change is justified by the approved production plan.
- During implementation, use the project's pinned commands: `npm run check` and `npm run render`. Verify Node.js 22+ and FFmpeg in the cloud environment before rendering; install missing dependencies only when execution is authorized.
- Final-render dependencies (fonts, code, audio, visuals) must be locally available and licensed. Do not depend on live network requests during frame rendering.

## HyperFrames production invariants
- Keep the composition structure native: timed clips use `data-start` and a valid duration; full-frame visual clips use `class="clip"`; reusable scene files are mounted with `data-composition-src`. HyperFrames owns clip visibility and media playback.
- Register a **paused, seekable GSAP root timeline** under `window.__timelines[compositionId]` with the matching `data-composition-id`. Consult `hyperframes-core/references/determinism-rules.md` before implementing timelines; avoid wall-clock timers, unseeded randomness, and render-time fetches.
- While implementing, use `npx hyperframes lint` for fast feedback and `npm run check` after composition changes; resolve errors and inspect warnings. Use `npx hyperframes timeline --json` when auditing scene timing.
- For an agent-operated Studio preview, use `npx hyperframes preview --background`, verify with `npx hyperframes preview --status`, and stop with `npx hyperframes preview --stop` when appropriate. After approval and checks, render with `npm run render`; verify the resulting MP4 visually and audibly.


## Creative ownership
- **Invent the concept yourself.** Determine the audience insight, core message, visual hook, storytelling structure, design language, pacing, sound, and appropriate production techniques from the user's brief. Make strong editorial choices rather than asking the user to design the film for you.
- Think in **motion graphics**: designed typography, vectors, illustration, graphic systems, compositional transformations, coded interfaces, procedural 2D/3D, masks, and intentional camera/motion choreography.
- Do not use real-world stock photography, downloaded footage, random web images, or photorealistic live-action aesthetics as the film's visual foundation unless the user explicitly requests a different medium. Research references may inspire craft; they are not final assets.
- Avoid generic template reels, arbitrary decorative 3D, device carousels, empty effects, and UI that exists only to fill the frame. Motion, visuals, and sound should serve one coherent idea.
- Do not invent client work, testimonials, awards, performance figures, endorsements, or brand claims. Use only assets and factual claims the user provides or explicitly authorizes.

## Creative planning quality
- The blank scaffold's 1920×1080 canvas, 10-second duration, and placeholder `Title` are not creative or delivery requirements; use the user's actual brief.
- When asked to propose a film, present the strongest original concept with a coherent story arc, approximate beats, visual/audio direction, realistic HyperFrames scene and file architecture, dependencies, risks, and verification strategy. Prefer a decisive recommendation over a menu of generic options. Let Claude Code's native permission modes and approval flow govern the transition from planning to implementation.

## Execution and verification
- After approval, implement the agreed direction with HyperFrames. Organize the film into maintainable scenes/sub-compositions and shared utilities where they add value; keep a single deterministic, seekable timeline.
- Run the framework checks, render the real output, inspect representative frames **and moving transitions**, listen to the soundtrack, correct visible/technical defects, and re-render as needed. A passing build alone does not prove design quality.
- Maintain a reproducible source project and clear final output. Keep generated intermediates separate; do not commit secrets, unnecessary caches, or oversized temporary renders.
- When a routine decision is within the approved scope, make it and proceed. Ask only for truly blocking information, required review/approval gates, or authorization before destructive/out-of-repository actions. Do not claim completion without verified output.
