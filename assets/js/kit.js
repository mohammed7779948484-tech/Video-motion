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
  window.SYRAZEN_KIT = { rng: rng, cue: cue };
})();
