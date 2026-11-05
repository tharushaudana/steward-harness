import React from 'react';
import {AbsoluteFill} from 'remotion';
import {Icon, IconName} from './lib/icons';
import {fontFamily, getTheme, Props, Theme} from './theme';

type Mech = keyof Theme['mech'];
export const ARCH_W = 2400;
export const ARCH_H = 1160;

const Y = 700; // spine centre line
const R = 68; // spine node radius
const JY = 330; // journey row
const JR = 48;
const spine: {id: string; x: number; icon: IconName; label: string; sub?: string; mech?: Mech; n?: string; solid?: boolean}[] = [
  {id: 'message', x: 150, icon: 'message', label: 'Message', solid: true},
  {id: 'gates', x: 400, icon: 'shield', label: 'Gates', mech: 'code', n: '1'},
  {id: 'triage', x: 650, icon: 'split', label: 'Triage', sub: '+ understand', mech: 'decision', n: '2'},
  {id: 'resume', x: 900, icon: 'resume', label: 'Resume', mech: 'decision', n: '3'},
  {id: 'compose', x: 1880, icon: 'layers', label: 'Compose', sub: 'templates · rules', mech: 'code', n: '5'},
  {id: 'commit', x: 2100, icon: 'database', label: 'Commit', mech: 'state', n: '6'},
  {id: 'reply', x: 2290, icon: 'send', label: 'Reply', solid: true},
];
const ENGINE = {x: 1050, y: 520, w: 680, h: 360};
const journey: {x: number; icon: IconName; label: string; mech: Mech}[] = [
  {x: 1000, icon: 'bolt', label: 'trigger', mech: 'decision'},
  {x: 1210, icon: 'form', label: 'extract', mech: 'llm'},
  {x: 1420, icon: 'wrench', label: 'tool', mech: 'tool'},
  {x: 1630, icon: 'ask', label: 'ask', mech: 'state'},
];
const sx = (id: string) => spine.find((s) => s.id === id)!.x;

const MiniTree: React.FC<{t: Theme; size: number}> = ({t, size}) => {
  const c = size / 2;
  const dom = [0, 1, 2, 3, 4].map((i) => (i / 5) * Math.PI * 2 - Math.PI / 2 + 0.3);
  const leaves = dom.flatMap((a, i) => [-0.38, -0.12, 0.14, 0.38].map((d, j) => ({a: a + d, i, j})));
  const p = (a: number, r: number) => ({x: c + Math.cos(a) * r, y: c + Math.sin(a) * r});
  const r1 = size * 0.2;
  const r2 = size * 0.44;
  const litD = 1;
  const litL = leaves.find((l) => l.i === litD && l.j === 2)!;
  const a = p(dom[litD], r1);
  const b = p(litL.a, r2);
  const bolt = `M${c} ${c} L${c + (a.x - c) * 0.55 + 6} ${c + (a.y - c) * 0.55 - 4} L${a.x} ${a.y} L${(a.x + b.x) / 2 - 7} ${(a.y + b.y) / 2 + 3} L${b.x} ${b.y}`;
  return (
    <svg width={size} height={size} style={{overflow: 'visible'}}>
      <defs><filter id="mtglow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="5" /></filter></defs>
      <circle cx={c} cy={c} r={r2} fill="none" stroke={t.line} strokeWidth={1.5} strokeDasharray="3 6" />
      {dom.map((d, i) => { const q = p(d, r1); return <line key={i} x1={c} y1={c} x2={q.x} y2={q.y} stroke={t.faint} strokeWidth={2} />; })}
      {leaves.map((l, k) => { const q = p(dom[l.i], r1); const z = p(l.a, r2); return <line key={k} x1={q.x} y1={q.y} x2={z.x} y2={z.y} stroke={t.line} strokeWidth={1.5} />; })}
      {leaves.map((l, k) => { const z = p(l.a, r2); return <circle key={k} cx={z.x} cy={z.y} r={4.5} fill={t.faint} />; })}
      {dom.map((d, i) => { const q = p(d, r1); return <circle key={i} cx={q.x} cy={q.y} r={7} fill={t.muted} />; })}
      <path d={bolt} fill="none" stroke={t.bolt.glow} strokeWidth={9} opacity={0.6} filter="url(#mtglow)" strokeLinejoin="round" />
      <path d={bolt} fill="none" stroke={t.bolt.glow} strokeWidth={3.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={b.x} cy={b.y} r={8} fill={t.used} />
      <circle cx={c} cy={c} r={11} fill={t.brand} />
    </svg>
  );
};

export const Architecture: React.FC<Props> = (props) => {
  const t = getTheme(props);
  const nodeBg = t.name === 'dark' ? t.panel : '#ffffff';
  const legend: [Mech, string][] = [['code', 'code'], ['decision', 'decision model'], ['llm', 'LLM'], ['tool', 'your tool'], ['state', 'state']];
  const triX = sx('triage');
  const compX = sx('compose');
  const resX = sx('resume');
  const label = (x: number, y: number, text: string, opts: {size?: number; weight?: number; color?: string; anchor?: 'start' | 'middle' | 'end'} = {}) => (
    <text x={x} y={y} textAnchor={opts.anchor ?? 'middle'} fontSize={opts.size ?? 30} fontWeight={opts.weight ?? 700}
      fill={opts.color ?? t.ink} fontFamily={fontFamily}>{text}</text>
  );
  return (
    <AbsoluteFill style={{background: t.bg, fontFamily}}>
      <svg width={ARCH_W} height={ARCH_H} style={{position: 'absolute', inset: 0}}>
        <defs>
          {[['ah', t.ink], ['ahb', t.brand], ['ahm', t.faint], ['aht', t.mech.tool]].map(([id, c]) => (
            <marker key={id} id={id} viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M0 0 L10 5 L0 10 z" fill={c} />
            </marker>
          ))}
          <filter id="eglow" x="-20%" y="-30%" width="140%" height="160%"><feGaussianBlur stdDeviation="26" /></filter>
        </defs>

        {/* spine connectors */}
        {spine.slice(0, -1).map((s, i) => {
          if (s.id === 'resume') return null;
          const next = spine[i + 1];
          return <line key={s.id} x1={s.x + R + 10} y1={Y} x2={next.x - R - 14} y2={Y} stroke={t.ink} strokeWidth={5} markerEnd="url(#ah)" />;
        })}
        <line x1={resX + R + 10} y1={Y} x2={ENGINE.x - 14} y2={Y} stroke={t.ink} strokeWidth={5} markerEnd="url(#ah)" />
        <line x1={ENGINE.x + ENGINE.w + 10} y1={Y} x2={compX - R - 14} y2={Y} stroke={t.ink} strokeWidth={5} markerEnd="url(#ah)" />

        {/* journey arc */}
        <path d={`M${triX} ${Y - R - 10} C${triX} ${JY + 30} ${triX + 120} ${JY} ${journey[0].x - JR - 14} ${JY}`} fill="none"
          stroke={t.brand} strokeWidth={5} markerEnd="url(#ahb)" />
        {journey.slice(0, -1).map((j, i) => (
          <line key={j.label} x1={j.x + JR + 10} y1={JY} x2={journey[i + 1].x - JR - 14} y2={JY} stroke={t.brand} strokeWidth={5} markerEnd="url(#ahb)" />
        ))}
        <path d={`M${journey[3].x + JR + 10} ${JY} C${compX - 60} ${JY} ${compX} ${JY + 60} ${compX} ${Y - R - 14}`} fill="none"
          stroke={t.brand} strokeWidth={5} markerEnd="url(#ahb)" />
        {/* ask waits -> resume */}
        <path d={`M${journey[3].x} ${JY + JR + 52} C${journey[3].x} ${JY + 150} ${resX + 160} ${JY + 140} ${resX + 40} ${JY + 175} S${resX} ${Y - R - 60} ${resX} ${Y - R - 14}`}
          fill="none" stroke={t.faint} strokeWidth={3.5} strokeDasharray="10 10" markerEnd="url(#ahm)" />
        {/* tool <-> your tools */}
        <line x1={journey[2].x} y1={JY - JR - 10} x2={journey[2].x} y2={202} stroke={t.mech.tool} strokeWidth={4} markerStart="url(#aht)" markerEnd="url(#aht)" />

        {/* engine glow */}
        <rect x={ENGINE.x} y={ENGINE.y} width={ENGINE.w} height={ENGINE.h} rx={36} fill={t.brand2} opacity={t.name === 'dark' ? 0.28 : 0.16} filter="url(#eglow)" />

        {/* journey nodes */}
        {journey.map((j) => (
          <g key={j.label}>
            <circle cx={j.x} cy={JY} r={JR} fill={nodeBg} stroke={t.mech[j.mech]} strokeWidth={4} />
            {label(j.x, JY + JR + 40, j.label, {size: 27, weight: 600, color: t.muted})}
          </g>
        ))}

        {/* spine nodes */}
        {spine.map((s) => (
          <g key={s.id}>
            <circle cx={s.x} cy={Y} r={R} fill={s.solid ? t.brand : nodeBg} stroke={s.solid ? t.brand : s.mech ? t.mech[s.mech] : t.line}
              strokeWidth={s.solid ? 0 : 5} />
            {s.n && <>
              <circle cx={s.x - R * 0.72} cy={Y - R * 0.72} r={19} fill={t.ink} />
              {label(s.x - R * 0.72, Y - R * 0.72 + 7, s.n, {size: 20, weight: 800, color: t.bg})}
            </>}
            {label(s.x, Y + R + 46, s.label, {size: 32})}
            {s.sub && label(s.x, Y + R + 82, s.sub, {size: 23, weight: 500, color: t.muted})}
          </g>
        ))}

        {/* annotations */}
        {label(110, JY - 20, 'Journeys', {size: 36, weight: 800, color: t.brand, anchor: 'start'})}
        {label(110, JY + 18, 'run alongside the answer', {size: 24, weight: 500, color: t.muted, anchor: 'start'})}
        {label(1240, JY + 128, 'ask waits for the reply', {size: 22, weight: 500, color: t.faint, anchor: 'start'})}
      </svg>

      {/* icons (HTML overlay, centred on the nodes) */}
      {spine.map((s) => (
        <div key={s.id} style={{position: 'absolute', left: s.x - 30, top: Y - 30}}>
          <Icon name={s.icon} size={60} color={s.solid ? '#ffffff' : t.ink} />
        </div>
      ))}
      {journey.map((j) => (
        <div key={j.label} style={{position: 'absolute', left: j.x - 21, top: JY - 21}}>
          <Icon name={j.icon} size={42} color={t.ink} />
        </div>
      ))}

      {/* your tools */}
      <div style={{position: 'absolute', left: journey[2].x - 390, top: 90, width: 780, height: 108, boxSizing: 'border-box', borderRadius: 54,
        border: `3px solid ${t.mech.tool}`, background: nodeBg, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 14}}>
        <span style={{fontSize: 26, fontWeight: 800, color: t.mech.tool, marginRight: 6, whiteSpace: 'nowrap'}}>your tools</span>
        {['crm.save_lead', 'agents.available', 'http.request'].map((x) => (
          <span key={x} style={{fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace', fontSize: 21, color: t.ink,
            background: t.panel2, borderRadius: 10, padding: '6px 11px'}}>{x}</span>
        ))}
      </div>

      {/* the engine */}
      <div style={{position: 'absolute', left: ENGINE.x, top: ENGINE.y, width: ENGINE.w, height: ENGINE.h, boxSizing: 'border-box',
        borderRadius: 36, border: `4px solid ${t.brand}`, background: t.name === 'dark' ? '#0d1b2a' : '#ffffff',
        display: 'flex', alignItems: 'center', gap: 34, padding: '0 40px'}}>
        <MiniTree t={t} size={250} />
        <div style={{display: 'flex', flexDirection: 'column', gap: 16}}>
          <div style={{display: 'flex', alignItems: 'center', gap: 14}}>
            <div style={{width: 40, height: 40, borderRadius: 20, background: t.ink, color: t.bg, fontSize: 22, fontWeight: 800,
              display: 'flex', alignItems: 'center', justifyContent: 'center'}}>4</div>
            <div style={{fontSize: 76, fontWeight: 800, letterSpacing: -2, color: t.ink, lineHeight: 1}}>DADR</div>
          </div>
          <div style={{fontSize: 27, color: t.muted, fontWeight: 500}}>knowledge engine</div>
          <div style={{display: 'grid', gridTemplateColumns: 'auto auto', gap: 10, marginTop: 6}}>
            {([['route', 'decision'], ['select', 'decision'], ['compute', 'code'], ['verify', 'code']] as [string, Mech][]).map(([l, m]) => (
              <span key={l} style={{display: 'inline-flex', alignItems: 'center', gap: 9, fontSize: 23, fontWeight: 600, color: t.ink,
                background: t.panel2, borderRadius: 999, padding: '6px 16px'}}>
                <span style={{width: 12, height: 12, borderRadius: 6, background: t.mech[m]}} />{l}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* legend */}
      <div style={{position: 'absolute', right: 60, top: 60, display: 'flex', gap: 26, fontSize: 23, color: t.muted}}>
        {legend.map(([m, l]) => (
          <span key={m} style={{display: 'flex', alignItems: 'center', gap: 10}}>
            <span style={{width: 16, height: 16, borderRadius: 8, border: `3.5px solid ${t.mech[m]}`}} />{l}
          </span>
        ))}
      </div>

      {/* infrastructure row */}
      <div style={{position: 'absolute', left: 80, right: 80, top: 950, borderTop: `2px solid ${t.line}`}} />
      {([
        [80, 'stores', ['memory', 'SQLite', 'MongoDB', 'Firestore', 'your own']],
        [1050, 'models', ['decision model', 'LLM', 'spend cap']],
        [1700, 'admin', ['draft', 'test', 'publish']],
      ] as [number, string, string[]][]).map(([x, title, chips]) => (
        <div key={title} style={{position: 'absolute', left: x, top: 985}}>
          <div style={{fontSize: 21, fontWeight: 800, letterSpacing: 3, color: t.faint, textTransform: 'uppercase', marginBottom: 14}}>{title}</div>
          <div style={{display: 'flex', gap: 12, alignItems: 'center'}}>
            {chips.map((c, i) => (
              <React.Fragment key={c}>
                {title === 'admin' && i > 0 && <span style={{color: t.faint, fontSize: 24}}>→</span>}
                <span style={{fontSize: 25, fontWeight: 600, color: t.ink, border: `2px solid ${t.line}`, borderRadius: 12, padding: '8px 18px'}}>{c}</span>
              </React.Fragment>
            ))}
          </div>
        </div>
      ))}
    </AbsoluteFill>
  );
};
