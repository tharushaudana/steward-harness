// Seeded lightning bolts: midpoint displacement along a polyline, plus a few short forks.

export type Pt = {x: number; y: number};

export const rng = (seed: number) => {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
};

const jag = (a: Pt, b: Pt, rough: number, depth: number, r: () => number): Pt[] => {
  if (depth === 0) return [a, b];
  const len = Math.hypot(b.x - a.x, b.y - a.y);
  const nx = -(b.y - a.y) / (len || 1);
  const ny = (b.x - a.x) / (len || 1);
  const off = (r() - 0.5) * len * rough;
  const m = {x: (a.x + b.x) / 2 + nx * off, y: (a.y + b.y) / 2 + ny * off};
  const left = jag(a, m, rough, depth - 1, r);
  return [...left.slice(0, -1), ...jag(m, b, rough, depth - 1, r)];
};

export type Bolt = {main: string; forks: string[]; points: Pt[]};

export const toPath = (pts: Pt[]) => pts.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');

/** A bolt through `via` (e.g. root -> domain -> topic -> section). */
export const bolt = (via: Pt[], seed: number, rough = 0.32, depth = 5, forks = 2): Bolt => {
  const r = rng(seed);
  let pts: Pt[] = [via[0]];
  for (let i = 1; i < via.length; i++) pts = [...pts, ...jag(via[i - 1], via[i], rough, depth, r).slice(1)];
  const out: string[] = [];
  for (let k = 0; k < forks; k++) {
    const i = Math.floor(pts.length * (0.25 + 0.5 * r()));
    const p = pts[i];
    const q = pts[Math.min(i + 3, pts.length - 1)];
    const ang = Math.atan2(q.y - p.y, q.x - p.x) + (r() < 0.5 ? -1 : 1) * (0.5 + 0.5 * r());
    const len = 40 + 70 * r();
    const end = {x: p.x + Math.cos(ang) * len, y: p.y + Math.sin(ang) * len};
    out.push(toPath(jag(p, end, 0.45, 3, r)));
  }
  return {main: toPath(pts), forks: out, points: pts};
};
