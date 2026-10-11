/* SYRAZEN shared kit — deterministic helpers shared by every scene (loaded once in index.html). */
(function () {
  // Seeded PRNG (mulberry32): same seed -> same sequence on every render.
  function rng(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  // Cue lookup in absolute film time; offset converts to a scene's local time.
  function cue(name, index, offset) {
    var v = window.SYRAZEN.cues[name];
    if (v === undefined) throw new Error("SYRAZEN: unknown cue " + name);
    if (Array.isArray(v)) v = v[index || 0];
    return v - (offset || 0);
  }

  // The seam between the shop (below) and the website (above), in px from the top of the frame,
  // as one pure function of film time. shop.html and website.html each drive their own --split
  // from this, so the two layers can never drift apart.
  var splitEase = null;
  function splitAt(t) {
    var L0 = cue("lineDrop", 0),
      L1 = cue("lineDrop", 1);
    if (t < L0) return 600;
    if (t < L1) {
      splitEase = splitEase || gsap.parseEase("power2.inOut");
      return 600 + 1000 * splitEase((t - L0) / (L1 - L0));
    }
    if (t < L1 + 0.05) return 1600;
    return 1920;
  }

  // Time-pure driver: calls fn(t) with film time t (0..end) on every seek of tl.
  function driver(tl, end, fn) {
    var d = { t: 0 };
    tl.fromTo(d, { t: 0 }, { t: end, duration: end, ease: "none", onUpdate: function () { fn(d.t); } }, 0);
  }

  // Camera on a world wrapper (transform-origin 540px 1100px in the scene CSS): an opening settle
  // so frame 0 is already moving, then a slow push on the shop through the lonely night, released
  // by the line. shop.html and website.html both call this so their two layers stay locked.
  // The scene passes its own element in (no DOM lookups here).
  function cameraPush(tl, el) {
    var L0 = cue("lineDrop", 0),
      L1 = cue("lineDrop", 1);
    tl.fromTo(el, { scale: 1.06 }, { scale: 1, duration: 0.95, ease: "power2.out" }, 0);
    tl.fromTo(el, { scale: 1 }, { scale: 1.035, duration: L0 - 1.0, ease: "sine.inOut", immediateRender: false }, 1.0);
    tl.fromTo(el, { scale: 1.035 }, { scale: 1, duration: L1 - L0, ease: "power2.inOut", immediateRender: false }, L0);
  }

  // Goods on the shelves: the same geometry in the shop window and the website hero (420x420 box).
  var GOODS = [
    // shelf 1 (bottom y 175)
    { x: 30, y: 89, w: 62, h: 86, r: "14px 14px 10px 10px", c: "#b5704f" },
    { x: 26, y: 78, w: 70, h: 16, r: "6px", c: "#efe3cf" },
    { x: 106, y: 65, w: 54, h: 110, r: "12px", c: "#7c8a5c" },
    { x: 112, y: 56, w: 42, h: 14, r: "5px", c: "#efe3cf" },
    { x: 178, y: 125, w: 64, h: 50, r: "6px 6px 14px 14px", c: "#a8643e" },
    { x: 168, y: 62, w: 52, h: 52, r: "50%", c: "#7c8a5c" },
    { x: 200, y: 50, w: 50, h: 50, r: "50%", c: "#8e9c6a" },
    { x: 184, y: 92, w: 56, h: 40, r: "50%", c: "#6d7a50" },
    { x: 266, y: 119, w: 74, h: 56, r: "6px", c: "#efe3cf" },
    { x: 276, y: 77, w: 54, h: 44, r: "6px", c: "#d9a441" },
    { x: 356, y: 55, w: 36, h: 120, r: "14px 14px 6px 6px", c: "#d9a441" },
    { x: 364, y: 40, w: 20, h: 20, r: "4px", c: "#8a5a3c" },
    // shelf 2 (bottom y 345)
    { x: 40, y: 270, w: 116, h: 70, r: "58px 58px 6px 6px", c: "#efe3cf" },
    { x: 92, y: 255, w: 12, h: 18, r: "6px", c: "#efe3cf" },
    { x: 70, y: 338, w: 56, h: 8, r: "3px", c: "#8a5a3c" },
    { x: 176, y: 291, w: 96, h: 54, r: "48px 48px 12px 12px", c: "#c98b7a" },
    { x: 192, y: 300, w: 64, h: 8, r: "4px", c: "#e7b3a2" },
    { x: 290, y: 301, w: 30, h: 44, r: "8px", c: "#b5704f" },
    { x: 326, y: 301, w: 30, h: 44, r: "8px", c: "#7c8a5c" },
    { x: 362, y: 301, w: 30, h: 44, r: "8px", c: "#d9a441" },
  ];
  var SHELVES = [175, 345];

  window.SYRAZEN_KIT = {
    rng: rng,
    cue: cue,
    splitAt: splitAt,
    driver: driver,
    cameraPush: cameraPush,
    GOODS: GOODS,
    SHELVES: SHELVES,
  };
})();
