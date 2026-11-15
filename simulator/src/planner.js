import { containsPoint, polygons } from './parcelGeometry.js';

const API = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '');
const DEFAULT_CENTER = { lon: -122.399, lat: 37.7905 };
const CATEGORIES = {
  bank_credit_union: 'Bank / credit union', clinic: 'Clinic', grocery: 'Grocery',
  pharmacy: 'Pharmacy', school: 'School',
};
const OBJECTIVES = { need: 'Service need', economic: 'Economics', sustainability: 'Sustainability' };
const FRESHNESS = { fixture: 'Demo data', live: 'Live parcels', cached: 'Cached parcels', stale: 'Last verified parcel snapshot' };
const REVIEW_SOURCES = { model: 'Model', category_baseline: 'Category median', fixture: 'Demo estimate' };
const count = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

function element(tag, text, testId) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (testId) node.dataset.testid = testId;
  return node;
}

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);
  const result = await response.json().catch(() => null);
  if (!response.ok || !result) throw new Error(result?.error?.message || 'Parcel Planner is unavailable. Try again.');
  return result;
}

function listParcels(bounds, signal) {
  return request(`/parcels?bbox=${bounds.join(',')}`, { signal });
}

function boundsAround({ lon, lat }, dx, dy = dx) {
  return [lon - dx, lat - dy, lon + dx, lat + dy];
}

function formatRange(range, format) {
  if (!range) return 'Unavailable';
  return `${format.format(range.p50)} (P10–P90: ${format.format(range.p10)}–${format.format(range.p90)})`;
}

function addMetric(list, label, value) {
  list.append(element('dt', label), element('dd', value));
}

function footprintSvg(geometry) {
  const parts = polygons(geometry);
  if (!parts.length) return null;
  const points = parts.flat(2);
  const west = Math.min(...points.map(([lon]) => lon));
  const east = Math.max(...points.map(([lon]) => lon));
  const south = Math.min(...points.map(([, lat]) => lat));
  const north = Math.max(...points.map(([, lat]) => lat));
  const lonScale = Math.cos((north + south) * Math.PI / 360);
  const scale = Math.min(180 / ((east - west) * lonScale || 1), 85 / (north - south || 1));
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 200 105');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Parcel footprint from above');
  const path = document.createElementNS(svg.namespaceURI, 'path');
  path.setAttribute('d', parts.flatMap((rings) => rings.map((ring) =>
    `M ${ring.map(([lon, lat]) => `${10 + (lon - west) * lonScale * scale},${10 + (north - lat) * scale}`).join(' L ')} Z`
  )).join(' '));
  path.setAttribute('fill-rule', 'evenodd');
  svg.append(path);
  return svg;
}

export function createPlanner(root) {
  const panel = element('aside');
  panel.className = 'panel planner';
  panel.setAttribute('aria-label', 'Parcel Planner');
  panel.innerHTML = `
    <details open>
      <summary>Parcel Planner</summary>
      <div class="planner-body">
        <label for="planner-parcel">Parcel</label>
        <div class="planner-parcels">
          <select id="planner-parcel" data-testid="parcel-selector" disabled>
            <option value="">Select a parcel</option>
          </select>
          <button type="button" data-find>Find nearby</button>
        </div>
        <p class="planner-note" data-find-status aria-live="polite"></p>
        <p class="planner-note" data-testid="data-freshness"></p>
        <div data-testid="comparison-results" data-status="idle">
          <p class="planner-note" data-status-text role="status"></p>
          <p data-error role="alert" hidden></p>
          <button type="button" data-retry hidden>Retry comparison</button>
          <div data-testid="massing-preview" class="planner-preview" hidden>
            <p data-preview-text></p>
            <div data-footprint></div>
            <div class="planner-views">
              <button type="button" data-testid="view-3d" aria-pressed="true">3D</button>
              <button type="button" data-testid="view-2d" aria-pressed="false">2D</button>
            </div>
          </div>
          <details class="planner-preview" data-permits open hidden>
            <summary>Preliminary permit checklist</summary>
            <form data-permit-form>
              <label>Proposed use <select data-permit-use></select></label>
              <label data-permit-area-row hidden>Gross floor area (sq ft)
                <input type="number" min="1" step="any" data-permit-area>
              </label>
              <button type="submit">Check permits</button>
            </form>
            <p class="planner-note" data-permit-status role="status"></p>
            <ol data-permit-steps></ol>
          </details>
          <details class="planner-weights" hidden>
            <summary>Ranking weights</summary>
            <div data-sliders></div>
            <button type="button" data-reset>Reset</button>
          </details>
          <p class="planner-note" data-testid="unavailable-reason" hidden></p>
          <div data-alternatives></div>
        </div>
      </div>
    </details>`;
  root.append(panel);

  const find = (selector) => panel.querySelector(selector);
  const byTestId = (name) => find(`[data-testid="${name}"]`);
  const select = byTestId('parcel-selector');
  const results = byTestId('comparison-results');
  const freshness = byTestId('data-freshness');
  const unavailable = byTestId('unavailable-reason');
  const preview = byTestId('massing-preview');
  const error = find('[data-error]');
  const retry = find('[data-retry]');
  const listStatus = find('[data-find-status]');
  const statusText = find('[data-status-text]');
  const findButton = find('[data-find]');
  const alternatives = find('[data-alternatives]');
  const weights = find('.planner-weights');
  const permits = find('[data-permits]');
  const permitUse = find('[data-permit-use]');
  const permitArea = find('[data-permit-area]');
  const permitStatus = find('[data-permit-status]');
  const permitSteps = find('[data-permit-steps]');
  const controls = find('[data-sliders]');
  const options = new Map();
  const sliders = {};
  let map = null;
  let parcel = null;
  let comparison = null;
  let candidate = null;
  let view = '3d';
  let parcelRequest = null;
  let compareRequest = null;
  let permitRequest = null;
  let compareTimer;
  let listSource = null;

  function setSource(source) {
    freshness.textContent = FRESHNESS[source?.freshness] || '';
  }

  function updateOptions(features) {
    options.clear();
    const candidates = features.filter((feature) => feature.properties.vacancy_status !== 'not_vacant');
    candidates.sort((a, b) => Number(b.properties.vacancy_status === 'source_indicated_vacant') -
      Number(a.properties.vacancy_status === 'source_indicated_vacant'));
    for (const feature of candidates) options.set(feature.id, feature);
    if (parcel) options.set(parcel.id, parcel);
    select.replaceChildren(new Option('Select a parcel', ''));
    for (const feature of options.values()) {
      const label = feature.properties.vacancy_status === 'source_indicated_vacant' ? 'Vacancy indicated' : 'Unknown';
      select.add(new Option(`${label} · ${feature.id}`, feature.id));
    }
    select.value = parcel?.id || '';
    select.disabled = options.size === 0;
  }

  async function refreshParcels() {
    parcelRequest?.abort();
    const controller = new AbortController();
    parcelRequest = controller;
    const { signal } = controller;
    const center = map?.center() || DEFAULT_CENTER;
    listStatus.textContent = 'Finding nearby parcels…';
    try {
      let data = await listParcels(boundsAround(center, 0.0022, 0.0018), signal);
      if (!data.features.length) {
        data = await listParcels(boundsAround(center, 0.025, 0.022), signal);
      }
      if (signal.aborted) return;
      listSource = data.source;
      if (!parcel) setSource(listSource);
      updateOptions(data.features);
      listStatus.textContent = options.size ? (map ? 'Click a vacant lot on the map or choose a parcel.' : '') :
        'No candidate lots nearby. Move the map and try again.';
      if (data.truncated) listStatus.textContent += ' The parcel list is limited to this area.';
    } catch (cause) {
      if (!signal.aborted) listStatus.textContent = cause.message;
    }
  }

  function renderPreview() {
    const massing = comparison?.massing;
    const visible = Boolean(candidate && massing);
    preview.hidden = !visible;
    delete preview.dataset.kind;
    const geometry = massing?.geometry;
    map?.draw(parcel, visible && view === '3d' ? massing : null);
    if (!visible) return;
    preview.dataset.kind = geometry ? 'volume' : 'outline';
    find('[data-preview-text]').textContent = geometry
      ? `${CATEGORIES[candidate]} · Illustrative ${massing.height_m} m building`
      : `${CATEGORIES[candidate]} · Parcel outline. Building footprint unavailable.`;
    const footprint = (!map || view === '2d') && footprintSvg(geometry || parcel?.geometry);
    find('[data-footprint]').replaceChildren(...(footprint ? [footprint] : []));
    byTestId('view-3d').disabled = !map;
    byTestId('view-3d').setAttribute('aria-pressed', String(Boolean(map) && view === '3d'));
    byTestId('view-2d').setAttribute('aria-pressed', String(!map || view === '2d'));
  }

  function selectCandidate(id) {
    candidate = id;
    for (const button of alternatives.querySelectorAll('[aria-pressed]')) {
      button.setAttribute('aria-pressed', String(button.dataset.category === id));
    }
    renderPreview();
    permitUse.value = id;
    updatePermitForm();
    checkPermits();
  }

  function updatePermitForm() {
    find('[data-permit-area-row]').hidden = permitUse.value !== 'grocery';
  }

  function resetPermits() {
    permitRequest?.abort();
    permitSteps.replaceChildren();
    permitStatus.textContent = '';
  }

  async function checkPermits() {
    resetPermits();
    if (!parcel || !permitUse.value) return;
    const controller = new AbortController();
    permitRequest = controller;
    const { signal } = controller;
    permitStatus.textContent = 'Checking zoning and review steps…';
    try {
      const result = await request('/permits/check', {
        method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          parcel_id: parcel.id,
          category_id: permitUse.value,
          floor_area_sqft: permitUse.value === 'grocery' && permitArea.value ? Number(permitArea.value) : null,
        }),
      });
      if (signal.aborted) return;
      const zoning = result.zoning_districts.join(', ') || 'Unconfirmed';
      const overlays = result.special_use_districts.join(', ');
      permitStatus.textContent = `Zoning: ${zoning}${overlays ? ` · Special use districts: ${overlays}` : ''}`;
      for (const step of result.steps) {
        const item = element('li');
        const link = element('a', step.title);
        link.href = step.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        item.append(link, element('p', step.detail));
        permitSteps.append(item);
      }
    } catch (cause) {
      if (!signal.aborted) permitStatus.textContent = cause.message;
    }
  }

  function renderComparison() {
    alternatives.replaceChildren();
    unavailable.hidden = true;
    if (!comparison) return;
    if (comparison.reason || comparison.unavailable_dimensions.length) {
      const labels = comparison.unavailable_dimensions.map((id) => OBJECTIVES[id]);
      unavailable.textContent = comparison.reason
        ? `Ranking unavailable · ${comparison.reason.replaceAll('_', ' ')}`
        : `Unavailable for ranking · ${labels.join(', ')}`;
      unavailable.hidden = false;
    }
    for (const row of comparison.alternatives) {
      const id = row.category_id;
      const card = element('article', null, `alternative-${id}`);
      card.className = 'planner-alternative';
      if (row.rank != null) card.dataset.rank = row.rank;
      const button = element('button', `${row.rank == null ? '' : `#${row.rank} · `}${CATEGORIES[id]}`, `candidate-${id}`);
      button.type = 'button';
      button.dataset.category = id;
      button.setAttribute('aria-pressed', String(id === candidate));
      button.addEventListener('click', () => selectCandidate(id));
      const values = element('dl', null, `raw-metrics-${id}`);
      if (row.total != null) addMetric(values, 'Weighted score', count.format(row.total));
      addMetric(values, 'Local sales tax / year', formatRange(row.economic.retained_sales_tax_usd_per_year, usd));
      addMetric(values, 'Operating jobs (FTE)', formatRange(row.economic.operating_fte, count));
      const reviews = row.review_activity;
      addMetric(values, 'Predicted Google reviews', reviews.predicted_log1p_reviews_count == null ? 'Unavailable' :
        `${count.format(Math.expm1(reviews.predicted_log1p_reviews_count))} · ${REVIEW_SOURCES[reviews.source]}`);
      addMetric(values, 'Access gap reduction', row.need.deficit_reduction == null ? 'Unavailable' :
        `${count.format(100 * row.need.deficit_reduction)} percentage points`);
      addMetric(values, 'Avoided vehicle miles / day', row.sustainability.avoided_vehicle_miles_per_day == null ?
        'Unavailable' : count.format(row.sustainability.avoided_vehicle_miles_per_day.base));
      card.append(button, values);
      alternatives.append(card);
    }
    renderPreview();
  }

  function sliderValues(prefix, keys) {
    return Object.fromEntries(Object.keys(keys).map((id) => [id, Number(sliders[`${prefix}-${id}`].value)]));
  }

  async function compare() {
    if (!parcel) return;
    const objectiveWeights = sliderValues('objective', OBJECTIVES);
    compareRequest?.abort();
    results.dataset.status = 'loading';
    statusText.textContent = 'Comparing scenarios…';
    error.hidden = retry.hidden = true;
    if (!Object.values(objectiveWeights).some(Boolean)) {
      results.dataset.status = 'error';
      statusText.textContent = '';
      error.textContent = 'Give at least one objective a positive weight.';
      error.hidden = false;
      return;
    }
    const controller = new AbortController();
    compareRequest = controller;
    const { signal } = controller;
    try {
      const data = await request('/scenarios/compare', {
        method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          parcel_id: parcel.id,
          objective_weights: objectiveWeights,
          category_priorities: sliderValues('priority', CATEGORIES),
        }),
      });
      if (signal.aborted) return;
      comparison = data;
      setSource(data.parcel_source);
      results.dataset.status = 'ready';
      statusText.textContent = 'Choose a service to preview its building.';
      renderComparison();
    } catch (cause) {
      if (signal.aborted) return;
      results.dataset.status = 'error';
      statusText.textContent = '';
      error.textContent = cause.message;
      error.hidden = retry.hidden = false;
    }
  }

  function scheduleCompare() {
    clearTimeout(compareTimer);
    compareRequest?.abort();
    compareTimer = setTimeout(compare, 150);
  }

  function chooseParcel(feature) {
    clearTimeout(compareTimer);
    compareRequest?.abort();
    parcel = feature || null;
    comparison = candidate = null;
    renderComparison();
    renderPreview();
    setSource(listSource);
    results.dataset.parcelId = parcel?.id || '';
    results.dataset.status = 'idle';
    statusText.textContent = '';
    error.hidden = retry.hidden = true;
    weights.hidden = !parcel;
    permits.hidden = !parcel;
    resetPermits();
    permitArea.value = '';
    permitUse.value = '';
    updatePermitForm();
    if (!parcel) return;
    panel.querySelector('details').open = true;
    map?.focus(parcel, view);
    compare();
  }

  function addSlider(prefix, id, title) {
    const key = `${prefix}-${id}`;
    const label = element('label', title);
    const slider = element('input', null, key);
    slider.type = 'range';
    slider.min = 0;
    slider.max = 2;
    slider.step = 0.25;
    slider.value = 1;
    const value = element('output', '1');
    slider.addEventListener('input', () => {
      value.textContent = slider.value;
      scheduleCompare();
    });
    label.append(slider, value);
    controls.append(label);
    sliders[key] = slider;
  }

  Object.entries(OBJECTIVES).forEach(([id, title]) => addSlider('objective', id, title));
  controls.append(element('p', 'Service need priorities'));
  Object.entries(CATEGORIES).forEach(([id, title]) => addSlider('priority', id, title));
  find('[data-reset]').addEventListener('click', () => {
    for (const slider of Object.values(sliders)) {
      slider.value = 1;
      slider.nextElementSibling.textContent = '1';
    }
    scheduleCompare();
  });
  for (const mode of ['2d', '3d']) {
    byTestId(`view-${mode}`).addEventListener('click', () => {
      view = mode;
      if (parcel) map?.focus(parcel, mode);
      renderPreview();
    });
  }
  permitUse.add(new Option('Choose a use', ''));
  for (const [id, name] of Object.entries(CATEGORIES)) permitUse.add(new Option(name, id));
  permitUse.addEventListener('change', () => {
    if (comparison && permitUse.value) {
      selectCandidate(permitUse.value);
      return;
    }
    updatePermitForm();
    checkPermits();
  });
  find('[data-permit-form]').addEventListener('submit', (event) => {
    event.preventDefault();
    checkPermits();
  });
  retry.addEventListener('click', compare);
  findButton.addEventListener('click', refreshParcels);
  select.addEventListener('change', () => {
    parcelRequest?.abort();
    chooseParcel(options.get(select.value));
  });
  refreshParcels();

  return {
    connectMap(connectedMap) {
      map = connectedMap;
      if (parcel) map.focus(parcel, view);
      renderPreview();
      if (!parcel && options.size) listStatus.textContent = 'Click a vacant lot on the map or choose a parcel.';
    },
    redraw: renderPreview,
    async pick(point) {
      parcelRequest?.abort();
      const controller = new AbortController();
      parcelRequest = controller;
      const { signal } = controller;
      listStatus.textContent = 'Looking up this lot…';
      try {
        const data = await listParcels(boundsAround(point, 0.00004), signal);
        if (signal.aborted) return;
        const hit = data.features.find((feature) => feature.properties.vacancy_status !== 'not_vacant' &&
          containsPoint(feature.geometry, point));
        if (!hit) {
          listStatus.textContent = 'No candidate lot at this point.';
          return;
        }
        listSource = data.source;
        const nearby = [...options.values(), hit];
        chooseParcel(hit);
        updateOptions(nearby);
        listStatus.textContent = 'Click a vacant lot on the map or choose a parcel.';
      } catch (cause) {
        if (!signal.aborted) listStatus.textContent = cause.message;
      }
    },
  };
}
