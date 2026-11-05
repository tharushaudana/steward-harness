import React from 'react';

// Hand-drawn line icons on a 24x24 grid (stroke-based, no fills), used inside the figures.
const P: Record<string, string[]> = {
  message: ['M4 5h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H10l-5 4v-4H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z'],
  shield: ['M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z', 'M8.5 12l2.5 2.5 4.5-5'],
  split: ['M3 12h6', 'M9 12l5-6h7', 'M9 12l5 6h7', 'M18 3l3 3-3 3', 'M18 15l3 3-3 3'],
  resume: ['M20 12a8 8 0 1 1-2.4-5.7', 'M20 4v5h-5'],
  layers: ['M12 3l9 5-9 5-9-5z', 'M3 13l9 5 9-5'],
  database: ['M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z', 'M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6', 'M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3'],
  send: ['M3 11l18-8-8 18-2-8z', 'M11 13l10-10'],
  bolt: ['M13 2L4 14h7l-1 8 9-12h-7z'],
  form: ['M8 3h8v3H8z', 'M6 5H5v16h14V5h-1', 'M8.5 11h7', 'M8.5 15h5'],
  wrench: ['M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.4 2.4-2.6-.6-.6-2.6z'],
  ask: ['M4 4h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-6l-5 4v-4H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z', 'M10 8.5a2 2 0 1 1 3 1.8c-.7.4-1 .8-1 1.6', 'M12 14.5h.01'],
  branch: ['M6 3v12', 'M6 15a3 3 0 1 0 0 .01', 'M18 9a3 3 0 1 0 0-.01', 'M18 9c0 4-6 3-12 6'],
  calc: ['M5 3h14v18H5z', 'M8 7h8', 'M8 12h2', 'M14 12h2', 'M8 16h2', 'M14 16h2'],
  check: ['M5 12l5 5 9-10'],
  filter: ['M3 5h18l-7 8v6l-4 2v-8z'],
};

export const Icon: React.FC<{name: keyof typeof P; size: number; color: string; stroke?: number}> = ({name, size, color, stroke = 1.9}) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round">
    {P[name].map((d) => <path key={d} d={d} />)}
  </svg>
);

export type IconName = keyof typeof P;
