// Tidy radial layout: sections get evenly spaced angular slots in domain -> topic order (with gaps between groups);
// each parent sits at the angular centre of its children; calculators sit on an outer ring next to what they read.
import {calculators, domains} from '../data/northwindKb';
import type {Pt} from './lightning';

export type Placed = Pt & {angle: number; level: number; id: string; title: string; parent: string | null};

export const radialLayout = (cx: number, cy: number, radii: [number, number, number, number]) => {
  const slots: Record<string, number> = {};
  let next = 0;
  for (const d of domains) {
    const ds: number[] = [];
    for (const t of d.topics) {
      const ts = t.sections.map((s) => (slots[s.id] = next++));
      slots[t.id] = (ts[0] + ts[ts.length - 1]) / 2;
      ds.push(slots[t.id]);
      next += 0.9; // gap between topics
    }
    slots[d.id] = (Math.min(...ds) + Math.max(...ds)) / 2;
    next += 1.8; // gap between domains
  }
  const total = next;
  const ang = (slot: number) => (slot / total) * Math.PI * 2 - Math.PI / 2;
  const place = (id: string, title: string, level: number, parent: string | null, r: number, a: number): Placed =>
    ({id, title, level, parent, angle: a, x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r});

  const nodes: Record<string, Placed> = {ROOT: {id: 'ROOT', title: 'Knowledge base', level: 0, parent: null, angle: 0, x: cx, y: cy}};
  for (const d of domains) {
    nodes[d.id] = place(d.id, d.title, 1, 'ROOT', radii[0], ang(slots[d.id]));
    for (const t of d.topics) {
      nodes[t.id] = place(t.id, t.title, 2, d.id, radii[1], ang(slots[t.id]));
      for (const s of t.sections) nodes[s.id] = place(s.id, s.title, 3, t.id, radii[2], ang(slots[s.id]));
    }
  }
  for (const c of calculators) {
    nodes[`C:${c.id}`] = place(`C:${c.id}`, c.title, 4, null, radii[3], ang(slots[c.reads[0]] + 1.2)); // next to its main input
  }
  return nodes;
};

/** A smooth radial link from a parent to a child (control points on the mid radius). */
export const radialLink = (a: Placed, b: Placed, cx: number, cy: number) => {
  const ra = Math.hypot(a.x - cx, a.y - cy);
  const rb = Math.hypot(b.x - cx, b.y - cy);
  const rm = (ra + rb) / 2;
  const aa = a.level === 0 ? b.angle : a.angle;
  const c1 = {x: cx + Math.cos(aa) * rm, y: cy + Math.sin(aa) * rm};
  const c2 = {x: cx + Math.cos(b.angle) * rm, y: cy + Math.sin(b.angle) * rm};
  return `M${a.x.toFixed(1)} ${a.y.toFixed(1)} C${c1.x.toFixed(1)} ${c1.y.toFixed(1)} ${c2.x.toFixed(1)} ${c2.y.toFixed(1)} ${b.x.toFixed(1)} ${b.y.toFixed(1)}`;
};
