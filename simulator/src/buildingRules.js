// San Francisco rules for replacing a building in the sketch.
//
// Two checks, both from the lot the building stands on:
//   1. Height: the Planning Code height limit from the Zoning Map - Height and Bulk Districts
//      (DataSF h9wh-cg3m, e.g. "40-X" = 40 ft). Bulk controls (the letter) are not modelled.
//      Districts whose height is set by a special plan (OS open space, Candlestick Point,
//      Hunters Point, Special Height Districts) have no numeric limit here.
//   2. Use: whether the use is allowed in the lot's zoning district, SIMPLIFIED from the
//      Planning Code zoning control tables (Article 2 residential/commercial/PDR, Article 7
//      neighborhood commercial, Article 8 mixed use). P = principally permitted,
//      C = needs Conditional Use authorization, N = not permitted. Districts are grouped into
//      families; individual districts have exceptions (floor-by-floor controls, size limits,
//      named NCDs). Always check the code for a real proposal.
//
// Not SF rules (sketch conventions): a default height when a type is picked, a 3 m floor,
// and "Tower" meaning 50 m or taller (the map's own definition).

export const FT_TO_M = 0.3048;

export const BUILDING_RULES = [
  // Parcel Planner service types (use category used for the zoning check in brackets)
  { id: 'hospital', label: 'Hospital', group: 'Services', render: 'medical', height: 30 },
  { id: 'clinic', label: 'Clinic', group: 'Services', render: 'medical', height: 8 },
  { id: 'pharmacy', label: 'Pharmacy', group: 'Services', render: 'retail', height: 6 },
  { id: 'grocery', label: 'Grocery store', group: 'Services', render: 'retail', height: 7 },
  { id: 'bank_credit_union', label: 'Bank / credit union', group: 'Services', render: 'office', height: 8 },
  { id: 'school', label: 'School (K-12)', group: 'Services', render: 'civic', height: 12 },
  // Map building types
  { id: 'tower', label: 'Tower / high-rise', group: 'Building types', render: 'tower', height: 80, minH: 50 },
  { id: 'office', label: 'Office', group: 'Building types', render: 'office', height: 30 },
  { id: 'apartment', label: 'Apartment / condo', group: 'Building types', render: 'apartment', height: 18 },
  { id: 'flats', label: 'Flats (2-4 units)', group: 'Building types', render: 'flats', height: 10 },
  { id: 'house', label: 'Single-family house', group: 'Building types', render: 'house', height: 8 },
  { id: 'mixed', label: 'Mixed-use (homes over shops)', group: 'Building types', render: 'mixed', height: 15 },
  { id: 'retail', label: 'Retail / commercial', group: 'Building types', render: 'retail', height: 6 },
  { id: 'hotel', label: 'Hotel', group: 'Building types', render: 'hotel', height: 30 },
  { id: 'civic', label: 'Civic / culture', group: 'Building types', render: 'civic', height: 12 },
  { id: 'medical', label: 'Medical office', group: 'Building types', render: 'medical', height: 15 },
  { id: 'industrial', label: 'Industrial / PDR', group: 'Building types', render: 'industrial', height: 10 },
  { id: 'other', label: 'Other / accessory', group: 'Building types', render: 'other', height: 5 },
];
for (const r of BUILDING_RULES) r.minH = r.minH ?? 3;

export const MAX_SKETCH_HEIGHT_M = 350;

// Zoning district -> family
export function zoningFamily(code) {
  if (!code) return 'unknown';
  const c = code.toUpperCase();
  if (c === 'P') return 'public';
  if (/^RH-/.test(c)) return 'rh';
  if (/^RTO-C$/.test(c)) return 'rc';
  if (/^(RM-|RTO)/.test(c)) return 'rm';
  if (/^RC-/.test(c)) return 'rc';
  if (/^(NC|NCD|NCT|CRNC|CCB|CVR|RCD)/.test(c)) return 'nc';
  if (c === 'C-2') return 'c2';
  if (/^C-3/.test(c)) return 'c3';
  if (/^(PDR|M-1|M-2|SALI|SLI)/.test(c)) return 'pdr';
  if (/^(UMU|MUG|MUR|MUO|WMUG|WMUO|CMUO|RED|SPD|RSD|SSO)/.test(c)) return 'mixed_use';
  return 'special_plan'; // redevelopment / special use districts: MB-*, HP-RA, PM-*, *DTR, P70-MU, MR-MU, ...
}

export const FAMILY_LABEL = {
  rh: 'Residential, house (RH)',
  rm: 'Residential, mixed (RM/RTO)',
  rc: 'Residential-commercial (RC)',
  nc: 'Neighborhood commercial (NC)',
  c2: 'Community business (C-2)',
  c3: 'Downtown commercial (C-3)',
  pdr: 'Production, distribution & repair (PDR)',
  mixed_use: 'Eastern Neighborhoods mixed use',
  public: 'Public (P)',
  special_plan: 'Special plan / redevelopment area',
  unknown: 'No zoning record',
};

// Simplified use table: P permitted, C conditional use, N not permitted.
//          hospital clinic pharmacy grocery bank school tower office apartment flats house mixed retail hotel civic medical industrial other
const COLS = ['hospital', 'clinic', 'pharmacy', 'grocery', 'bank_credit_union', 'school', 'tower', 'office', 'apartment',
  'flats', 'house', 'mixed', 'retail', 'hotel', 'civic', 'medical', 'industrial', 'other'];
const TABLE = {
  rh:        'C N N N N C N N N P P N N N C N N P',
  rm:        'C C N N N C N N P P P N N N C C N P',
  rc:        'C P P P P C N C P P P P P C C P N P',
  nc:        'C P P P C C N P P P P P P C C P N P',
  c2:        'C P P P P C N P P P P P P P P P C P',
  c3:        'C P P P P C P P P P P P P P P P N P',
  pdr:       'N N P P C N N N N N N N P N C N P P',
  mixed_use: 'C P P P P C N C P P P P P C P P P P',
  public:    'P P N N N P N N N N N N N N P P N P',
};

const USE = Object.fromEntries(Object.entries(TABLE).map(([f, row]) => {
  const v = row.trim().split(/\s+/);
  return [f, Object.fromEntries(COLS.map((c, i) => [c, v[i]]))];
}));

const BY_ID = new Map(BUILDING_RULES.map((r) => [r.id, r]));
export const ruleFor = (id) => BY_ID.get(id) || null;
export const ruleForMapType = (mapTypeId) => BY_ID.get(mapTypeId) || BY_ID.get('other');

/**
 * ctx: { zoning, heightLimitFt (0 = none), specialPlan, openSpace } for the building's lot.
 * Returns the height range the slider should offer for this lot.
 */
export function heightRange(rule, ctx) {
  const limitM = ctx && ctx.heightLimitFt > 0 ? ctx.heightLimitFt * FT_TO_M : null; // exact; shown to 0.1 m
  return { min: rule.minH, max: limitM != null ? Math.max(rule.minH, Math.floor(limitM)) : MAX_SKETCH_HEIGHT_M, limitM };
}

/** Default height for a type on this lot: the type's typical height, capped at the SF limit. */
export function defaultHeight(rule, ctx) {
  const { limitM } = heightRange(rule, ctx);
  return limitM != null ? Math.max(rule.minH, Math.min(rule.height, Math.floor(limitM))) : rule.height;
}

/**
 * Check a replacement against SF rules.
 * Returns { ok, reasons[] (blocking), notes[] (shown, not blocking), summary }.
 */
export function validateBuilding(rule, height, ctx = {}) {
  const reasons = [], notes = [];
  if (!rule) return { ok: false, reasons: ['Choose a building type.'], notes, summary: '' };
  const family = zoningFamily(ctx.zoning);
  const zoneName = ctx.zoning ? `${ctx.zoning} (${FAMILY_LABEL[family]})` : FAMILY_LABEL.unknown;

  // 1. use permission
  if (ctx.openSpace) {
    if (rule.id === 'civic' || rule.id === 'other') notes.push('Open-space height district: structures need case-by-case approval.');
    else reasons.push(`${rule.label} is not allowed in an open-space (OS) height district.`);
  } else if (USE[family]) {
    const p = USE[family][rule.id];
    if (p === 'N') reasons.push(`${rule.label} is not permitted in ${zoneName}.`);
    else if (p === 'C') notes.push(`${rule.label} needs Conditional Use authorization in ${zoneName}.`);
  } else if (family === 'special_plan') {
    notes.push(`Uses in ${zoneName} are set by its special area plan; check the plan.`);
  } else {
    notes.push('No zoning record for this lot; the use was not checked.');
  }

  // 2. height
  const { limitM } = heightRange(rule, ctx);
  if (!Number.isFinite(height)) reasons.push(`Enter a height for ${rule.label}.`);
  else {
    if (height < rule.minH) reasons.push(rule.id === 'tower' ? 'A tower is 50 m or taller on this map.' : `Height must be at least ${rule.minH} m.`);
    if (limitM != null && height > limitM) {
      reasons.push(`The SF height limit here is ${ctx.heightLimitFt} ft (${limitM.toFixed(1)} m); ${height} m is too tall.`);
    } else if (limitM == null && height > MAX_SKETCH_HEIGHT_M) {
      reasons.push(`Height must be at most ${MAX_SKETCH_HEIGHT_M} m.`);
    }
    if (limitM == null && ctx.specialPlan && !ctx.openSpace) notes.push('Height here is set by a special plan, not a numeric limit.');
  }

  const heightText = limitM != null ? `height limit ${ctx.heightLimitFt} ft (${limitM.toFixed(1)} m)`
    : ctx.specialPlan ? 'height set by special plan' : 'no height district on record';
  return { ok: reasons.length === 0, reasons, notes, summary: `SF zoning ${zoneName} · ${heightText}` };
}
