import { SERVICE_COLORS } from './planningStyle.js';
import { containsPoint } from './parcelGeometry.js';

const PICK_OWN = '__pick_own__';

// Plain-language tract needs, three site choices, and one numbered pin per use.
export function createAreaPlanning(root, { request, categories, onPreview, onClear }) {
  const section = document.createElement('section');
  section.className = 'area-planning';
  section.dataset.testid = 'area-planner';
  section.innerHTML = `
    <label for="planning-area">Choose a tract</label>
    <select id="planning-area" data-testid="area-selector" disabled><option>Loading areas…</option></select>
    <p class="planner-note" data-area-population></p>
    <p class="area-recommendation" data-testid="tract-recommendation"></p>
    <div class="area-needs" data-testid="area-needs" aria-label="Suggested building types"></div>
    <p class="planner-note" data-testid="site-ranking-status" aria-live="polite"></p>
    <button type="button" data-area-retry hidden>Try again</button>
    <label for="planning-site" data-site-label>Suggested location</label>
    <select id="planning-site" data-testid="site-selector" disabled><option>Choose an area first</option></select>
    <div class="site-pick-notice" data-testid="site-pick-notice" hidden>
      <p data-pick-message role="status">Click a vacant lot inside the blue tract boundary.</p>
      <button type="button" data-testid="cancel-site-pick">Cancel selection</button>
    </div>
    <div class="area-site-reason" data-site-reason hidden>
      <strong class="area-score" data-testid="site-score"></strong>
      <p data-testid="selected-site-fit"></p>
    </div>
    <div data-size-mount></div>
    <label class="vacant-view-toggle"><input type="checkbox" data-testid="only-vacant-view"> Show only vacant lots</label>
    <details class="area-method" data-testid="tract-details"><summary>Details</summary>
      <p class="planner-note">The same scale applies in every tract: 0–24 No clear need; 25–49 May help; 50–74 Needed; 75–100 High need.</p>
      <p class="planner-note" data-area-source></p>
      <p class="planner-note" data-score-details></p>
      <p class="planner-note" data-exclusion-summary></p>
      <ul class="planner-note" data-exclusion-list></ul>
      <p class="planner-note">Pins 1–5 are alternative uses, not five buildings to build together. Some uses may share a site. A use without a suitable vacant lot has no pin.</p>
      <p class="planner-note">Suggested sizes are concepts. Moving the height slider changes the preview, while scores and financial estimates continue to use the suggested concept.</p>
      <div data-detail-mount></div>
    </details>`;
  root.append(section);
  const find = (selector) => section.querySelector(selector);
  const select = find('[data-testid="area-selector"]');
  const status = find('[data-testid="site-ranking-status"]');
  const sites = find('[data-testid="site-selector"]');
  const needs = find('[data-testid="area-needs"]');
  const retry = find('[data-area-retry]');
  const number = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
  const plural = { school: 'Schools', clinic: 'Clinics', grocery: 'Grocery stores', pharmacy: 'Pharmacies', bank_credit_union: 'Banking services' };
  let collection = null, area = null, category = null, ranking = null, rankings = null, map = null;
  let areaRequest = null, siteRequest = null;
  let selectedSite = null, selectionSerial = 0, userChoseCategory = false;
  let onlyVacant = false;
  let picking = false, customSite = null, pickRequest = null, availableRequest = null, availableSites = [];

  function stopPicking() {
    picking = false;
    pickRequest?.abort();
    availableRequest?.abort();
    availableSites = [];
    map?.drawAvailableSites?.([], category);
    find('[data-testid="site-pick-notice"]').hidden = true;
    map?.setPickingSite?.(false);
  }

  const bestSite = (id) => rankings?.[id]?.sites.find((site) => site.rank != null);
  const orderedNeeds = () => [...area.properties.needs].sort((a, b) => {
    if (rankings) return (bestSite(b.category_id)?.score ?? -1) - (bestSite(a.category_id)?.score ?? -1) || a.category_id.localeCompare(b.category_id);
    return (b.access_deficit_pct ?? -1) - (a.access_deficit_pct ?? -1) || a.category_id.localeCompare(b.category_id);
  });
  const rankedSites = () => ranking?.sites.filter((site) => site.rank != null) || [];

  function node(tag, text) {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    return el;
  }

  function draw() {
    const pins = area ? orderedNeeds().flatMap((need, i) => {
      const options = rankings?.[need.category_id]?.sites.filter((site) => site.rank != null) || [];
      const best = need.category_id === category ? customSite || options.find((site) => site.parcel_id === selectedSite) || options[0] : options[0];
      return best ? [{ ...best.design, parcel_id: best.parcel_id, rank: i + 1, category_id: need.category_id }] : [];
    }) : [];
    map?.drawAreas?.(collection?.features || [], area?.id, pins, category);
  }

  function renderNeeds() {
    needs.replaceChildren();
    const sorted = orderedNeeds();
    if (!category) category = sorted[0].category_id;
    const biggest = sorted[0].access_deficit_pct;
    const topScore = bestSite(sorted[0].category_id)?.score;
    find('[data-testid="tract-recommendation"]').textContent = biggest == null
      ? 'There is not enough local data to compare needs here.'
      : (topScore == null ? biggest > 0 : topScore >= 25) ? `${plural[sorted[0].category_id]} are the biggest need in this area.`
        : 'The current data does not show a clear shortage here. You can still explore the options.';
    sorted.forEach((need, i) => {
      const button = node('button');
      button.type = 'button';
      button.dataset.testid = `area-use-${need.category_id}`;
      button.style.borderLeft = `4px solid ${SERVICE_COLORS[need.category_id]}`;
      button.setAttribute('aria-pressed', String(need.category_id === category));
      const label = node('span', `${i + 1}  ${categories[need.category_id]}`);
      const hint = rankings ? bestSite(need.category_id)?.need_level || (rankings[need.category_id].unranked_count ? 'Need unknown' : 'No suitable site')
        : need.access_deficit_pct == null ? 'Need unknown' : 'Checking sites…';
      button.append(label, node('span', hint));
      button.addEventListener('click', () => {
        if (category === need.category_id) return;
        category = need.category_id;
        userChoseCategory = true;
        stopPicking();
        customSite = null;
        onClear?.();
        renderNeeds();
        if (rankings) { ranking = rankings[category]; renderSites(); }
      });
      needs.append(button);
    });
  }

  function renderSites() {
    const options = rankedSites();
    sites.replaceChildren();
    ['Best Match', 'Second Best Match', 'Third Best Match'].slice(0, options.length).forEach((label, i) =>
      sites.add(new Option(label, options[i].parcel_id)));
    sites.disabled = false;
    if (!options.length) sites.add(new Option('No suitable vacant site found', ''));
    sites.add(new Option('Choose Your Own Vacant Lot…', PICK_OWN));
    selectedSite = options[0]?.parcel_id || null;
    if (selectedSite) sites.value = selectedSite;
    find('[data-site-label]').textContent = `Suggested location for ${categories[category].toLowerCase()}`;
    status.textContent = options.length ? `${options.length === 1 ? 'One suitable location' : `${options.length} suitable locations`} found.`
      : ranking.unranked_count ? 'There is not enough local information to recommend a site.'
        : 'No vacant lot fits this building concept here. Try another type or tract.';
    find('[data-site-reason]').hidden = !selectedSite;
    find('[data-exclusion-summary]').textContent = `${ranking.candidate_count} candidate lots; ${ranking.excluded_count} do not fit the physical concept; ${ranking.unranked_count} lack a usable need estimate.`;
    find('[data-exclusion-list]').replaceChildren(...Object.entries(ranking.exclusion_reasons).map(([reason, count]) => node('li', `${count}: ${reason}`)));
    draw();
    if (selectedSite) previewSite(selectedSite, false);
    else onClear?.();
  }

  async function previewSite(parcelId, focus = false) {
    const serial = ++selectionSerial;
    selectedSite = parcelId;
    const site = customSite?.parcel_id === parcelId ? customSite : ranking.sites.find((row) => row.parcel_id === parcelId);
    find('[data-testid="site-score"]').textContent = site.score == null ? 'Not scored' : `${Math.round(site.score)}/100`;
    find('[data-testid="selected-site-fit"]').textContent = site.explanation;
    find('[data-score-details]').textContent = `Lot ${parcelId}. Tract access gap: ${number.format(ranking.tract_access_deficit_pct)}%. Scoring is the same everywhere: 85% tract need, 10% space, 5% access improvement. A 30% gap reaches full need points. Concept: ${number.format(site.design.footprint_m2)} m² on the ground, ${site.design.floors} floors.`;
    draw();
    try {
      await onPreview(parcelId, category, collection.data_version, { focus });
    } catch (cause) {
      if (serial === selectionSerial && cause.name !== 'AbortError') status.textContent = cause.message;
    }
  }

  async function loadSites() {
    siteRequest?.abort();
    const controller = new AbortController();
    siteRequest = controller;
    ranking = rankings = null;
    selectedSite = null;
    selectionSerial++;
    sites.replaceChildren(new Option('Finding locations…', ''));
    sites.disabled = true;
    find('[data-site-reason]').hidden = true;
    retry.hidden = true;
    status.textContent = 'Finding suitable vacant locations…';
    draw();
    try {
      const result = await request(`/planning/areas/${encodeURIComponent(area.id)}/recommendations`, { signal: controller.signal });
      if (controller.signal.aborted) return;
      if (result.data_version !== collection.data_version) throw new Error('The data changed. Reload to see the latest suggestions.');
      rankings = result.rankings;
      if (!userChoseCategory) category = orderedNeeds()[0].category_id;
      ranking = rankings[category];
      renderNeeds();
      renderSites();
    } catch (cause) {
      if (!controller.signal.aborted) { status.textContent = cause.message; retry.hidden = false; }
    }
  }

  function chooseArea(id) {
    stopPicking();
    customSite = null;
    onClear?.();
    area = collection.features.find((feature) => feature.id === id);
    if (!area) return;
    ranking = rankings = null;
    selectedSite = null;
    category = null;
    userChoseCategory = false;
    find('[data-area-population]').textContent = `${number.format(area.properties.population)} residents · ${area.properties.candidate_count} lots marked vacant in the data.`;
    const coverage = area.properties.population ? 100 * area.properties.supported_population / area.properties.population : 0;
    find('[data-area-source]').textContent = `Boundary data: ${collection.sources.tract_vintage}. Population: ${collection.sources.population_vintage}. Access coverage: ${number.format(coverage)}%. Needs use the existing 800 m access model.`;
    renderNeeds();
    map?.focusArea?.(area);
    loadSites();
  }

  async function loadAreas() {
    areaRequest?.abort();
    const controller = new AbortController();
    areaRequest = controller;
    retry.hidden = true;
    status.textContent = 'Loading areas…';
    try {
      collection = await request('/planning/areas', { signal: controller.signal });
      if (controller.signal.aborted) return;
      select.replaceChildren(...[...collection.features].sort((a, b) => a.properties.name.localeCompare(b.properties.name, 'en', { numeric: true }))
        .map((feature) => new Option(feature.properties.name, feature.id)));
      select.disabled = !collection.features.length;
      const center = map?.center() || { lon: -122.399, lat: 37.7905 };
      const candidates = collection.features.filter((feature) => feature.properties.candidate_count > 0 && feature.properties.supported_population > 0);
      const nearest = [...(candidates.length ? candidates : collection.features)].sort((a, b) => {
        const distance = (row) => (row.properties.representative_point[0] - center.lon) ** 2 + (row.properties.representative_point[1] - center.lat) ** 2;
        return distance(a) - distance(b);
      })[0];
      if (nearest) { select.value = nearest.id; chooseArea(nearest.id); }
      else status.textContent = 'No area boundaries are available.';
    } catch (cause) {
      if (!controller.signal.aborted) {
        select.replaceChildren(new Option('Area data unavailable', ''));
        status.textContent = cause.message;
        retry.hidden = false;
      }
    }
  }

  select.addEventListener('change', () => chooseArea(select.value));
  sites.addEventListener('change', async () => {
    if (sites.value === PICK_OWN) {
      picking = true;
      const message = find('[data-pick-message]');
      message.textContent = 'Finding vacant lots that fit this building type…';
      find('[data-testid="site-pick-notice"]').hidden = false;
      map?.setPickingSite?.(true);
      availableRequest?.abort();
      const controller = new AbortController();
      availableRequest = controller;
      try {
        const result = await request(`/planning/areas/${encodeURIComponent(area.id)}/available-sites?category=${category}`, { signal: controller.signal });
        if (controller.signal.aborted || !picking) return;
        if (result.data_version !== collection.data_version) throw new Error('The data changed. Reload before choosing a lot.');
        availableSites = result.features;
        map?.drawAvailableSites?.(availableSites, category);
        message.textContent = availableSites.length
          ? `${availableSites.length} compatible vacant ${availableSites.length === 1 ? 'lot is' : 'lots are'} blinking yellow. Click one inside the blue boundary.`
          : 'No vacant lots in this tract fit this building type. Try another type or tract.';
      } catch (error) {
        if (!controller.signal.aborted) message.textContent = error.message;
      }
      return;
    }
    stopPicking();
    if (sites.value !== customSite?.parcel_id) {
      customSite = null;
      sites.querySelector('[data-custom-site]')?.remove();
    }
    if (sites.value) previewSite(sites.value);
  });
  find('[data-testid="cancel-site-pick"]').addEventListener('click', () => {
    stopPicking();
    sites.value = selectedSite || '';
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && picking) {
      stopPicking();
      sites.value = selectedSite || '';
    }
  });

  async function pickSite(point) {
    if (!picking || !area) return false;
    const message = find('[data-pick-message]');
    if (!containsPoint(area.geometry, point)) {
      message.textContent = 'That location is outside this tract. Choose a vacant lot inside the blue boundary.';
      return true;
    }
    pickRequest?.abort();
    const controller = new AbortController();
    pickRequest = controller;
    const { signal } = controller;
    message.textContent = 'Checking this vacant lot…';
    try {
      const hit = availableSites.find((feature) => containsPoint(feature.geometry, point));
      if (!hit) throw new Error('Choose one of the highlighted vacant lots. Other locations do not fit this building type.');
      const result = await request(`/planning/areas/${encodeURIComponent(area.id)}/sites?category=${category}&parcel_id=${encodeURIComponent(hit.id)}&limit=1`, { signal });
      if (signal.aborted) return true;
      if (result.data_version !== collection.data_version) throw new Error('The data changed. Reload the planner before selecting this lot.');
      const site = result.sites.find((row) => row.parcel_id === hit.id);
      if (!site) throw new Error('This vacant lot is unavailable for this building type.');
      customSite = site;
      const previous = sites.querySelector('[data-custom-site]');
      previous?.remove();
      if (![...sites.options].some((option) => option.value === hit.id)) {
        const option = new Option('Your Selected Lot', hit.id);
        option.dataset.customSite = '';
        sites.insertBefore(option, sites.lastElementChild);
      }
      sites.value = hit.id;
      stopPicking();
      find('[data-site-reason]').hidden = false;
      status.textContent = 'Your vacant lot is selected. The score uses the same tract needs and physical-fit checks.';
      await previewSite(hit.id, false);
    } catch (error) {
      if (!signal.aborted) message.textContent = error.message;
    }
    return true;
  }
  find('[data-testid="only-vacant-view"]').addEventListener('change', (event) => {
    onlyVacant = event.target.checked;
    map?.setOnlyVacant?.(onlyVacant);
  });
  retry.addEventListener('click', () => collection ? loadSites() : loadAreas());
  loadAreas();
  return {
    mountSizeControl(control) { find('[data-size-mount]').append(control); },
    mountDetails(content) { find('[data-detail-mount]').append(content); },
    isPicking: () => picking,
    pickSite,
    tractAt(point) { return collection?.features.find((feature) => containsPoint(feature.geometry, point)); },
    jumpToTract(point) {
      const tract = collection?.features.find((feature) => containsPoint(feature.geometry, point));
      if (!tract) return false;
      select.value = tract.id;
      chooseArea(tract.id);
      return true;
    },
    connectMap(connectedMap) { map = connectedMap; map.setOnlyVacant?.(onlyVacant); if (area) map.focusArea?.(area); draw(); }, redraw: draw,
  };
}
