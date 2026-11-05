import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {calculators, domains, stats, strikes} from './data/northwindKb';
import {bolt} from './lib/lightning';
import {radialLayout, radialLink} from './lib/radialLayout';
import {fontFamily, getTheme, Props, Theme} from './theme';

const V = 1600; // the drawing is made in a 1600x1600 viewBox and scaled to the composition size
const C = V / 2;
const nodes = radialLayout(C, C, [190, 360, 540, 455]);
const NUM = ['1', '2', '3', '4'];

type Bolts = {k: number; main: string; forks: string[]}[];
const strikeBolts: Bolts = strikes.flatMap((s, k) => {
  const out: Bolts = [];
  s.used.forEach((sid, j) => {
    const sec = nodes[sid];
    const topic = nodes[sec.parent!];
    const dom = nodes[topic.parent!];
    const b = bolt([nodes.ROOT, dom, topic, sec], 1000 + k * 97 + j * 13, 0.3, 5, 2);
    out.push({k, main: b.main, forks: b.forks});
  });
  for (const cid of s.calcs) {
    const calc = nodes[`C:${cid}`];
    const reads = calculators.find((c) => c.id === cid)!.reads.filter((r) => s.used.includes(r))
      .sort((a, b) => Math.hypot(nodes[a].x - calc.x, nodes[a].y - calc.y) - Math.hypot(nodes[b].x - calc.x, nodes[b].y - calc.y))
      .slice(0, 1); // one bolt, from the nearest used section
    for (const sid of reads) {
      const b = bolt([nodes[sid], calc], 2000 + k * 31 + sid.length, 0.35, 4, 0);
      out.push({k, main: b.main, forks: []});
    }
  }
  return out;
});

/** level: 0..1 strength of each strike (1 = full flash), flash: 0..1 canvas flash */
const Drawing: React.FC<{t: Theme; levels: number[]; draw: number[]; flash: number}> = ({t, levels, draw, flash}) => {
  const usedBy: Record<string, number> = {};
  const consideredBy: Record<string, number> = {};
  strikes.forEach((s, k) => {
    s.used.forEach((id) => (usedBy[id] = k));
    s.calcs.forEach((id) => (usedBy[`C:${id}`] = k));
    s.considered.forEach((id) => (consideredBy[id] ??= k));
  });
  const pathNodes: Record<string, number> = {};
  strikes.forEach((s, k) => s.used.forEach((sid) => {
    let n = nodes[sid];
    while (n.parent) { pathNodes[n.parent] = Math.max(pathNodes[n.parent] ?? 0, levels[k]); n = nodes[n.parent]; }
  }));
  const label = (id: string, size: number, color: string, weight: number, offset: number) => {
    const n = nodes[id];
    const deg = (n.angle * 180) / Math.PI;
    const left = Math.cos(n.angle) < 0;
    return (
      <text x={n.x} y={n.y} dy="0.35em" fontSize={size} fill={color} fontWeight={weight}
        transform={`rotate(${left ? deg + 180 : deg} ${n.x} ${n.y}) translate(${left ? -offset : offset} 0)`}
        textAnchor={left ? 'end' : 'start'}>{n.title}</text>
    );
  };
  const edges: [string, string][] = [];
  for (const d of domains) {
    edges.push(['ROOT', d.id]);
    for (const tp of d.topics) {
      edges.push([d.id, tp.id]);
      for (const s of tp.sections) edges.push([tp.id, s.id]);
    }
  }
  return (
    <svg viewBox={`0 0 ${V} ${V}`} width="100%" height="100%" style={{fontFamily}}>
      <defs>
        <filter id="halo" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="11" /></filter>
        <filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3.2" /></filter>
        <filter id="nodeglow" x="-200%" y="-200%" width="500%" height="500%"><feGaussianBlur stdDeviation="7" /></filter>
        <radialGradient id="core">
          <stop offset="0%" stopColor={t.brand2} stopOpacity={0.55} />
          <stop offset="100%" stopColor={t.brand2} stopOpacity={0} />
        </radialGradient>
      </defs>
      {/* guide rings */}
      {[190, 360, 455, 540].map((r) => (
        <circle key={r} cx={C} cy={C} r={r} fill="none" stroke={t.line} strokeWidth={1} strokeDasharray={r === 455 ? '2 7' : undefined} opacity={0.6} />
      ))}
      {/* tree edges */}
      {edges.map(([a, b]) => {
        const k = usedBy[b];
        const onPath = pathNodes[b] != null || k != null;
        return <path key={a + b} d={radialLink(nodes[a], nodes[b], C, C)} fill="none" stroke={onPath ? t.muted : t.line}
          strokeWidth={nodes[b].level === 3 ? 1 : 1.6} opacity={onPath ? 0.9 : 0.75} />;
      })}
      {/* calculator links (dashed) */}
      {calculators.flatMap((c) => c.reads.slice(0, 1).map((sid) => (
        <line key={c.id + sid} x1={nodes[sid].x} y1={nodes[sid].y} x2={nodes[`C:${c.id}`].x} y2={nodes[`C:${c.id}`].y}
          stroke={t.calc} strokeWidth={1.2} strokeDasharray="4 5" opacity={0.45} />
      )))}
      {/* section labels (faint; struck ones bold) */}
      {domains.flatMap((d) => d.topics.flatMap((tp) => tp.sections.map((s) => {
        const k = usedBy[s.id];
        const on = k != null && levels[k] > 0.05;
        return <React.Fragment key={'l' + s.id}>{label(s.id, on ? 17 : 13, on ? t.ink : t.faint, on ? 700 : 400, on ? 18 : 12)}</React.Fragment>;
      })))}
      {/* topic labels */}
      {domains.flatMap((d) => d.topics.map((tp) => (
        <React.Fragment key={'tl' + tp.id}>{label(tp.id, 13, pathNodes[tp.id] ? t.ink : t.muted, pathNodes[tp.id] ? 600 : 400, 12)}</React.Fragment>
      )))}
      {/* sections */}
      {domains.flatMap((d) => d.topics.flatMap((tp) => tp.sections.map((s) => {
        const n = nodes[s.id];
        const k = usedBy[s.id];
        const c = consideredBy[s.id];
        const on = k != null ? levels[k] : 0;
        const cons = c != null ? Math.min(1, levels[c] * 1.5) : 0;
        return (
          <g key={s.id}>
            {on > 0.05 && <circle cx={n.x} cy={n.y} r={20} fill={t.used} opacity={0.55 * on} filter="url(#nodeglow)" />}
            <circle cx={n.x} cy={n.y} r={on > 0.05 ? 9 : cons > 0.05 ? 6.5 : 4.2}
              fill={on > 0.05 ? t.used : cons > 0.05 ? t.considered : t.faint}
              stroke={s.approved ? t.used : 'none'} strokeWidth={s.approved ? 2.5 : 0} />
          </g>
        );
      })))}
      {/* topics and domains */}
      {domains.flatMap((d) => d.topics.map((tp) => {
        const n = nodes[tp.id];
        const p = pathNodes[tp.id] ?? 0;
        return <circle key={tp.id} cx={n.x} cy={n.y} r={8} fill={p > 0.05 ? t.ink : t.muted} stroke={t.bg} strokeWidth={2} />;
      }))}
      {domains.map((d) => {
        const n = nodes[d.id];
        return <circle key={d.id} cx={n.x} cy={n.y} r={13} fill={t.ink} stroke={t.bg} strokeWidth={3} />;
      })}
      {/* calculators */}
      {calculators.map((c) => {
        const n = nodes[`C:${c.id}`];
        const k = usedBy[`C:${c.id}`];
        const on = k != null ? levels[k] : 0;
        return (
          <g key={c.id}>
            {on > 0.05 && <rect x={n.x - 18} y={n.y - 18} width={36} height={36} rx={8} fill={t.calc} opacity={0.6 * on} filter="url(#nodeglow)" />}
            <rect x={n.x - 9} y={n.y - 9} width={18} height={18} rx={4} fill={t.calc} opacity={on > 0.05 ? 1 : 0.75} />
          </g>
        );
      })}
      {/* lightning */}
      {strikeBolts.map((b, i) => {
        const lv = levels[b.k];
        if (lv <= 0.01) return null;
        const dash = {strokeDasharray: 1, strokeDashoffset: 1 - draw[b.k]} as const;
        return (
          <g key={i} strokeLinecap="round" strokeLinejoin="round" fill="none">
            <path d={b.main} pathLength={1} {...dash} stroke={t.bolt.halo} strokeWidth={22} opacity={0.45 * lv} filter="url(#halo)" />
            <path d={b.main} pathLength={1} {...dash} stroke={t.bolt.glow} strokeWidth={8} opacity={0.9 * lv} filter="url(#glow)" />
            {b.forks.map((f, j) => <path key={j} d={f} stroke={t.bolt.glow} strokeWidth={3} opacity={0.8 * lv * draw[b.k]} />)}
            <path d={b.main} pathLength={1} {...dash} stroke={t.bolt.core} strokeWidth={2.6} opacity={Math.min(1, 1.2 * lv)} />
            {b.forks.map((f, j) => <path key={'c' + j} d={f} stroke={t.bolt.core} strokeWidth={1.2} opacity={lv * draw[b.k]} />)}
          </g>
        );
      })}
      {/* root */}
      <circle cx={C} cy={C} r={70} fill="url(#core)" />
      <circle cx={C} cy={C} r={24} fill={t.brand} stroke={t.bg} strokeWidth={4} />
      <rect x={C - 84} y={C + 34} width={168} height={32} rx={16} fill={t.panel} stroke={t.line} strokeWidth={1.5} />
      <text x={C} y={C + 50} dy="0.35em" textAnchor="middle" fontSize={17} fontWeight={700} fill={t.ink}>knowledge base</text>
      {/* domain labels */}
      {domains.map((d) => {
        const n = nodes[d.id];
        const lx = C + Math.cos(n.angle) * 268;
        const ly = C + Math.sin(n.angle) * 268;
        const w = d.title.length * 11.2 + 28;
        return (
          <g key={'dl' + d.id}>
            <rect x={lx - w / 2} y={ly - 19} width={w} height={38} rx={19} fill={t.panel} stroke={pathNodes[d.id] ? t.ink : t.line} strokeWidth={1.5} />
            <text x={lx} y={ly} dy="0.35em" textAnchor="middle" fontSize={19} fontWeight={700} fill={t.ink}>{d.title}</text>
          </g>
        );
      })}
      {/* number badges at the struck sections */}
      {strikes.map((s, k) => {
        if (levels[k] <= 0.05) return null;
        const n = nodes[s.used[0]];
        const bx = C + Math.cos(n.angle) * 512;
        const by = C + Math.sin(n.angle) * 512;
        return (
          <g key={'b' + k} opacity={Math.min(1, levels[k] * 1.4)}>
            <circle cx={bx} cy={by} r={15} fill={t.bolt.glow} />
            <text x={bx} y={by} dy="0.36em" textAnchor="middle" fontSize={17} fontWeight={800} fill="#0b1120">{NUM[k]}</text>
          </g>
        );
      })}
      {flash > 0 && <rect x={0} y={0} width={V} height={V} fill={t.bolt.glow} opacity={flash * 0.07} />}
    </svg>
  );
};

const Corners: React.FC<{t: Theme; levels: number[]; scale: number}> = ({t, levels, scale}) => {
  const box = {position: 'absolute' as const, fontFamily, color: t.ink, fontSize: 20 * scale, lineHeight: 1.45};
  const dot = (c: string, square = false, ring = false): React.CSSProperties => ({
    display: 'inline-block', width: 14 * scale, height: 14 * scale, borderRadius: square ? 3 * scale : '50%',
    background: ring ? 'transparent' : c, border: ring ? `${2.5 * scale}px solid ${c}` : 'none', marginRight: 10 * scale,
    verticalAlign: 'middle', boxSizing: 'border-box'});
  return (
    <>
      <div style={{...box, left: 48 * scale, top: 40 * scale}}>
        <div style={{fontSize: 30 * scale, fontWeight: 800, letterSpacing: -0.5 * scale}}>Knowledge base</div>
        <div style={{color: t.muted}}>Northwind Bikes (fictional example)</div>
        <div style={{color: t.muted}}>{stats.domains} domains · {stats.topics} topics · {stats.sections} sections · {stats.calculators} calculators</div>
      </div>
      <div style={{...box, left: 48 * scale, bottom: 40 * scale}}>
        <div style={{fontWeight: 700, marginBottom: 6 * scale}}>Each bolt is one answer</div>
        {strikes.map((s, k) => (
          <div key={k} style={{opacity: 0.35 + 0.65 * Math.min(1, levels[k] * 1.4)}}>
            <span style={{display: 'inline-block', width: 26 * scale, height: 26 * scale, borderRadius: '50%', background: t.bolt.glow,
              color: '#0b1120', fontWeight: 800, textAlign: 'center', lineHeight: `${26 * scale}px`, marginRight: 10 * scale, fontSize: 16 * scale}}>{NUM[k]}</span>
            {s.question}
          </div>
        ))}
      </div>
      <div style={{...box, right: 48 * scale, bottom: 40 * scale, textAlign: 'left'}}>
        <div><span style={dot(t.used)} />used in the answer</div>
        <div><span style={dot(t.considered)} />considered</div>
        <div><span style={dot(t.calc, true)} />calculator</div>
        <div><span style={dot(t.used, false, true)} />management-approved</div>
        <div><span style={{...dot(t.bolt.glow), height: 4 * scale, width: 24 * scale, borderRadius: 2 * scale}} />decision path</div>
      </div>
    </>
  );
};

export const KnowledgeRadial: React.FC<Props> = (props) => {
  const t = getTheme(props);
  const {width} = useVideoConfig();
  const levels = strikes.map(() => 0.95);
  return (
    <AbsoluteFill style={{background: t.bg}}>
      <Drawing t={t} levels={levels} draw={levels.map(() => 1)} flash={0} />
      <Corners t={t} levels={levels} scale={width / V} />
    </AbsoluteFill>
  );
};

export const STRIKE_FRAMES = 132;

export const KnowledgeStrikes: React.FC<Props> = (props) => {
  const t = getTheme(props);
  const frame = useCurrentFrame();
  const {width} = useVideoConfig();
  const fadeAll = interpolate(frame, [STRIKE_FRAMES - 18, STRIKE_FRAMES - 2], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const starts = strikes.map((_, k) => 10 + k * 34);
  const draw = starts.map((s) => interpolate(frame, [s, s + 4], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}));
  const levels = starts.map((s) => fadeAll * interpolate(frame, [s, s + 4, s + 8, s + 26], [0, 1, 1, 0.55],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}));
  const flash = Math.max(0, ...starts.map((s) => interpolate(frame, [s + 3, s + 5, s + 11], [0, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})));
  return (
    <AbsoluteFill style={{background: t.bg}}>
      <Drawing t={t} levels={levels} draw={draw} flash={flash} />
      <Corners t={t} levels={levels} scale={width / V} />
    </AbsoluteFill>
  );
};
