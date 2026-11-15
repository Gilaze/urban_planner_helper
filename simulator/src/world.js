import * as THREE from 'three';
import earcut from 'earcut';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { elevOfJS } from './materials.js';

const lin = (hex) => new THREE.Color(hex); // THREE.Color converts sRGB hex to linear

function sphereFor(minX, maxX, minY, maxY, minZ, maxZ) {
  const c = new THREE.Vector3((minX + maxX) / 2, (minY + maxY) / 2, (minZ + maxZ) / 2);
  const r = 0.5 * Math.hypot(maxX - minX, maxY - minY, maxZ - minZ);
  return new THREE.Sphere(c, r + 1);
}

// ---------------------------------------------------------------------------
export function buildTerrain(terrain, mats) {
  const { n, extent } = terrain;
  const geo = new THREE.PlaneGeometry(2 * extent, 2 * extent, n - 1, n - 1);
  geo.rotateX(-Math.PI / 2);
  geo.deleteAttribute('uv');
  geo.deleteAttribute('normal');
  geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 150, 0), extent * 1.5);
  const mesh = new THREE.Mesh(geo, mats.mat);
  mesh.customDepthMaterial = mats.depth;
  mesh.receiveShadow = true;
  mesh.castShadow = true;
  mesh.name = 'terrain';
  return mesh;
}

export function buildWater(mat) {
  const geo = new THREE.PlaneGeometry(90000, 90000, 1, 1);
  geo.rotateX(-Math.PI / 2);
  const mesh = new THREE.Mesh(geo, mat);
  mesh.receiveShadow = true;
  mesh.name = 'bay';
  return mesh;
}

// ---------------------------------------------------------------------------
/** One building chunk as a mesh (shared by the city chunks and user-added buildings). */
export function makeBuildingMesh(c, mats, isLod, chunkSize) {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(c.pos, 3));
  g.setAttribute('aUE', new THREE.BufferAttribute(c.ue, 4));
  g.setAttribute('aMeta', new THREE.BufferAttribute(c.meta, 2));
  g.setAttribute('aCtr', new THREE.BufferAttribute(c.ctr, 2));
  g.setAttribute('aBid', new THREE.BufferAttribute(c.bid, 1));
  g.setIndex(new THREE.BufferAttribute(c.idx, 1));
  const lo = Math.min(0, c.minY), hi = Math.max(c.maxY, 1);
  // buildings belong to the chunk of their centroid, so allow generous overhang
  const reach = chunkSize * 0.5 + 450;
  g.boundingSphere = new THREE.Sphere(
    new THREE.Vector3(0, (lo + hi) / 2 / c.Q, 0),
    Math.hypot(reach, reach, (hi - lo) / 2) / c.Q,
  );
  const m = new THREE.Mesh(g, isLod ? mats.lod : mats.mat);
  if (!isLod) m.customDepthMaterial = mats.depth;
  m.position.set(c.ox, 0, c.oz);
  m.scale.setScalar(c.Q);
  m.castShadow = !isLod;
  m.receiveShadow = !isLod;
  m.updateMatrix();
  m.matrixAutoUpdate = false;
  return m;
}

export function buildBuildingMeshes({ full, lod }, mats, chunkSize) {
  const group = new THREE.Group();
  group.name = 'buildings';
  const lodGroup = new THREE.Group();
  lodGroup.name = 'buildings-lod';
  const chunks = [];      // { x, z, full, lod } for per-frame radius culling
  const byKey = new Map();
  let tris = 0, verts = 0, lodTris = 0;
  const make = (c, isLod) => {
    const m = makeBuildingMesh(c, mats, isLod, chunkSize);
    const key = `${c.cx},${c.cz}`;
    if (!byKey.has(key)) {
      const rec = { x: c.ox, z: c.oz, full: null, lod: null };
      byKey.set(key, rec);
      chunks.push(rec);
    }
    byKey.get(key)[isLod ? 'lod' : 'full'] = m;
    return m;
  };
  for (const c of full) {
    group.add(make(c, false));
    tris += c.idx.length / 3;
    verts += c.pos.length / 3;
  }
  for (const c of lod) {
    lodGroup.add(make(c, true));
    lodTris += c.idx.length / 3;
  }
  return { group, lodGroup, chunks, tris, verts, lodTris };
}

export function buildRoadMeshes(chunks, mats) {
  const group = new THREE.Group();
  group.name = 'roads';
  let tris = 0;
  for (const c of chunks) {
    for (const k of ['side', 'asphalt', 'struct']) {
      const L = c[k];
      if (!L.idx.length) continue;
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(L.pos, 3));
      g.setAttribute('aRoad', new THREE.BufferAttribute(L.road, 4));
      g.setIndex(new THREE.BufferAttribute(L.idx, 1));
      let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity, e0 = 0, e1 = 1;
      for (let i = 0; i < L.pos.length; i += 3) {
        const x = L.pos[i], z = L.pos[i + 2];
        if (x < x0) x0 = x; if (x > x1) x1 = x; if (z < z0) z0 = z; if (z > z1) z1 = z;
      }
      for (let i = 2; i < L.road.length; i += 4) {
        const e = L.road[i];
        if (e < e0) e0 = e; if (e > e1) e1 = e;
      }
      g.boundingSphere = sphereFor(x0, x1, e0 - 3, e1 + 2, z0, z1);
      const m = new THREE.Mesh(g, mats[k]);
      m.receiveShadow = true;
      if (k === 'struct') {
        m.castShadow = true;
        m.customDepthMaterial = mats.depth;
      }
      m.renderOrder = k === 'side' ? 1 : 2;
      m.matrixAutoUpdate = false;
      group.add(m);
      tris += L.idx.length / 3;
    }
  }
  return { group, tris };
}

// ---------------------------------------------------------------------------
export function buildLakes(buf, mat) {
  const dv = new DataView(buf);
  const count = dv.getUint32(4, true);
  const Q = dv.getFloat32(8, true);
  let off = 12;
  const pos = [], elev = [], nor = [], idx = [];
  for (let l = 0; l < count; l++) {
    const level = dv.getInt16(off, true) / 10;
    const nr = dv.getUint8(off + 2);
    off += 3;
    const flat = [], holes = [];
    for (let r = 0; r < nr; r++) {
      const n = dv.getUint16(off, true);
      off += 2;
      if (r > 0) holes.push(flat.length / 2);
      for (let i = 0; i < n; i++) {
        flat.push(dv.getInt16(off, true) * Q, dv.getInt16(off + 2, true) * Q);
        off += 4;
      }
    }
    const base = pos.length / 3;
    for (let i = 0; i < flat.length; i += 2) {
      pos.push(flat[i], 0.12, flat[i + 1]);
      elev.push(level);
      nor.push(0, 1, 0);
    }
    const tri = earcut(flat, holes.length ? holes : undefined, 2);
    for (let t = 0; t < tri.length; t += 3) {
      const [a, b, c] = [tri[t], tri[t + 1], tri[t + 2]];
      const s = (flat[2 * b] - flat[2 * a]) * (flat[2 * c + 1] - flat[2 * a + 1]) - (flat[2 * c] - flat[2 * a]) * (flat[2 * b + 1] - flat[2 * a + 1]);
      if (s > 0) idx.push(base + a, base + c, base + b);
      else idx.push(base + a, base + b, base + c);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('normal', new THREE.Float32BufferAttribute(nor, 3));
  g.setAttribute('aElev', new THREE.Float32BufferAttribute(elev, 1));
  g.setIndex(idx);
  g.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 100, 0), 20000);
  const m = new THREE.Mesh(g, mat);
  m.receiveShadow = true;
  m.renderOrder = 1;
  m.name = 'lakes';
  return m;
}

// ---------------------------------------------------------------------------
function colored(geo, color) {
  const g = geo.index ? geo.toNonIndexed() : geo;
  g.deleteAttribute('uv');
  g.deleteAttribute('normal');
  const c = new THREE.Color(color);
  const arr = new Float32Array(g.attributes.position.count * 3);
  for (let i = 0; i < arr.length; i += 3) { arr[i] = c.r; arr[i + 1] = c.g; arr[i + 2] = c.b; }
  g.setAttribute('color', new THREE.BufferAttribute(arr, 3));
  return g;
}

function treeGeometries() {
  const trunk = '#7a5a3c';
  const conifer = mergeGeometries([
    colored(new THREE.CylinderGeometry(0.32, 0.5, 3.2, 6).translate(0, 1.6, 0), trunk),
    colored(new THREE.ConeGeometry(3.3, 6.5, 8).translate(0, 5.6, 0), '#2f6b3a'),
    colored(new THREE.ConeGeometry(2.5, 5.0, 8).translate(0, 8.4, 0), '#3b7d43'),
    colored(new THREE.ConeGeometry(1.5, 3.4, 8).translate(0, 10.9, 0), '#4a8f4c'),
  ]);
  const broad = mergeGeometries([
    colored(new THREE.CylinderGeometry(0.3, 0.45, 3.4, 6).translate(0, 1.7, 0), trunk),
    colored(new THREE.IcosahedronGeometry(3.4, 1).scale(1, 0.82, 1).translate(0, 5.9, 0), '#4f9a3f'),
    colored(new THREE.IcosahedronGeometry(2.3, 1).translate(1.5, 7.3, 0.7), '#5daa48'),
    colored(new THREE.IcosahedronGeometry(2.1, 1).translate(-1.4, 6.9, -0.9), '#57a343'),
  ]);
  return [broad, conifer];
}

export function buildTrees(buf, shared, chunkSize, extent) {
  const dv = new DataView(buf);
  const count = dv.getUint32(4, true);
  const Q = dv.getFloat32(8, true);
  const recs = [];
  const buckets = new Map();
  let seed = 99;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let i = 0; i < count; i++) {
    const o = 12 + i * 8;
    const r = {
      x: dv.getInt16(o, true) * Q,
      z: dv.getInt16(o + 2, true) * Q,
      e: dv.getInt16(o + 4, true) / 10,
      s: dv.getUint8(o + 6) / 100,
      k: dv.getUint8(o + 7),
      rot: rnd() * Math.PI * 2,
      tint: 0.82 + rnd() * 0.3,
      hue: rnd(),
    };
    const key = `${Math.floor((r.x + extent) / chunkSize)},${Math.floor((r.z + extent) / chunkSize)},${r.k}`;
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(r);
    recs.push(r);
  }
  const geos = treeGeometries();
  const mat = new THREE.MeshStandardMaterial({ vertexColors: true, flatShading: true, roughness: 0.92 });
  const group = new THREE.Group();
  group.name = 'trees';
  const meshes = [];
  const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), p = new THREE.Vector3(), sc = new THREE.Vector3();
  const up = new THREE.Vector3(0, 1, 0);
  const color = new THREE.Color();
  for (const [key, list] of buckets) {
    const kind = Number(key.split(',')[2]);
    const im = new THREE.InstancedMesh(geos[kind], mat, list.length);
    im.castShadow = true;
    im.receiveShadow = true;
    list.forEach((r, i) => {
      color.setRGB(r.tint * (0.95 + 0.1 * r.hue), r.tint, r.tint * (0.9 + 0.1 * (1 - r.hue)));
      im.setColorAt(i, color);
    });
    const [kx, kz] = key.split(',').map(Number);
    im.userData.center = new THREE.Vector2(-extent + (kx + 0.5) * chunkSize, -extent + (kz + 0.5) * chunkSize);
    meshes.push({ im, list });
    group.add(im);
  }
  function place() {
    for (const { im, list } of meshes) {
      list.forEach((r, i) => {
        q.setFromAxisAngle(up, r.rot);
        p.set(r.x, elevOfJS(shared, r.e) - 0.3, r.z);
        sc.setScalar(r.s);
        im.setMatrixAt(i, m4.compose(p, q, sc));
      });
      im.instanceMatrix.needsUpdate = true;
      im.computeBoundingSphere();
    }
  }
  place();
  return { group, place, count, meshes: meshes.map((m) => m.im) };
}

// ---------------------------------------------------------------------------
// Landmarks that are not plain extrusions
// ---------------------------------------------------------------------------
function beam(a, b, r0, r1, color, seg = 6) {
  const dir = new THREE.Vector3().subVectors(b, a);
  const len = dir.length();
  const g = new THREE.CylinderGeometry(r1, r0, len, seg, Math.max(1, Math.round(len / 12)));
  g.translate(0, len / 2, 0);
  g.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize()));
  g.translate(a.x, a.y, a.z);
  return colored(g, color);
}

function bandColors(geo, y0, band, c1, c2) {
  const pos = geo.attributes.position;
  const col = geo.attributes.color;
  const a = new THREE.Color(c1), b = new THREE.Color(c2);
  for (let i = 0; i < pos.count; i += 3) {
    const y = (pos.getY(i) + pos.getY(i + 1) + pos.getY(i + 2)) / 3;
    const c = Math.floor((y - y0) / band) % 2 === 0 ? a : b;
    for (let k = 0; k < 3; k++) col.setXYZ(i + k, c.r, c.g, c.b);
  }
  return geo;
}

export function buildLandmarks(proj, sampleGround, shared) {
  const group = new THREE.Group();
  group.name = 'landmarks';
  const mat = new THREE.MeshStandardMaterial({ vertexColors: true, flatShading: true, roughness: 0.6, metalness: 0.15 });
  const ORANGE = '#c0362c';

  // ---- Golden Gate Bridge: towers, portal struts, main cables, suspenders, stiffening truss
  const S = proj.toWorld(-122.47785, 37.81402);
  const N = proj.toWorld(-122.47923, 37.82551);
  const ax = new THREE.Vector3(N.x - S.x, 0, N.z - S.z);
  const span = ax.length();
  ax.normalize();
  const pr = new THREE.Vector3(-ax.z, 0, ax.x);
  const DECK = 67, TOP = 227, HALF = 16.5;
  const parts = [];
  for (const T of [S, N]) {
    const c = new THREE.Vector3(T.x, 0, T.z);
    for (const sgn of [-1, 1]) {
      const legBase = c.clone().addScaledVector(pr, sgn * HALF);
      const leg = new THREE.BoxGeometry(9, TOP + 12, 15, 1, 12, 1);
      const p = leg.attributes.position;
      for (let i = 0; i < p.count; i++) {
        const t = (p.getY(i) + (TOP + 12) / 2) / (TOP + 12);
        const s = 1 - 0.42 * t;
        p.setX(i, p.getX(i) * s);
        p.setZ(i, p.getZ(i) * s);
      }
      leg.translate(0, (TOP + 12) / 2 - 12, 0);
      leg.rotateY(Math.atan2(ax.x, ax.z));
      leg.translate(legBase.x, 0, legBase.z);
      parts.push(colored(leg, ORANGE));
    }
    for (const h of [DECK - 9, 118, 160, 198, TOP - 6]) {
      const a = c.clone().addScaledVector(pr, -HALF).setY(h);
      const b = c.clone().addScaledVector(pr, HALF).setY(h);
      const strut = new THREE.BoxGeometry(2 * HALF, h > TOP - 10 ? 9 : 6, 6);
      strut.rotateY(Math.atan2(ax.x, ax.z)); // box x-axis -> across the bridge
      strut.translate((a.x + b.x) / 2, h, (a.z + b.z) / 2);
      parts.push(colored(strut, ORANGE));
    }
    const pier = new THREE.BoxGeometry(48, 22, 30);
    pier.rotateY(Math.atan2(pr.x, pr.z));
    pier.translate(c.x, -6, c.z);
    parts.push(colored(pier, '#b9b2a6'));
  }
  // stiffening truss under the deck, from anchorage to anchorage
  const SIDE = 343;
  const a0 = new THREE.Vector3(S.x, 0, S.z).addScaledVector(ax, -SIDE);
  const a1 = new THREE.Vector3(N.x, 0, N.z).addScaledVector(ax, SIDE);
  const truss = new THREE.BoxGeometry(27, 7.5, span + 2 * SIDE, 1, 1, 60);
  truss.rotateY(Math.atan2(ax.x, ax.z));
  truss.translate((a0.x + a1.x) / 2, DECK - 4.2, (a0.z + a1.z) / 2);
  parts.push(colored(truss, ORANGE));

  // main cables (parabolic main span, sagging side spans) + vertical suspenders
  const cablePts = [];
  const lines = [];
  for (const sgn of [-1, 1]) {
    const pts = [];
    const off = pr.clone().multiplyScalar(sgn * HALF);
    const anchorY = DECK + 6;
    for (let i = 0; i <= 20; i++) {
      const s = i / 20;
      const pos = a0.clone().addScaledVector(ax, s * SIDE).add(off);
      pts.push(pos.setY(anchorY + (TOP - anchorY) * s - 14 * Math.sin(Math.PI * s)));
    }
    for (let i = 1; i <= 60; i++) {
      const s = i / 60;
      const pos = new THREE.Vector3(S.x, 0, S.z).addScaledVector(ax, s * span).add(off);
      const y = DECK + 4 + (TOP - DECK - 4) * Math.pow(2 * s - 1, 2);
      pts.push(pos.setY(y));
    }
    for (let i = 1; i <= 20; i++) {
      const s = i / 20;
      const pos = new THREE.Vector3(N.x, 0, N.z).addScaledVector(ax, s * SIDE).add(off);
      pts.push(pos.setY(TOP + (anchorY - TOP) * s - 14 * Math.sin(Math.PI * s)));
    }
    const curve = new THREE.CatmullRomCurve3(pts);
    parts.push(colored(new THREE.TubeGeometry(curve, 240, 0.65, 6), ORANGE));
    cablePts.push(curve);
    for (let d = 15; d < span + 2 * SIDE - 15; d += 15) {
      const t = d / (span + 2 * SIDE);
      const top = curve.getPointAt(Math.min(1, Math.max(0, t)));
      lines.push(top.x, top.y, top.z, top.x, DECK + 0.5, top.z);
    }
  }
  const gg = new THREE.Mesh(mergeGeometries(parts), mat);
  gg.castShadow = true;
  gg.receiveShadow = true;
  gg.name = 'golden-gate-bridge';
  group.add(gg);
  const lg = new THREE.BufferGeometry();
  lg.setAttribute('position', new THREE.Float32BufferAttribute(lines, 3));
  group.add(new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ color: ORANGE })));

  // ---- Sutro Tower: three tapering legs, two triangular crossarm levels, masts
  const st = proj.toWorld(-122.45281, 37.75518);
  const sutro = new THREE.Group();
  const sp = [];
  const legPt = (k, r, y) => new THREE.Vector3(Math.cos(k * 2.094 + 0.5) * r, y, Math.sin(k * 2.094 + 0.5) * r);
  for (let k = 0; k < 3; k++) {
    sp.push(beam(legPt(k, 24, -3), legPt(k, 9, 160), 2.6, 1.9, '#d23a33', 6));
    sp.push(beam(legPt(k, 9, 160), legPt(k, 7, 232), 1.9, 1.5, '#d23a33', 6));
    sp.push(beam(legPt(k, 7, 232), legPt(k, 4, 298), 0.9, 0.5, '#d23a33', 6));
    for (const [y, r] of [[160, 22], [232, 19]]) {
      sp.push(beam(legPt(k, r, y), legPt(k + 1, r, y), 1.1, 1.1, '#d23a33', 5));
      sp.push(beam(legPt(k, 8, y), legPt(k, r, y), 0.9, 0.9, '#d23a33', 5));
    }
    for (let y = 30; y < 160; y += 32) {
      const r0 = 24 - (15 * (y + 3)) / 163, r1 = 24 - (15 * (y + 35)) / 163;
      sp.push(beam(legPt(k, r0, y), legPt(k + 1, r1, y + 32), 0.5, 0.5, '#d23a33', 4));
      sp.push(beam(legPt(k + 1, r0, y), legPt(k, r1, y + 32), 0.5, 0.5, '#d23a33', 4));
    }
  }
  const sg = bandColors(mergeGeometries(sp), -3, 25, '#d23a33', '#f2efe8');
  const sm = new THREE.Mesh(sg, mat);
  sm.castShadow = true;
  sm.receiveShadow = true;
  sutro.add(sm);
  sutro.position.set(st.x, 0, st.z);
  sutro.name = 'sutro-tower';
  group.add(sutro);
  const sutroGround = sampleGround(st.x, st.z);

  function place() {
    sutro.position.y = elevOfJS(shared, sutroGround);
  }
  place();
  return { group, place };
}
