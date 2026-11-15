import { containsPoint } from './parcelGeometry.js';
const API = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '');
const USES = ['clinic', 'pharmacy', 'grocery', 'bank_credit_union', 'school'];
const number = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const node = (tag, text) => { const el = document.createElement(tag); if (text != null) el.textContent = text; return el; };
async function request(path, body) {
  const response = await fetch(`${API}${path}`, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
  const data = await response.json();
  if (!response.ok) throw Error(data?.error?.message || 'Details are unavailable. Try again.');
  return data;
}
export function mountLotEffects(card, { point, feature, category, tract, design }) {
  const previous = card.querySelector('[data-lot-effects]');
  const expanded = previous?.open;
  previous?.remove();
  const details = node('details'); details.dataset.lotEffects = ''; details.dataset.testid = 'lot-effects';
  details.append(node('summary', 'Tract effects & details'));
  const body = node('div'); body.setAttribute('aria-live', 'polite'); details.append(body); card.append(details);
  let loaded = false;
  details.addEventListener('toggle', async () => {
    if (!details.open || loaded) return;
    loaded = true; body.textContent = 'Loading estimates…';
    if (!USES.includes(category)) { body.textContent = 'Estimates are available for clinics, pharmacies, grocery stores, banks and schools. Choose one of these services to see its tract effects.'; return; }
    try {
      if (!feature) {
        const d = .00004;
        const data = await request(`/parcels?bbox=${[point.lon-d,point.lat-d,point.lon+d,point.lat+d].join(',')}`);
        feature = data.features.find(row => row.properties.vacancy_status === 'source_indicated_vacant' && containsPoint(row.geometry,point));
      }
      if (!feature) throw Error('This building could not be matched to a verified vacant lot, so tract effects are unavailable.');
      const [comparison, ranking] = await Promise.all([
        request('/scenarios/compare', { parcel_id: feature.id, objective_weights: { need: 1, economic: 1, sustainability: 1 } }),
        tract ? request(`/planning/areas/${tract.id}/sites?category=${category}&parcel_id=${encodeURIComponent(feature.id)}&limit=1`).catch(() => null) : null,
      ]);
      if (!details.isConnected) return;
      body.replaceChildren();
      const site = ranking?.sites[0];
      body.append(node('p', tract?.properties.name || 'Tract unavailable'));
      if (site) body.append(node('strong', site.score == null ? 'Need unknown' : `${site.score}/100 · ${site.need_level}`), node('p', site.explanation));
      const row = comparison.alternatives.find(row => row.category_id === category);
      const list = node('dl');
      const metric = (label, value) => list.append(node('dt',label),node('dd',value));
      const range = (value, format) => value ? `${format.format(value.p50)} (${format.format(value.p10)}–${format.format(value.p90)})` : 'Unavailable';
      metric('Access gap reduction', row?.need.deficit_reduction == null ? 'Unavailable' : `${number.format(row.need.deficit_reduction*100)} percentage points`);
      metric('Operating jobs (FTE)', range(row?.economic.operating_fte,number));
      metric('Local sales tax / year', range(row?.economic.retained_sales_tax_usd_per_year,money));
      metric('Avoided vehicle miles / day', row?.sustainability.avoided_vehicle_miles_per_day == null ? 'Unavailable' : number.format(row.sustainability.avoided_vehicle_miles_per_day.base));
      body.append(list,node('p','Scenario estimates for one facility; ranges are P10–P90. Changing sketch height does not recalculate these effects.'));
      const permitBody=node('div');body.append(permitBody);permitBody.textContent='Loading permit guidance…';
      try {
        const permits = await request('/permits/check',{parcel_id:feature.id,category_id:category,floor_area_sqft:category==='grocery'&&design?.floor_area_m2?design.floor_area_m2*10.7639104167:null});
        if (!details.isConnected) return;
        permitBody.replaceChildren(node('strong','Permit guidance'));
        const steps=node('ol');for(const step of permits.steps){const li=node('li');const a=node('a',step.title);a.href=step.url;a.target='_blank';a.rel='noopener noreferrer';li.append(a,node('p',step.detail));steps.append(li);}permitBody.append(steps);
      } catch(error){permitBody.textContent=error.message;}
    } catch(error) { body.textContent=error.message;loaded=false; }
  });
  if (expanded) details.open = true;
}
