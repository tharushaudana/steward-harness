import {loadFont} from '@remotion/google-fonts/Inter';

export const {fontFamily} = loadFont('normal', {weights: ['400', '500', '600', '700', '800'], subsets: ['latin']});

export type ThemeName = 'light' | 'dark';
export type Props = {theme: ThemeName};

export type Theme = {
  name: ThemeName;
  bg: string;
  panel: string;
  panel2: string;
  ink: string;
  muted: string;
  faint: string;
  line: string;
  brand: string;
  brand2: string;
  bolt: {core: string; glow: string; halo: string};
  used: string;
  considered: string;
  calc: string;
  mech: Record<'code' | 'decision' | 'llm' | 'tool' | 'state', string>;
  shadow: string;
};

const mech = {code: '#2563eb', decision: '#7c3aed', llm: '#ea580c', tool: '#16a34a', state: '#64748b'};

export const themes: Record<ThemeName, Theme> = {
  light: {
    name: 'light', bg: '#ffffff', panel: '#ffffff', panel2: '#f1f5f9', ink: '#0f172a', muted: '#475569', faint: '#94a3b8',
    line: '#cbd5e1', brand: '#0f766e', brand2: '#14b8a6',
    bolt: {core: '#ffffff', glow: '#06b6d4', halo: '#7c3aed'},
    used: '#16a34a', considered: '#f59e0b', calc: '#7c3aed', mech,
    shadow: '0 6px 24px rgba(15, 23, 42, 0.08), 0 1px 3px rgba(15, 23, 42, 0.06)',
  },
  dark: {
    name: 'dark', bg: '#0b1120', panel: '#111a2e', panel2: '#17223a', ink: '#e2e8f0', muted: '#94a3b8', faint: '#475569',
    line: '#2a3754', brand: '#2dd4bf', brand2: '#14b8a6',
    bolt: {core: '#ffffff', glow: '#22d3ee', halo: '#8b5cf6'},
    used: '#22c55e', considered: '#fbbf24', calc: '#a78bfa',
    mech: {code: '#60a5fa', decision: '#a78bfa', llm: '#fb923c', tool: '#4ade80', state: '#94a3b8'},
    shadow: 'none',
  },
};

export const getTheme = (p?: Partial<Props>): Theme => themes[p?.theme === 'dark' ? 'dark' : 'light'];
