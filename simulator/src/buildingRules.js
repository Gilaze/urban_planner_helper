// What a building can be replaced with, and the size each choice needs.
//
// These are illustrative planning defaults for sketching, not San Francisco code: typical
// heights and the smallest footprint on which each use is plausible. A choice whose rule
// is not met cannot be applied, and the card says why.
//
//   render   map style it is drawn with (an id from meta.types: colour + window pattern)
//   height   default height in metres when the type is picked
//   minH/maxH  allowed height range (m)
//   minArea  minimum building footprint (m²)

export const BUILDING_RULES = [
  // Parcel Planner service types
  { id: 'hospital', label: 'Hospital', group: 'Services', render: 'medical', height: 30, minH: 12, maxH: 120, minArea: 2000 },
  { id: 'clinic', label: 'Clinic', group: 'Services', render: 'medical', height: 8, minH: 4, maxH: 30, minArea: 150 },
  { id: 'pharmacy', label: 'Pharmacy', group: 'Services', render: 'retail', height: 6, minH: 4, maxH: 20, minArea: 80 },
  { id: 'grocery', label: 'Grocery store', group: 'Services', render: 'retail', height: 7, minH: 4, maxH: 20, minArea: 300 },
  { id: 'bank_credit_union', label: 'Bank / credit union', group: 'Services', render: 'office', height: 8, minH: 4, maxH: 40, minArea: 100 },
  { id: 'school', label: 'School (K-12)', group: 'Services', render: 'civic', height: 12, minH: 6, maxH: 30, minArea: 1500 },
  // Map building types
  { id: 'tower', label: 'Tower / high-rise', group: 'Building types', render: 'tower', height: 80, minH: 50, maxH: 350, minArea: 300 },
  { id: 'office', label: 'Office', group: 'Building types', render: 'office', height: 30, minH: 8, maxH: 250, minArea: 200 },
  { id: 'apartment', label: 'Apartment / condo', group: 'Building types', render: 'apartment', height: 18, minH: 9, maxH: 120, minArea: 150 },
  { id: 'flats', label: 'Flats (2-4 units)', group: 'Building types', render: 'flats', height: 10, minH: 6, maxH: 20, minArea: 80 },
  { id: 'house', label: 'Single-family house', group: 'Building types', render: 'house', height: 8, minH: 4, maxH: 14, minArea: 40 },
  { id: 'mixed', label: 'Mixed-use (homes over shops)', group: 'Building types', render: 'mixed', height: 15, minH: 8, maxH: 60, minArea: 100 },
  { id: 'retail', label: 'Retail / commercial', group: 'Building types', render: 'retail', height: 6, minH: 4, maxH: 30, minArea: 60 },
  { id: 'hotel', label: 'Hotel', group: 'Building types', render: 'hotel', height: 30, minH: 10, maxH: 200, minArea: 300 },
  { id: 'civic', label: 'Civic / culture', group: 'Building types', render: 'civic', height: 12, minH: 4, maxH: 60, minArea: 150 },
  { id: 'medical', label: 'Medical office', group: 'Building types', render: 'medical', height: 15, minH: 6, maxH: 80, minArea: 150 },
  { id: 'industrial', label: 'Industrial / PDR', group: 'Building types', render: 'industrial', height: 10, minH: 5, maxH: 30, minArea: 200 },
  { id: 'other', label: 'Other / accessory', group: 'Building types', render: 'other', height: 5, minH: 3, maxH: 20, minArea: 0 },
];

const BY_ID = new Map(BUILDING_RULES.map((r) => [r.id, r]));

export function ruleFor(id) {
  return BY_ID.get(id) || null;
}

/** Rule matching an original building's map type (meta.types[i].id), for the starting selection. */
export function ruleForMapType(mapTypeId) {
  return BY_ID.get(mapTypeId) || BY_ID.get('other');
}

/** Returns { ok, reasons[] } for building `rule` at `height` m on a footprint of `area` m². */
export function validateBuilding(rule, height, area) {
  const reasons = [];
  if (!rule) return { ok: false, reasons: ['Choose a building type.'] };
  if (!Number.isFinite(height)) reasons.push(`Enter a height for ${rule.label}.`);
  else if (height < rule.minH || height > rule.maxH) reasons.push(`${rule.label} must be ${rule.minH}–${rule.maxH} m tall.`);
  if (area < rule.minArea) {
    reasons.push(`${rule.label} needs a footprint of at least ${rule.minArea.toLocaleString()} m²; this one is ${Math.round(area).toLocaleString()} m².`);
  }
  return { ok: reasons.length === 0, reasons };
}
