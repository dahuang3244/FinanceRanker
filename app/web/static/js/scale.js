/* ==========================================================================
   Score colour scale — single source of truth.

   The dimension matrix scores are a *monotonic* 1–10 percentile rank, but the
   chosen encoding is a diverging red↔green scale: 5.5 is the neutral axis,
   red means weak, green means strong, and saturation encodes how far the score
   sits from that axis.

   Keeping this in one module matters because the same score is rendered in
   three places (overview swatches, ranking/history meter bars, ticker drawer).
   If each derived its own colours they would drift apart.

   Accessibility: the numeric value is always rendered next to the colour, so
   the table stays readable for red/green colour blindness. Colour is a scan
   aid, never the only carrier of the value.
   ========================================================================== */
"use strict";

const FRScale = (() => {
  // Neutral axis. Chosen as 5.5 because the 1–10 percentile scale has no true
  // midpoint (each peer is ranked against the pool), and 5.5 reads as
  // "middle-ish but slightly below average" which matches how the thresholds in
  // FR.tone() were already drawn.
  const MID = 5.5;
  // The axis is off-centre in [0, 10], so the two directions need their own
  // denominators. A single span made the distances asymmetric: -5.5/5.5 = -1 but
  // +4.5/5.5 = 0.82, so the green pole never reached full saturation and scores
  // 9 and 10 rendered almost identically.
  const SPAN_LOW = MID;          // 5.5 -> maps score 0   to -1
  const SPAN_HIGH = 10 - MID;    // 4.5 -> maps score 10  to +1

  // Standard red and green as the two poles, muted so they sit on the warm
  // paper background instead of shouting over it.
  const RED = [176, 58, 48];
  const GREEN = [38, 122, 72];
  // Neutral colours are darker than the paper background so the pale middle
  // still holds a mid-to-dark value; dark ink on a light wash was only 3.2:1.
  const NEUTRAL = [150, 140, 126];       // unused by swatch, kept for reference
  // Dark enough that white ink clears WCAG AA (4.5:1) even at its lightest,
  // which lets every cell use the same ink and avoids a per-cell contrast cliff.
  const NEUTRAL_DARK = [120, 112, 101];  // weakest colour actually used

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const mix = (a, b, t) => [
    a[0] + (b[0] - a[0]) * t,
    a[1] + (b[1] - a[1]) * t,
    a[2] + (b[2] - a[2]) * t,
  ];
  const rgb = (c) => `rgb(${Math.round(c[0])}, ${Math.round(c[1])}, ${Math.round(c[2])})`;
  const rgba = (c, a) => `rgba(${Math.round(c[0])}, ${Math.round(c[1])}, ${Math.round(c[2])}, ${a.toFixed(2)})`;

  /** How far a score sits from neutral, normalised to -1..+1 (asymmetric-safe). */
  function distance(score) {
    const s = clamp(Number(score), 0, 10);
    const delta = s - MID;
    return clamp(delta >= 0 ? delta / SPAN_HIGH : delta / SPAN_LOW, -1, 1);
  }

  /**
   * Base colour for a score. Near the axis the colour is darkened toward
   * NEUTRAL_DARK so the pale middle does not wash out; the two ends stay at the
   * full red / green poles.
   */
  function base(score) {
    const d = distance(score);
    const target = d >= 0 ? GREEN : RED;
    return mix(NEUTRAL_DARK, target, Math.abs(d));
  }

  function tone(score) {
    const d = distance(score);
    if (d >= 0.35) return "good";
    if (d > 0) return "good-soft";
    if (d <= -0.35) return "low";
    if (d < 0) return "low-soft";
    return "flat";
  }

  /** Strong, readable colour for bars, fills and small marks. */
  function fill(score) {
    return rgb(base(score));
  }

  /**
   * Swatch appearance: background + text colour.
   * `uniform` keeps every cell at full opacity so a column can be compared by
   * hue alone; that is what makes this an encoding rather than a gradient.
   */
  function swatch(score, { uniform = true } = {}) {
    const d = distance(score);
    const shade = base(score);
    const alpha = uniform ? 1 : 0.22 + Math.abs(d) * 0.78;
    // White ink throughout: NEUTRAL_DARK was chosen so even the weakest colour
    // clears 4.5:1 against white, so no cell needs a different ink.
    return { background: rgba(shade, alpha), color: "#ffffff" };
  }

  /** A row of legend entries from 0..10 for the page header. */
  function legendSteps() {
    return Array.from({ length: 11 }, (_, i) => i);
  }

  return { MID, distance, tone, fill, swatch, legendSteps, RED, GREEN, NEUTRAL, NEUTRAL_DARK };
})();
