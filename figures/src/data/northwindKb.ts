// A fictional knowledge base ("Northwind Bikes") shaped like a real one: domains -> topics -> section-sized units,
// plus calculators linked to the sections holding their inputs. Used only for the README figures.

export type Section = {id: string; title: string; approved?: boolean};
export type Topic = {id: string; title: string; sections: Section[]};
export type Domain = {id: string; title: string; topics: Topic[]};
export type Calculator = {id: string; title: string; reads: string[]};
export type Strike = {question: string; used: string[]; considered: string[]; calcs: string[]};

const raw: [string, [string, (string | [string, true])[]][]][] = [
  ['Bikes', [
    ['Mountain bikes', ['Trail 500 overview', 'Trail 500 frame sizes', 'Trail 500 suspension', 'Ridge 700 overview', 'Ridge 700 geometry', 'Summit carbon frame']],
    ['Road bikes', ['Aero 9 overview', 'Aero 9 groupset', 'Sprint 3 overview', 'Endurance fit guide']],
    ['City and commuter', ['City Cruiser overview', 'City Cruiser lights and rack', 'Metro fold specs', 'Commuter sizing']],
    ['Kids bikes', ['Sprout 16 overview', 'Sprout 20 overview', 'Kids size chart']],
    ['Bike prices', ['Bike price list (old page)', ['Bike price list 2026', true], 'Seasonal offers']],
  ]],
  ['E-bikes', [
    ['Volt range', ['Volt overview', 'Volt battery and charging', 'Volt motor modes', 'Volt frame sizes']],
    ['Cargo e-bikes', ['Hauler overview', 'Hauler load capacity', 'Child seat options']],
    ['Batteries', ['Battery care', 'Range by mode', 'Spare batteries']],
    ['E-bike prices', ['Volt price (old page)', ['Approved e-bike prices 2026', true], 'Hauler price list']],
    ['E-bike rules', ['Speed limits', 'Insurance basics']],
  ]],
  ['Parts and accessories', [
    ['Helmets', ['Helmet sizes', 'Helmet safety ratings', 'Kids helmets']],
    ['Locks and lights', ['Lock ratings', 'Front and rear lights', 'Lights for e-bikes']],
    ['Tyres and tubes', ['Tyre sizes', 'Tubeless setup', 'Puncture-proof tyres', 'Tyre pressure guide']],
    ['Spare parts', ['Chains and cassettes', 'Brake pads', 'Saddles', 'Pedals']],
  ]],
  ['Workshop and service', [
    ['Repairs', ['Brake service', 'Gear tune-up', 'Puncture repair', 'Wheel truing']],
    ['Service plans', ['Basic plan', 'Premium plan', 'Plan comparison']],
    ['Booking', ['Book a service', 'Workshop hours', 'Drop-off and pick-up']],
    ['Warranty', ['Frame warranty', 'Battery warranty', 'Claims process']],
  ]],
  ['Orders and delivery', [
    ['Ordering', ['How to order', 'Payment options', 'Finance plans']],
    ['Delivery', ['Delivery fee', 'Delivery times', 'Assembly on delivery']],
    ['Returns', ['30-day returns', 'Refund times', 'Exchange policy']],
  ]],
  ['Company and contact', [
    ['About us', ['About Northwind', 'Our stores', 'Careers']],
    ['Contact', ['Phone and chat', 'Store addresses', ['Opening hours', true]]],
  ]],
];

let n = 0;
export const domains: Domain[] = raw.map(([dt, topics], di) => ({
  id: `D${di + 1}`, title: dt,
  topics: topics.map(([tt, secs], ti) => ({
    id: `T${di + 1}.${ti + 1}`, title: tt,
    sections: secs.map((s) => {
      n += 1;
      const [title, approved] = typeof s === 'string' ? [s, false] : s;
      return {id: `S${String(n).padStart(3, '0')}`, title, approved};
    }),
  })),
}));

export const allSections = domains.flatMap((d) => d.topics.flatMap((t) => t.sections));
const byTitle = (t: string) => {
  const s = allSections.find((x) => x.title === t);
  if (!s) throw new Error(`no section ${t}`);
  return s.id;
};

export const calculators: Calculator[] = [
  {id: 'order_total', title: 'order_total', reads: ['Approved e-bike prices 2026', 'Delivery fee'].map(byTitle)},
  {id: 'battery_range', title: 'battery_range', reads: ['Range by mode', 'Volt battery and charging'].map(byTitle)},
  {id: 'finance_monthly', title: 'finance_monthly', reads: ['Finance plans', 'Bike price list 2026'].map(byTitle)},
  {id: 'delivery_eta', title: 'delivery_eta', reads: ['Delivery times'].map(byTitle)},
];

export const strikes: Strike[] = [
  {question: 'Volt e-bike price with delivery?', used: ['Approved e-bike prices 2026', 'Delivery fee'].map(byTitle),
    considered: ['Volt price (old page)', 'Volt overview', 'Seasonal offers'].map(byTitle), calcs: ['order_total']},
  {question: 'Trail 500 frame sizes?', used: ['Trail 500 frame sizes'].map(byTitle),
    considered: ['Trail 500 overview', 'Commuter sizing', 'Kids size chart'].map(byTitle), calcs: []},
  {question: 'Book a brake service', used: ['Brake service', 'Book a service'].map(byTitle),
    considered: ['Workshop hours', 'Basic plan'].map(byTitle), calcs: []},
];

export const stats = {
  domains: domains.length,
  topics: domains.reduce((a, d) => a + d.topics.length, 0),
  sections: allSections.length,
  calculators: calculators.length,
};
