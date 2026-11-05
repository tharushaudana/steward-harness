import React from 'react';
import {AbsoluteFill} from 'remotion';
import {Icon} from './lib/icons';
import {fontFamily, getTheme, Props, Theme} from './theme';

// DADR (dependency-aware decision retrieval), told as one question in four steps. Fictional data (Northwind Bikes).
export const DADR_W = 2440;
export const DADR_H = 1120;

type Mech = keyof Theme['mech'];
const PANEL = {y: 250, h: 740, w: 540};
const GAP = (DADR_W - 80 - 4 * PANEL.w) / 3;
const PX = [0, 1, 2, 3].map((i) => 40 + i * (PANEL.w + GAP));
const INNER = PANEL.w - 68;

const mono = 'ui-monospace, SFMono-Regular, Consolas, monospace';

const Panel: React.FC<{t: Theme; i: number; title: string; mech: Mech[]; foot: string; children: React.ReactNode}> = ({t, i, title, mech, foot, children}) => (
  <div style={{position: 'absolute', left: PX[i], top: PANEL.y, width: PANEL.w, height: PANEL.h, boxSizing: 'border-box', borderRadius: 30,
    border: `2px solid ${t.line}`, background: t.name === 'dark' ? t.panel : '#ffffff', padding: '30px 34px', display: 'flex', flexDirection: 'column'}}>
    <div style={{display: 'flex', alignItems: 'center', gap: 14}}>
      <div style={{width: 44, height: 44, borderRadius: 22, background: t.ink, color: t.bg, fontSize: 24, fontWeight: 800,
        display: 'flex', alignItems: 'center', justifyContent: 'center'}}>{i + 1}</div>
      <div style={{fontSize: 40, fontWeight: 800, letterSpacing: -0.8, color: t.ink}}>{title}</div>
      <div style={{flex: 1}} />
      {mech.map((m) => <span key={m} style={{width: 18, height: 18, borderRadius: 9, border: `4px solid ${t.mech[m]}`}} />)}
    </div>
    <div style={{flex: 1, position: 'relative', marginTop: 18}}>{children}</div>
    <div style={{fontSize: 27, color: t.muted, fontWeight: 500, borderTop: `2px solid ${t.line}`, paddingTop: 18}}>{foot}</div>
  </div>
);

const Score: React.FC<{t: Theme; v: number; on?: boolean}> = ({t, v, on}) => (
  <span style={{fontFamily: mono, fontSize: 23, fontWeight: 700, padding: '3px 10px', borderRadius: 8,
    background: on ? t.mech.decision : t.panel2, color: on ? '#ffffff' : t.muted}}>{v.toFixed(2)}</span>
);

const Route: React.FC<{t: Theme}> = ({t}) => {
  const W = INNER;
  const root = {x: W / 2, y: 40};
  const doms = [['Bikes', 0.08], ['E-bikes', 0.94], ['Orders', 0.11], ['Workshop', 0.02]] as const;
  const dx = (i: number) => 56 + i * ((W - 112) / 3);
  const DY = 190;
  const topics = [['Volt range', 0.46], ['E-bike prices', 0.91], ['Batteries', 0.05]] as const;
  const tx = (i: number) => 70 + i * ((W - 140) / 2);
  const TY = 400;
  return (
    <svg width={W} height={520} style={{fontFamily, overflow: 'visible'}}>
      <defs><filter id="rglow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="6" /></filter></defs>
      {doms.map(([, v], i) => (
        <line key={i} x1={root.x} y1={root.y} x2={dx(i)} y2={DY} stroke={v > 0.5 ? t.bolt.glow : t.line} strokeWidth={v > 0.5 ? 5 : 2} />
      ))}
      {topics.map(([, v], i) => (
        <line key={i} x1={dx(1)} y1={DY} x2={tx(i)} y2={TY} stroke={v > 0.5 ? t.bolt.glow : t.line} strokeWidth={v > 0.5 ? 5 : 2} />
      ))}
      <path d={`M${root.x} ${root.y} L${dx(1)} ${DY} L${tx(1)} ${TY}`} stroke={t.bolt.glow} strokeWidth={12} opacity={0.45} fill="none" filter="url(#rglow)" />
      <circle cx={root.x} cy={root.y} r={16} fill={t.brand} />
      {doms.map(([name, v], i) => (
        <g key={name}>
          <circle cx={dx(i)} cy={DY} r={v > 0.5 ? 13 : 9} fill={v > 0.5 ? t.ink : t.faint} />
          <text x={dx(i)} y={DY - 26} textAnchor="middle" fontSize={24} fontWeight={v > 0.5 ? 800 : 500} fill={v > 0.5 ? t.ink : t.muted}>{name}</text>
        </g>
      ))}
      {topics.map(([name, v], i) => (
        <g key={name}>
          <circle cx={tx(i)} cy={TY} r={v > 0.5 ? 12 : 8} fill={v > 0.5 ? t.ink : t.faint} />
          <text x={tx(i)} y={TY + 42} textAnchor="middle" fontSize={24} fontWeight={v > 0.5 ? 800 : 500} fill={v > 0.5 ? t.ink : t.muted}>{name}</text>
        </g>
      ))}
      {/* score badges */}
      {doms.map(([name, v], i) => (
        <foreignObject key={'s' + name} x={dx(i) - 34} y={DY + 18} width={68} height={34}>
          <div style={{display: 'flex', justifyContent: 'center'}}><Score t={t} v={v} on={v > 0.5} /></div>
        </foreignObject>
      ))}
      {topics.map(([name, v], i) => (
        <foreignObject key={'t' + name} x={tx(i) - 34} y={TY + 56} width={68} height={34}>
          <div style={{display: 'flex', justifyContent: 'center'}}><Score t={t} v={v} on={v > 0.5} /></div>
        </foreignObject>
      ))}
    </svg>
  );
};

const Row: React.FC<{t: Theme; ok: boolean; label: string; v: number; tag?: string; calc?: boolean}> = ({t, ok, label, v, tag, calc}) => (
  <div style={{display: 'flex', alignItems: 'center', gap: 14, padding: '13px 16px', borderRadius: 14,
    background: ok ? (t.name === 'dark' ? '#10261c' : '#f0fdf4') : 'transparent', border: `2px solid ${ok ? t.used : t.line}`, opacity: ok ? 1 : 0.7}}>
    <span style={{width: 34, height: 34, borderRadius: calc ? 8 : 17, background: ok ? (calc ? t.calc : t.used) : t.panel2,
      display: 'flex', alignItems: 'center', justifyContent: 'center', color: ok ? '#fff' : t.faint, fontSize: 22, fontWeight: 800, flex: 'none'}}>
      {ok ? <Icon name={calc ? 'calc' : 'check'} size={22} color="#ffffff" stroke={2.6} /> : '×'}
    </span>
    <span style={{fontSize: 27, fontWeight: ok ? 700 : 500, color: t.ink, flex: 1, fontFamily: calc ? mono : fontFamily}}>{label}</span>
    {tag && <span style={{fontSize: 18, color: t.muted}}>{tag}</span>}
    <Score t={t} v={v} on={ok} />
  </div>
);

const Select: React.FC<{t: Theme}> = ({t}) => (
  <div style={{display: 'flex', flexDirection: 'column', gap: 12}}>
    <div style={{fontSize: 22, fontWeight: 800, letterSpacing: 2, color: t.faint}}>SECTIONS</div>
    <Row t={t} ok label="Volt price (old page)" v={0.88} />
    <Row t={t} ok={false} label="Volt overview" v={0.21} />
    <Row t={t} ok={false} label="Hauler price list" v={0.07} />
    <div style={{fontSize: 22, fontWeight: 800, letterSpacing: 2, color: t.faint, marginTop: 10}}>CALCULATORS</div>
    <Row t={t} ok calc label="order_total" v={0.86} />
    <div style={{fontSize: 22, fontWeight: 800, letterSpacing: 2, color: t.faint, marginTop: 10}}>GATE</div>
    <div style={{display: 'flex', alignItems: 'center', gap: 14, padding: '13px 16px', borderRadius: 14, border: `2px dashed ${t.line}`}}>
      <span style={{fontSize: 27, color: t.ink, flex: 1}}>enough to answer?</span>
      <span style={{fontSize: 26, fontWeight: 800, color: t.used}}>yes</span>
      <Score t={t} v={0.81} on />
    </div>
  </div>
);

const Chip: React.FC<{t: Theme; children: React.ReactNode; color?: string; strike?: boolean; dashed?: boolean}> = ({t, children, color, strike, dashed}) => (
  <div style={{display: 'inline-flex', alignItems: 'center', gap: 10, padding: '10px 16px', borderRadius: 14, fontSize: 26, fontWeight: 600,
    border: `2.5px ${dashed ? 'dashed' : 'solid'} ${color ?? t.line}`, color: t.ink, background: t.name === 'dark' ? t.bg : '#ffffff',
    textDecoration: strike ? 'line-through' : 'none', opacity: strike ? 0.6 : 1, whiteSpace: 'nowrap'}}>{children}</div>
);

const Deps: React.FC<{t: Theme}> = ({t}) => {
  const small = {fontSize: 22, fontWeight: 800, letterSpacing: 2, color: t.faint} as const;
  return (
    <div style={{position: 'relative', height: '100%'}}>
      <div style={small}>APPROVED BEATS OUTDATED</div>
      <div style={{display: 'flex', alignItems: 'center', gap: 12, marginTop: 14}}>
        <Chip t={t} strike><b style={{fontFamily: mono}}>$2,490</b></Chip>
        <span style={{fontSize: 30, color: t.muted}}>→</span>
        <Chip t={t} color={t.used}><b style={{fontFamily: mono}}>$2,290</b>
          <span style={{fontSize: 16, fontWeight: 800, color: t.used, border: `2px solid ${t.used}`, borderRadius: 6, padding: '0 6px'}}>approved</span></Chip>
      </div>
      <div style={{...small, marginTop: 40}}>CALCULATOR INPUTS</div>
      <svg width={INNER} height={300} style={{position: 'absolute', left: 0, top: 200, overflow: 'visible'}}>
        {[[50, 40], [INNER / 2, 40], [INNER - 50, 40]].map(([x, y]) => (
          <path key={x} d={`M${x} ${y + 34} C${x} ${y + 120} ${INNER / 2} ${y + 110} ${INNER / 2} ${y + 178}`} fill="none" stroke={t.calc} strokeWidth={3.5} strokeDasharray="9 7" />
        ))}
      </svg>
      <div style={{position: 'absolute', top: 236, left: 0, right: 0, display: 'flex', justifyContent: 'space-between'}}>
        <Chip t={t} color={t.used}>price</Chip>
        <Chip t={t} color={t.considered} dashed>delivery fee</Chip>
        <Chip t={t}>qty = 2</Chip>
      </div>
      <div style={{position: 'absolute', top: 196, left: 0, right: 0, textAlign: 'center', fontSize: 22, color: t.considered, fontWeight: 700}}>pulled in from another domain</div>
      <div style={{position: 'absolute', top: 410, left: 0, right: 0, display: 'flex', justifyContent: 'center'}}>
        <div style={{display: 'flex', alignItems: 'center', gap: 12, padding: '14px 22px', borderRadius: 16, background: t.calc, color: '#fff',
          fontSize: 30, fontWeight: 800, fontFamily: mono}}>
          <Icon name="calc" size={30} color="#ffffff" stroke={2.2} />order_total
        </div>
      </div>
    </div>
  );
};

const Verify: React.FC<{t: Theme}> = ({t}) => (
  <div style={{display: 'flex', flexDirection: 'column', gap: 26}}>
    <div style={{borderRadius: 18, border: `2.5px solid ${t.calc}`, padding: '20px 22px', background: t.name === 'dark' ? t.bg : '#faf5ff'}}>
      <div style={{fontSize: 22, fontWeight: 800, letterSpacing: 2, color: t.faint}}>COMPUTED IN CODE</div>
      <div style={{fontFamily: mono, fontSize: 34, fontWeight: 700, color: t.ink, marginTop: 10}}>2 × 2,290 + 49</div>
      <div style={{fontFamily: mono, fontSize: 44, fontWeight: 800, color: t.calc, marginTop: 4}}>= 4,629</div>
    </div>
    <div style={{alignSelf: 'flex-start', maxWidth: 470, borderRadius: '22px 22px 22px 6px', background: t.panel2, padding: '18px 22px',
      fontSize: 30, lineHeight: 1.4, color: t.ink}}>
      Two Volt e-bikes come to <b>$4,629</b>: <b>$2,290</b> each plus <b>$49</b> delivery.
    </div>
    <div style={{display: 'flex', gap: 10, flexWrap: 'wrap'}}>
      {['4,629', '2,290', '49'].map((n) => (
        <span key={n} style={{display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 25, fontWeight: 700, color: t.used,
          border: `2px solid ${t.used}`, borderRadius: 999, padding: '6px 14px', fontFamily: mono}}>
          <Icon name="check" size={20} color={t.used} stroke={3} />{n}
        </span>
      ))}
    </div>
  </div>
);

export const Dadr: React.FC<Props> = (props) => {
  const t = getTheme(props);
  return (
    <AbsoluteFill style={{background: t.bg, fontFamily, color: t.ink}}>
      {/* title + question */}
      <div style={{position: 'absolute', left: 40, top: 46}}>
        <div style={{fontSize: 64, fontWeight: 800, letterSpacing: -2, lineHeight: 1}}>DADR</div>
        <div style={{fontSize: 28, color: t.muted, fontWeight: 500, marginTop: 8}}>dependency-aware decision retrieval</div>
      </div>
      <div style={{position: 'absolute', left: 760, top: 64, display: 'flex', alignItems: 'center', gap: 20}}>
        <div style={{width: 64, height: 64, borderRadius: 32, background: t.brand, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
          <Icon name="message" size={34} color="#ffffff" />
        </div>
        <div style={{fontSize: 40, fontWeight: 700, color: t.ink, background: t.panel2, padding: '18px 30px', borderRadius: '30px 30px 30px 8px'}}>
          “What would two Volt e-bikes cost in total?”
        </div>
      </div>
      <div style={{position: 'absolute', right: 40, top: 64, display: 'flex', gap: 24, fontSize: 23, color: t.muted}}>
        {([['decision', 'decision model'], ['code', 'code'], ['llm', 'LLM']] as [Mech, string][]).map(([m, l]) => (
          <span key={m} style={{display: 'flex', alignItems: 'center', gap: 10}}>
            <span style={{width: 16, height: 16, borderRadius: 8, border: `3.5px solid ${t.mech[m]}`}} />{l}
          </span>
        ))}
      </div>

      <Panel t={t} i={0} title="Route" mech={['decision']} foot="one batched call scores the tree">
        <Route t={t} />
      </Panel>
      <Panel t={t} i={1} title="Select" mech={['decision']} foot="yes / no per section, then a gate">
        <Select t={t} />
      </Panel>
      <Panel t={t} i={2} title="Dependencies" mech={['code']} foot="what it needs comes with it">
        <Deps t={t} />
      </Panel>
      <Panel t={t} i={3} title="Compute · verify" mech={['code', 'llm']} foot="the LLM phrases checked numbers">
        <Verify t={t} />
      </Panel>

      {/* arrows between panels */}
      <svg width={DADR_W} height={DADR_H} style={{position: 'absolute', inset: 0, pointerEvents: 'none'}}>
        <defs>
          <marker id="dah" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill={t.ink} /></marker>
        </defs>
        {[0, 1, 2].map((i) => (
          <line key={i} x1={PX[i] + PANEL.w + 8} y1={PANEL.y + PANEL.h / 2} x2={PX[i + 1] - 12} y2={PANEL.y + PANEL.h / 2}
            stroke={t.ink} strokeWidth={5} markerEnd="url(#dah)" />
        ))}
      </svg>

      {/* takeaways */}
      <div style={{position: 'absolute', left: 40, right: 40, top: 1030, display: 'flex', justifyContent: 'space-between', fontSize: 28, color: t.muted}}>
        {['no embeddings or vector index', 'a cheap decision model reads the tree', 'numbers come from code, never the LLM', 'every step is in the trace'].map((x) => (
          <span key={x} style={{display: 'flex', alignItems: 'center', gap: 10}}><Icon name="check" size={24} color={t.brand} stroke={3} />{x}</span>
        ))}
      </div>
    </AbsoluteFill>
  );
};
