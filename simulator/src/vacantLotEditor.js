import * as THREE from 'three';
import { containsPoint } from './parcelGeometry.js';
import { fitsLot, heightRange, ruleFor, validateBuilding } from './buildingRules.js';
import { mountLotEffects } from './lotEffects.js';

const API = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '');

// Standalone map sketches use the same verified vacant parcels and fitted footprints as the planner.
export function createVacantLotEditor({ scene, projection, groundAt, parcels, hud, planner, choices }) {
  const sketches = new THREE.Group();
  sketches.name = 'vacant-lot-sketches';
  scene.add(sketches);
  const outline = new THREE.Group();
  outline.name = 'selected-vacant-lot';
  scene.add(outline);
  const entries = new Map();
  let selected = null, pending = null;

  async function request(path, signal) {
    const response = await fetch(`${API}${path}`, { signal });
    const data = await response.json();
    if (!response.ok) throw new Error(data?.error?.message || 'Could not load this vacant lot. Please try again.');
    return data;
  }

  function clear() {
    pending?.abort();
    pending = null;
    selected = null;
    parcels.draw(null, null, outline);
    hud.hideBuilding();
  }

  function drawEntry(entry) {
    const design = entry.plan.designs[entry.category];
    parcels.draw(null, { ...design, height_m: entry.height }, entry.group);
  }

  function show(feature, plan) {
    selected = { feature, plan };
    const point = feature.properties.representative_point.coordinates;
    const location = { lon: point[0], lat: point[1] };
    const tract = planner.tractAt(location);
    const ctx = { zoning: feature.properties.zoning };
    const available = choices.filter(choice => plan?.designs[choice.id]?.feasible && fitsLot(choice, ctx));
    const entry = entries.get(feature.id);
    const category = entry?.category || available[0]?.id;
    parcels.draw(feature, null, outline);
    hud.showBuilding({
      title: entry ? ruleFor(category).label : 'Vacant land', color: '#e22b32', lot: 2,
      choices: available, choiceId: category, heightM: entry?.height || plan?.designs[category]?.height_m || 3,
      footprintM2: feature.properties.area_m2, edited: Boolean(entry), hasRules: false,
      editable: available.length > 0,
      lockedReason: plan ? 'None of the five service buildings fits the available land and recorded zoning here.'
        : 'Building fit data is unavailable for this lot. You can still explore its tract.',
      tractName: tract?.properties.name,
    }, {
      onClose: clear,
      onJumpToTract: tract ? () => { clear(); hud.openPlanner(); planner.jumpToTract(location); } : null,
      range: id => heightRange(ruleFor(id), ctx),
      defaultHeight: id => plan.designs[id].height_m,
      validate: (id, height) => validateBuilding(ruleFor(id), height, ctx),
      onDetails: (id, card) => mountLotEffects(card, { feature, point: location, category: id, tract, design: plan?.designs[id] }),
      onApply(id, height) {
        if (!available.some(choice => choice.id === id) || !validateBuilding(ruleFor(id), height, ctx).ok) return;
        const current = entries.get(feature.id) || { group: new THREE.Group() };
        Object.assign(current, { feature, plan, category: id, height });
        current.group.userData.parcelId = feature.id;
        sketches.add(current.group);
        entries.set(feature.id, current);
        drawEntry(current);
        show(feature, plan);
      },
      onDelete: entry ? () => {
        parcels.draw(null, null, entry.group);
        sketches.remove(entry.group);
        entries.delete(feature.id);
        show(feature, plan);
      } : null,
    });
  }

  return {
    clear,
    get active() { return Boolean(selected || pending); },
    hitPoint(raycaster) {
      const hit = raycaster.intersectObjects(sketches.children, true).find(hit => hit.object.isMesh);
      if (!hit) return null;
      const entry = entries.get(hit.object.parent.userData.parcelId);
      const [lon, lat] = entry.feature.properties.representative_point.coordinates;
      return { lon, lat };
    },
    async pick(point) {
      clear();
      const controller = new AbortController();
      pending = controller;
      const { signal } = controller;
      try {
        const saved = [...entries.values()].find(entry => containsPoint(entry.feature.geometry, point));
        if (saved) { show(saved.feature, saved.plan); return; }
        const d = .00004;
        const data = await request(`/parcels?bbox=${[point.lon-d, point.lat-d, point.lon+d, point.lat+d].join(',')}`, signal);
        const feature = data.features.find(row => row.properties.vacancy_status === 'source_indicated_vacant' && containsPoint(row.geometry, point));
        if (!feature || signal.aborted) return;
        let plan = null;
        try { plan = await request(`/planning/sites/${encodeURIComponent(feature.id)}`, signal); }
        catch (error) { if (signal.aborted) return; }
        if (!signal.aborted) show(feature, plan);
      } catch (error) {
        if (!signal.aborted) hud.showBuilding({ title: 'Lot unavailable', color: '#e22b32', lot: 0,
          choices: [], editable: false, lockedReason: error.message }, { onClose: clear });
      } finally {
        if (pending === controller) pending = null;
      }
    },
    redraw() {
      for (const entry of entries.values()) drawEntry(entry);
      if (selected) parcels.draw(selected.feature, null, outline);
    },
    update(camera) {
      if (!selected) return;
      const [lon, lat] = selected.feature.properties.representative_point.coordinates;
      const { x, z } = projection.toWorld(lon, lat);
      const height = entries.get(selected.feature.id)?.height || 0;
      const p = new THREE.Vector3(x, groundAt(x, z) + height, z).project(camera);
      hud.placeBuilding((p.x + 1) / 2 * innerWidth, (1-p.y) / 2 * innerHeight,
        p.z > -1 && p.z < 1 && Math.abs(p.x) < 1.2 && Math.abs(p.y) < 1.2);
    },
  };
}
