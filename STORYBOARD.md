---
format: 1080x1920
duration: 15s
message: "Your shop closes. A SYRAZEN website keeps you open — and wakes you up to new customers."
arc: Recognition → Loss → Turn → Outcome → Brand
audience: Non-technical business owners without a professional website
mode: autonomous
music: original procedural score, 120 BPM, F minor → Ab major at the turn
---

## Frame 1 — Closing time (hook)
- scene: A warm lit shopfront, "YOUR BUSINESS", at dusk. 17:59 → 18:00: the door sign flips to CLOSED, three lights click off.
- duration: 2.6s
- poster: 1.4s
- transition_in: cut
- status: animated
- src: compositions/shop.html (+ story.html for copy, environment.html for the world)
- rules: spring-pop-entrance (sign swing settle), 3d flip via rotationY, ambient-glow-bloom (window light)

"Your doors / close at 6." The owner recognises their own evening in the first second.

## Frame 2 — Customers keep looking (pain)
- scene: Night. Phone glows light up in neighbouring windows; search bubbles pop ("open now?", "near me?", "book today?", "prices?", "anyone open?") then drift off to a competitor glowing at the right edge.
- duration: 2.8s
- poster: 3.9s
- transition_in: crossfade (headline swap)
- status: animated
- src: compositions/story.html (+ shop.html, environment.html for sky, glows, competitor)
- rules: spring-pop-entrance (bubbles), nudge-curve (drift away), sine-wave-loop (glow breathing)

"Customers / keep looking." → "…and find / someone else."

## Frame 3 — The turn
- scene: Hush, then a vermilion line sweeps down the dark shop: above it the shop becomes its own glowing website (awning → header, window → hero, door → Book now / Call us / Find us, CLOSED → OPEN 24/7).
- duration: 3.2s
- poster: 7.0s
- transition_in: wipe (the line)
- status: animated
- src: compositions/website.html (+ shop.html below the line)
- rules: clip-path reveal (techniques §12), ambient-glow-bloom, svg-path-draw (customer ping paths), motion-blur-streak (line)

"A website / keeps you open." Customers' pings now fly into Book now.

## Frame 4 — Wake up to new customers (outcome)
- scene: Sunrise. Notifications stack over the site: New booking · 02:14, New order · 04:47, New enquiry · 05:52, New message · 06:10.
- duration: 2.5s
- poster: 10.8s
- transition_in: crossfade (dawn)
- status: animated
- src: compositions/website.html (+ story.html)
- rules: spring-pop-entrance (stagger ≤0.5s), waterfall-entry

## Frame 5 — SYRAZEN (brand)
- scene: The website card expands to a full cream frame; the SYRAZEN wordmark lands on the impact with its vermilion line; "Get your business online." / WEB DESIGN & DEVELOPMENT.
- duration: 3.1s
- poster: 14.0s
- transition_in: card-morph-anchor (website card → frame)
- status: animated
- src: compositions/lockup.html
- rules: card-morph-anchor, logo-assemble-lockup blueprint, ambient-glow-bloom
