// Click-to-inspect for buildings: CPU ray picking against the building records and a
// yellow glow highlight for the selected building.
//
// Building meshes get their elevation in the vertex shader (elevOf), so three's mesh
// raycaster would test the wrong heights. Instead we walk the click ray across a 2D grid
// of building bounding boxes and intersect each candidate as an extruded footprint.
import * as THREE from 'three';
import earcut from 'earcut';
import { elevOfJS } from './materials.js';

const CELL = 64; // metres per pick-grid cell

// ---------------------------------------------------------------------------
// Index
// ---------------------------------------------------------------------------
/** Parse buildings.bin (SFB2) into flat arrays plus a uniform grid over footprint bboxes. */
export function createBuildingIndex(buf, extent) {
  const dv = new DataView(buf);
  const count = dv.getUint32(4, true);
  const Q = dv.getFloat32(8, true);
  const recOff = new Uint32Array(count);
  const type = new Uint8Array(count);
  const kind = new Uint8Array(count);
  const extra = new Uint8Array(count);
  const h = new Float32Array(count);
  const elev = new Float32Array(count);
  const bottom = new Float32Array(count);
  const roof = new Float32Array(count);
  const bbox = new Float32Array(count * 4); // minX, maxX, minZ, maxZ
  let off = 12;
  for (let b = 0; b < count; b++) {
    recOff[b] = off;
    type[b] = dv.getUint8(off);
    const nr = dv.getUint8(off + 1);
    h[b] = dv.getUint16(off + 2, true) / 10;
    elev[b] = dv.getInt16(off + 4, true) / 10;
    bottom[b] = dv.getInt16(off + 6, true) / 10;
    roof[b] = dv.getUint16(off + 8, true) / 10;
    kind[b] = dv.getUint8(off + 10);
    extra[b] = dv.getUint8(off + 11);
    off += 12;
    let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
    for (let r = 0; r < nr; r++) {
      const n = dv.getUint16(off, true);
      off += 2;
      for (let i = 0; i < n; i++) {
        const x = dv.getInt16(off, true) * Q, z = dv.getInt16(off + 2, true) * Q;
        off += 4;
        if (r === 0) {
          if (x < x0) x0 = x; if (x > x1) x1 = x;
          if (z < z0) z0 = z; if (z > z1) z1 = z;
        }
      }
    }
    bbox.set([x0, x1, z0, z1], b * 4);
  }

  // Grid (CSR layout): cellStart[c]..cellStart[c+1] index into cellItems.
  const G = Math.ceil((2 * extent) / CELL);
  const cellOf = (v) => Math.min(G - 1, Math.max(0, Math.floor((v + extent) / CELL)));
  const counts = new Uint32Array(G * G + 1);
  const forCells = (b, fn) => {
    const cx0 = cellOf(bbox[4 * b]), cx1 = cellOf(bbox[4 * b + 1]);
    const cz0 = cellOf(bbox[4 * b + 2]), cz1 = cellOf(bbox[4 * b + 3]);
    for (let cz = cz0; cz <= cz1; cz++) for (let cx = cx0; cx <= cx1; cx++) fn(cz * G + cx);
  };
  for (let b = 0; b < count; b++) forCells(b, (c) => counts[c + 1]++);
  for (let c = 0; c < G * G; c++) counts[c + 1] += counts[c];
  const cellStart = counts.slice();
  const fill = counts.slice(0, G * G);
  const cellItems = new Uint32Array(cellStart[G * G]);
  for (let b = 0; b < count; b++) forCells(b, (c) => { cellItems[fill[c]++] = b; });

  let maxTop = 0;
  for (let b = 0; b < count; b++) maxTop = Math.max(maxTop, elev[b] + h[b] + roof[b]);

  return { dv, Q, count, extent, G, recOff, type, kind, extra, h, elev, bottom, roof, bbox, cellStart, cellItems, maxTop, stamp: new Uint32Array(count), pass: 0 };
}

export function readRings(index, b) {
  const { dv, Q, recOff } = index;
  let off = recOff[b];
  const nr = dv.getUint8(off + 1);
  off += 12;
  const rings = [];
  for (let r = 0; r < nr; r++) {
    const n = dv.getUint16(off, true);
    off += 2;
    const p = new Float64Array(n * 2);
    for (let i = 0; i < n; i++) {
      p[2 * i] = dv.getInt16(off, true) * Q;
      p[2 * i + 1] = dv.getInt16(off + 2, true) * Q;
      off += 4;
    }
    rings.push(p);
  }
  return rings;
}

function ringCentroid(p) {
  const n = p.length / 2;
  let a = 0, cx = 0, cz = 0;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const f = p[2 * j] * p[2 * i + 1] - p[2 * i] * p[2 * j + 1];
    a += f;
    cx += (p[2 * j] + p[2 * i]) * f;
    cz += (p[2 * j + 1] + p[2 * i + 1]) * f;
  }
  if (Math.abs(a) < 1e-6) {
    let sx = 0, sz = 0;
    for (let i = 0; i < n; i++) { sx += p[2 * i]; sz += p[2 * i + 1]; }
    return [sx / n, sz / n];
  }
  return [cx / (3 * a), cz / (3 * a)];
}

const scaleRing = (p, c, s) => p.map((v, i) => c[i & 1] + (v - c[i & 1]) * s);

/**
 * Solid parts of a building as prisms {rings, y0, y1} in world metres, mirroring the
 * worker's geometry closely enough for picking and highlighting.
 */
export function buildingParts(index, b, shared) {
  const rings = readRings(index, b);
  const base = elevOfJS(shared, index.elev[b]);
  const edit = index.override && index.override.get(b);
  if (edit) return [{ rings, y0: base + Math.min(index.bottom[b], 0), y1: base + edit.height }]; // user replacement: flat roof
  const h = index.h[b], roof = index.roof[b], k = index.kind[b];
  const y0 = base + Math.min(index.bottom[b], 0);
  if (k === 3) {
    const up = scaleRing(rings[0], ringCentroid(rings[0]), index.extra[b] / 255);
    return [{ rings, y0, y1: base + h }, { rings: [up], y0: base + h - 0.3, y1: base + h + roof }];
  }
  const top = k === 1 ? h + roof * 0.5 : h; // hipped roof: mid-roof height is close enough
  return [{ rings, y0, y1: base + top }];
}

// ---------------------------------------------------------------------------
// Ray picking
// ---------------------------------------------------------------------------
function insideRings(rings, x, z) {
  let inside = false;
  for (const p of rings) {
    const n = p.length / 2;
    for (let i = 0, j = n - 1; i < n; j = i++) {
      const xi = p[2 * i], zi = p[2 * i + 1], xj = p[2 * j], zj = p[2 * j + 1];
      if ((zi > z) !== (zj > z) && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) inside = !inside;
    }
  }
  return inside;
}

function hitPrism(part, o, d) {
  let best = Infinity;
  // top cap
  if (Math.abs(d.y) > 1e-9) {
    const t = (part.y1 - o.y) / d.y;
    if (t > 0 && insideRings(part.rings, o.x + d.x * t, o.z + d.z * t)) best = t;
  }
  // walls
  for (const p of part.rings) {
    const n = p.length / 2;
    for (let i = 0, j = n - 1; i < n; j = i++) {
      const ax = p[2 * j], az = p[2 * j + 1];
      const ex = p[2 * i] - ax, ez = p[2 * i + 1] - az;
      const den = d.x * ez - d.z * ex;
      if (Math.abs(den) < 1e-12) continue;
      const wx = ax - o.x, wz = az - o.z;
      const t = (wx * ez - wz * ex) / den;
      const s = (wx * d.z - wz * d.x) / den;
      if (t <= 0 || t >= best || s < 0 || s > 1) continue;
      const y = o.y + d.y * t;
      if (y >= part.y0 && y <= part.y1) best = t;
    }
  }
  return best;
}

/**
 * Nearest visible building hit by the ray, or -1. Stops at the terrain surface so clicks
 * on open ground behind a hill do not select buildings beyond it.
 */
export function pickBuilding(index, ray, shared, groundAt) {
  const o = ray.origin, d = ray.direction;
  const E = index.extent, G = index.G;
  const hidden = shared.uHidden.value;
  index.pass = (index.pass + 1) >>> 0 || 1;
  const pass = index.pass;

  // clip the ray to the world square in xz
  let tMin = 0, tMax = Infinity;
  for (const [oc, dc] of [[o.x, d.x], [o.z, d.z]]) {
    if (Math.abs(dc) < 1e-12) {
      if (oc < -E || oc > E) return -1;
    } else {
      let t0 = (-E - oc) / dc, t1 = (E - oc) / dc;
      if (t0 > t1) [t0, t1] = [t1, t0];
      tMin = Math.max(tMin, t0);
      tMax = Math.min(tMax, t1);
    }
  }
  // skip the stretch where the ray is above every roof
  const ceiling = index.maxTop + 50;
  if (d.y < 0 && o.y > ceiling) tMin = Math.max(tMin, (ceiling - o.y) / d.y);
  if (tMin >= tMax) return -1;

  const px = o.x + d.x * tMin, pz = o.z + d.z * tMin;
  let cx = Math.min(G - 1, Math.max(0, Math.floor((px + E) / CELL)));
  let cz = Math.min(G - 1, Math.max(0, Math.floor((pz + E) / CELL)));
  const stepX = d.x > 0 ? 1 : -1, stepZ = d.z > 0 ? 1 : -1;
  const nextBoundary = (c, step) => -E + (c + (step > 0 ? 1 : 0)) * CELL;
  let tNextX = Math.abs(d.x) < 1e-12 ? Infinity : (nextBoundary(cx, stepX) - o.x) / d.x;
  let tNextZ = Math.abs(d.z) < 1e-12 ? Infinity : (nextBoundary(cz, stepZ) - o.z) / d.z;
  const dtX = Math.abs(d.x) < 1e-12 ? Infinity : CELL / Math.abs(d.x);
  const dtZ = Math.abs(d.z) < 1e-12 ? Infinity : CELL / Math.abs(d.z);

  let best = Infinity, bestB = -1, tEnter = tMin;
  for (let iter = 0; iter < 4 * G && cx >= 0 && cz >= 0 && cx < G && cz < G; iter++) {
    const tExit = Math.min(tNextX, tNextZ, tMax);
    const c = cz * G + cx;
    for (let k = index.cellStart[c]; k < index.cellStart[c + 1]; k++) {
      const b = index.cellItems[k];
      if (index.stamp[b] === pass) continue;
      index.stamp[b] = pass;
      if (index.deleted && index.deleted.has(b)) continue;
      const type = index.override && index.override.has(b) ? index.override.get(b).type : index.type[b];
      if (hidden[type] > 0.5 || (index.skip && index.skip(b))) continue;
      for (const part of buildingParts(index, b, shared)) {
        const t = hitPrism(part, o, d);
        if (t < best) { best = t; bestB = b; }
      }
    }
    if (best <= tExit) return bestB;
    // terrain occlusion: sample the ground across this cell
    for (let s = 1; s <= 4; s++) {
      const t = tEnter + ((tExit - tEnter) * s) / 4;
      const x = o.x + d.x * t, z = o.z + d.z * t;
      if (o.y + d.y * t < groundAt(x, z)) return best < t ? bestB : -1;
    }
    if (tExit >= tMax) break;
    tEnter = tExit;
    if (tNextX < tNextZ) { cx += stepX; tNextX += dtX; } else { cz += stepZ; tNextZ += dtZ; }
  }
  return bestB;
}

// ---------------------------------------------------------------------------
// Highlight: yellow tint on the visible surfaces plus a screen-space outline and glow.
// SF row buildings share walls, so a geometric rim would hide behind the neighbours;
// the outline is drawn from a silhouette mask instead, on top of the finished frame.
// ---------------------------------------------------------------------------
function prismGeometry(parts) {
  const pos = [], nrm = [], idx = [];
  for (const part of parts) {
    for (const ring of part.rings) {
      const n = ring.length / 2;
      const base = pos.length / 3;
      for (let i = 0; i < n; i++) {
        // smooth outward (miter) normal in xz so the expanded shell has no gaps at corners
        const a = (i + n - 1) % n, c = (i + 1) % n;
        const e1x = ring[2 * i] - ring[2 * a], e1z = ring[2 * i + 1] - ring[2 * a + 1];
        const e2x = ring[2 * c] - ring[2 * i], e2z = ring[2 * c + 1] - ring[2 * i + 1];
        const l1 = Math.hypot(e1x, e1z) || 1, l2 = Math.hypot(e2x, e2z) || 1;
        let mx = e1z / l1 + e2z / l2, mz = -e1x / l1 - e2x / l2;
        const ml = Math.hypot(mx, mz) || 1;
        mx /= ml; mz /= ml;
        pos.push(ring[2 * i], part.y0, ring[2 * i + 1], ring[2 * i], part.y1, ring[2 * i + 1]);
        nrm.push(mx, 0, mz, mx * 0.7, 0.7, mz * 0.7);
      }
      // ring winding varies; orient wall triangles so the miter normal points outward
      let area = 0;
      for (let i = 0, j = n - 1; i < n; j = i++) area += ring[2 * j] * ring[2 * i + 1] - ring[2 * i] * ring[2 * j + 1];
      for (let i = 0; i < n; i++) {
        const j = (i + 1) % n;
        const b0 = base + 2 * i, t0 = b0 + 1, b1 = base + 2 * j, t1 = b1 + 1;
        if (area > 0) idx.push(b0, t1, b1, b0, t0, t1);
        else idx.push(b0, b1, t1, b0, t1, t0);
      }
    }
    // top cap (outer ring + holes)
    const flat = [], holes = [], top = [];
    part.rings.forEach((ring, r) => {
      if (r > 0) holes.push(flat.length / 2);
      for (let i = 0; i < ring.length / 2; i++) {
        flat.push(ring[2 * i], ring[2 * i + 1]);
        top.push(pos.length / 3);
        pos.push(ring[2 * i], part.y1, ring[2 * i + 1]);
        nrm.push(0, 1, 0);
      }
    });
    const tri = earcut(flat, holes.length ? holes : undefined, 2);
    for (let t = 0; t < tri.length; t += 3) {
      const [a, b, c] = [tri[t], tri[t + 1], tri[t + 2]];
      const s = (flat[2 * b] - flat[2 * a]) * (flat[2 * c + 1] - flat[2 * a + 1]) - (flat[2 * c] - flat[2 * a]) * (flat[2 * b + 1] - flat[2 * a + 1]);
      if (s > 0) idx.push(top[a], top[c], top[b]);
      else idx.push(top[a], top[b], top[c]);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('normal', new THREE.Float32BufferAttribute(nrm, 3));
  g.setIndex(idx);
  g.computeBoundingSphere();
  g.boundingSphere.radius += 60; // the shell grows in the vertex shader
  return g;
}

const OUTLINE_FRAG = /* glsl */ `
uniform sampler2D tMask;
uniform vec2 uTexel;
uniform float uScale;
uniform float uPulse;
uniform vec3 uColor;
varying vec2 vUv;
float maskAt(vec2 o) { return texture2D(tMask, vUv + o * uTexel).r; }
void main() {
  float m = maskAt(vec2(0.0));
  float edge = 0.0, glow = 0.0;
  for (int i = 0; i < 16; i++) {
    float a = float(i) * 0.3926991;
    vec2 d = vec2(cos(a), sin(a));
    edge = max(edge, maskAt(d * 2.5 * uScale));
    glow += maskAt(d * 5.0 * uScale) * 0.6 + maskAt(d * 9.0 * uScale) * 0.4;
  }
  edge = max(edge - m, 0.0);
  glow = (glow / 16.0) * (1.0 - m);
  float a = clamp(edge * 1.0 + glow * 0.8, 0.0, 1.0) * uPulse;
  gl_FragColor = vec4(uColor * a, a);
}`;

export function createSelectionHighlight(scene, renderer) {
  const group = new THREE.Group();
  group.name = 'selection';
  scene.add(group);
  const tint = new THREE.MeshBasicMaterial({
    color: '#ffd84a', transparent: true, opacity: 0.38, depthWrite: false,
    polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -4,
  });

  // silhouette mask (no depth test: the outline wraps the whole building, even where
  // neighbours stand in front of it)
  const maskScene = new THREE.Scene();
  const maskMat = new THREE.MeshBasicMaterial({ color: 0xffffff, depthTest: false, depthWrite: false });
  const size = renderer.getDrawingBufferSize(new THREE.Vector2());
  const maskRT = new THREE.WebGLRenderTarget(size.x, size.y, { depthBuffer: false });
  const post = new THREE.ShaderMaterial({
    uniforms: {
      tMask: { value: maskRT.texture },
      uTexel: { value: new THREE.Vector2(1 / size.x, 1 / size.y) },
      uScale: { value: renderer.getPixelRatio() },
      uPulse: { value: 1 },
      uColor: { value: new THREE.Color('#ffd23f') },
    },
    vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }',
    fragmentShader: OUTLINE_FRAG,
    transparent: true, depthTest: false, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  const quadScene = new THREE.Scene();
  const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), post);
  quad.frustumCulled = false;
  quadScene.add(quad);
  const quadCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

  let geo = null;
  const center = new THREE.Vector3();

  return {
    group,
    center,
    show(parts) {
      this.clear();
      geo = prismGeometry(parts);
      const body = new THREE.Mesh(geo, tint);
      body.renderOrder = 10;
      group.add(body);
      maskScene.add(new THREE.Mesh(geo, maskMat));
      geo.computeBoundingBox();
      geo.boundingBox.getCenter(center);
      center.y = geo.boundingBox.max.y;
    },
    clear() {
      group.clear();
      maskScene.clear();
      if (geo) geo.dispose();
      geo = null;
    },
    resize() {
      renderer.getDrawingBufferSize(size);
      maskRT.setSize(size.x, size.y);
      post.uniforms.uTexel.value.set(1 / size.x, 1 / size.y);
      post.uniforms.uScale.value = renderer.getPixelRatio();
    },
    /** Gentle pulse on the tint and glow. */
    update(time) {
      const pulse = 0.85 + 0.15 * Math.sin(time * 3.0);
      tint.opacity = 0.3 + 0.1 * pulse;
      post.uniforms.uPulse.value = pulse;
    },
    /** Draw the outline over the finished frame (call after the main render). */
    render(camera) {
      if (!geo) return;
      renderer.getDrawingBufferSize(size);
      if (maskRT.width !== size.x || maskRT.height !== size.y) this.resize();
      const prevAuto = renderer.autoClear;
      renderer.setRenderTarget(maskRT);
      renderer.setClearColor(0x000000, 0);
      renderer.clear(true, false, false);
      renderer.render(maskScene, camera);
      renderer.setRenderTarget(null);
      renderer.autoClear = false;
      renderer.render(quadScene, quadCam);
      renderer.autoClear = prevAuto;
    },
  };
}
