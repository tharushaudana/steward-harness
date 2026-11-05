import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fontFamily, getTheme, Props, Theme} from './theme';

// The emblem: an "S" drawn as a decision path through nodes. The chosen path glows; the branches not taken are muted.
// Drawn on a 400x400 grid.
const S_PATH = 'M300 80 H150 Q100 80 100 130 V150 Q100 200 150 200 H250 Q300 200 300 250 V270 Q300 320 250 320 H100';
const BRANCHES = [
  'M150 200 V140',          // up from the middle
  'M250 200 V140 H330',     // up and out to the right
  'M300 260 H360',          // right from the lower bend
  'M150 320 V370',          // down from the bottom stroke
  'M100 140 H45',           // left from the upper bend
];
const BRANCH_ENDS = [[150, 140], [330, 140], [360, 260], [150, 370], [45, 140]];
const PATH_NODES = [[300, 80], [100, 140], [200, 200], [300, 260], [100, 320]];

export const Emblem: React.FC<{t: Theme; size: number}> = ({t, size}) => (
  <svg viewBox="0 0 400 400" width={size} height={size} style={{overflow: 'visible'}}>
    <defs>
      <linearGradient id="sgrad" x1="300" y1="80" x2="100" y2="320" gradientUnits="userSpaceOnUse">
        <stop offset="0%" stopColor={t.brand} />
        <stop offset="100%" stopColor={t.bolt.glow} />
      </linearGradient>
      <filter id="sglow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="10" /></filter>
    </defs>
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      {BRANCHES.map((d) => <path key={d} d={d} stroke={t.faint} strokeWidth={10} opacity={0.75} />)}
      <path d={S_PATH} stroke={t.bolt.glow} strokeWidth={34} opacity={t.name === 'dark' ? 0.55 : 0.35} filter="url(#sglow)" />
      <path d={S_PATH} stroke="url(#sgrad)" strokeWidth={24} />
    </g>
    {BRANCH_ENDS.map(([x, y]) => <circle key={`${x}-${y}`} cx={x} cy={y} r={11} fill={t.bg === '#ffffff' ? '#ffffff' : t.bg} stroke={t.faint} strokeWidth={6} />)}
    {PATH_NODES.map(([x, y], i) => (
      <circle key={`${x}-${y}`} cx={x} cy={y} r={i === 0 || i === 4 ? 21 : 17} fill={t.name === 'dark' ? t.bg : '#ffffff'}
        stroke={i === 4 ? t.bolt.glow : t.brand} strokeWidth={9} />
    ))}
    <circle cx={100} cy={320} r={8} fill={t.bolt.glow} />
  </svg>
);

export const LogoBanner: React.FC<Props> = (props) => {
  const t = getTheme(props);
  return (
    <AbsoluteFill style={{flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 56, fontFamily}}>
      <Emblem t={t} size={300} />
      <div style={{display: 'flex', flexDirection: 'column'}}>
        <div style={{fontSize: 172, fontWeight: 800, letterSpacing: -6, color: t.ink, lineHeight: 1}}>Steward</div>
        <div style={{fontSize: 40, fontWeight: 500, color: t.muted, marginTop: 18, letterSpacing: -0.2}}>
          a compliant customer-service harness, powered by <span style={{color: t.brand, fontWeight: 700}}>DADR</span>
        </div>
      </div>
    </AbsoluteFill>
  );
};

export const LogoIcon: React.FC<Props> = (props) => {
  const t = getTheme(props);
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
      <div style={{width: 512, height: 512, borderRadius: 112, background: t.name === 'dark' ? t.bg : '#ffffff',
        display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: `inset 0 0 0 2px ${t.line}`}}>
        <Emblem t={t} size={400} />
      </div>
    </AbsoluteFill>
  );
};
