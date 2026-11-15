// User edits to the city: delete a building, or replace it with another type and height.
//
// Originals stay in the merged chunk meshes; an edited building is hidden in the vertex
// shader through a one-texel-per-building texture (shared.uEdited). A replacement is a new
// mesh on the same footprint built with the same emitters and materials as the city, so it
// gets the same windows, shading, shadows, clear-radius LOD switch and vacancy colouring.
// Edits are a local sketch for exploring options: they live in this browser only.
import * as THREE from 'three';
import { BuildingChunk, emitState, emitWalls, emitCap, ringCentroid, packChunk } from './buildingGeom.js';
import { makeBuildingMesh } from './world.js';
import { readRings } from './picking.js';

export const MIN_HEIGHT_M = 3;
export const MAX_HEIGHT_M = 350;

export function createEdits({ scene, shared, index, mats, chunkSize, vacancy, storageKey }) {
  const edits = new Map(); // building index -> { deleted: true } | { type, height }
  const meshes = new Map(); // building index -> [full mesh, lod mesh]
  const group = new THREE.Group();
  group.name = 'user-edits';
  scene.add(group);
  const tex = shared.uEdited.value;
  const listeners = new Set();

  // Picking reads these: deleted buildings are skipped, replaced ones use the new shape.
  index.deleted = new Set();
  index.override = new Map();

  function removeMeshes(b) {
    for (const m of meshes.get(b) || []) {
      group.remove(m);
      m.geometry.dispose();
    }
    meshes.delete(b);
  }

  function buildReplacement(b, type, height) {
    const rings = readRings(index, b);
    const c = ringCentroid(rings[0]);
    const elev = index.elev[b];
    const bottom = Math.min(index.bottom[b], 0);
    const seed = (b * 2654435761) >>> 24; // stable per building
    emitState.vac = vacancy && b < vacancy.length ? vacancy[b] : 0;
    emitState.bid = -1; // never hidden by the edit texture
    const out = [];
    for (const isLod of [false, true]) {
      const ch = new BuildingChunk(0, 0, c[0], c[1], index.Q);
      for (const r of rings) emitWalls(ch, r, r, bottom, height, elev, height, type, seed);
      emitCap(ch, rings, height, elev, height, type, seed);
      const m = makeBuildingMesh(packChunk(ch), mats, isLod, 2 * height + 200);
      m.userData.editedBuilding = b;
      group.add(m);
      out.push(m);
    }
    meshes.set(b, out);
  }

  function setHidden(b, on) {
    tex.image.data[b] = on ? 255 : 0;
    tex.needsUpdate = true;
  }

  function apply(b, edit) {
    removeMeshes(b);
    index.deleted.delete(b);
    index.override.delete(b);
    if (!edit) {
      edits.delete(b);
      setHidden(b, false);
      return;
    }
    edits.set(b, edit);
    setHidden(b, true);
    if (edit.deleted) {
      index.deleted.add(b);
    } else {
      index.override.set(b, { type: edit.type, height: edit.height });
      buildReplacement(b, edit.type, edit.height);
    }
  }

  function save() {
    try {
      localStorage.setItem(storageKey, JSON.stringify([...edits.entries()]));
    } catch { /* private mode or storage blocked: edits just won't persist */ }
    for (const fn of listeners) fn(edits.size);
  }

  // restore saved edits
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || '[]');
    for (const [b, e] of saved) {
      if (Number.isInteger(b) && b >= 0 && b < index.count && e && (e.deleted ||
          (Number.isInteger(e.type) && Number.isFinite(e.height)))) {
        apply(b, e.deleted ? { deleted: true } : { type: e.type, height: clampHeight(e.height) });
      }
    }
  } catch { /* ignore unreadable storage */ }

  return {
    group,
    count: () => edits.size,
    get: (b) => edits.get(b) || null,
    onChange(fn) { listeners.add(fn); fn(edits.size); },
    remove(b) { apply(b, { deleted: true }); save(); },
    replace(b, type, height) { apply(b, { type, height: clampHeight(height) }); save(); },
    restore(b) { apply(b, null); save(); },
    resetAll() {
      for (const b of [...edits.keys()]) apply(b, null);
      save();
    },  };
}

export function clampHeight(h) {
  return Math.min(MAX_HEIGHT_M, Math.max(MIN_HEIGHT_M, Math.round(Number(h) || MIN_HEIGHT_M)));
}
