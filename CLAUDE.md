# Video-motion — Claude Code Guidance

## Project
- This repository is a **code-driven motion graphics studio** built on HyperFrames. The existing root project was scaffolded with `hyperframes init blank`; **do not re-initialize or overwrite it**.
- Each new request supplies its own subject, brand, audience, and constraints. Do not inherit a concept, storyboard, palette, claims, or creative choices from previous advertisements.
- The purpose is an **original, professionally art-directed motion graphics film**, not a live-action, stock-footage, or photo-montage advertisement.

## Framework and environment
- HyperFrames is the composition/rendering framework. Read `.claude/skills/hyperframes/SKILL.md` first, follow its routing guidance, and load only the relevant domain/workflow skills from `.claude/skills/`. Skills contain the detailed API and animation rules; do not duplicate them here.
- Work within this existing repository and preserve its `index.html`, `hyperframes.json`, `meta.json`, and pinned `package.json` setup unless a change is justified by the approved production plan.
- During implementation, use the project's pinned commands: `npm run check` and `npm run render`. Verify Node.js 22+ and FFmpeg in the cloud environment before rendering; install missing dependencies only when execution is authorized.
- Final-render dependencies (fonts, code, audio, visuals) must be locally available and licensed. Do not depend on live network requests during frame rendering.

## Creative ownership
- **Invent the concept yourself.** Determine the audience insight, core message, visual hook, storytelling structure, design language, pacing, sound, and appropriate production techniques from the user's brief. Make strong editorial choices rather than asking the user to design the film for you.
- Think in **motion graphics**: designed typography, vectors, illustration, graphic systems, compositional transformations, coded interfaces, procedural 2D/3D, masks, and intentional camera/motion choreography.
- Do not use real-world stock photography, downloaded footage, random web images, or photorealistic live-action aesthetics as the film's visual foundation unless the user explicitly requests a different medium. Research references may inspire craft; they are not final assets.
- Avoid generic template reels, arbitrary decorative 3D, device carousels, empty effects, and UI that exists only to fill the frame. Motion, visuals, and sound should serve one coherent idea.
- Do not invent client work, testimonials, awards, performance figures, endorsements, or brand claims. Use only assets and factual claims the user provides or explicitly authorizes.

## Plan Mode — plan, do not produce
- In **Plan Mode**, inspect the repository and read relevant skills and docs **without modifying files, installing dependencies, scaffolding, or rendering**. The existing blank composition is a starting point, not a creative brief: its 1920×1080 size, 10-second duration, and `Title` text are placeholders.
- Own the creative exploration. Evaluate distinct directions internally and present **one strongest recommended concept**, with a concise rationale and relevant trade-offs. Do not recycle a known ad structure merely because it is familiar.
- Produce an actionable production plan that connects:
  - **Strategy:** who the ad is for, what it communicates, and why the idea is compelling.
  - **Storytelling:** opening visual hook, progression, reveal/payoff, brand/CTA, proposed on-screen copy, and approximate shot/beat timings.
  - **Art and sound direction:** visual system, motion language, typography, audio approach, and intended viewer experience.
  - **Engineering:** proposed HyperFrames scene/sub-composition boundaries, file organization, reusable components, asset generation, timeline and render approach.
  - **Verification:** preview/still inspection, mobile readability where relevant, sound sync, technical checks, production risks, and definition of done.
- Recommend duration, aspect ratio, and frame rate when the brief does not specify them; **do not silently treat the blank scaffold defaults as requirements**.
- End Plan Mode with the plan and decisions that genuinely need approval. Do not start implementation until the user accepts the plan and execution is enabled.

## Execution and verification
- After approval, implement the agreed direction with HyperFrames. Organize the film into maintainable scenes/sub-compositions and shared utilities where they add value; keep a single deterministic, seekable timeline.
- Run the framework checks, render the real output, inspect representative frames **and moving transitions**, listen to the soundtrack, correct visible/technical defects, and re-render as needed. A passing build alone does not prove design quality.
- Maintain a reproducible source project and clear final output. Keep generated intermediates separate; do not commit secrets, unnecessary caches, or oversized temporary renders.
- When a routine decision is within the approved scope, make it and proceed. Ask only for truly blocking information, required review/approval gates, or authorization before destructive/out-of-repository actions. Do not claim completion without verified output.
