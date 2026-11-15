// Web worker: turns the compact binary city data into chunked, GPU-ready vertex arrays.
import earcut from 'earcut';

class Grow {
  constructor(Type, cap = 4096) {
    this.Type = Type;
    this.a = new Type(cap);
    this.n = 0;
  }
  reserve(k) {
    if (this.n + k <= this.a.length) return;
    let cap = this.a.length * 2;
    while (cap < this.n + k) cap *= 2;
    const b = new this.Type(cap);
    b.set(this.a.subarray(0, this.n));
    this.a = b;
  }
  push1(v) { this.reserve(1); this.a[this.n++] = v; }
  push2(a, b) { this.reserve(2); this.a[this.n++] = a; this.a[this.n++] = b; }
  push3(a, b, c) { this.reserve(3); this.a[this.n++] = a; this.a[this.n++] = b; this.a[this.n++] = c; }
  push4(a, b, c, d) { this.reserve(4); this.a[this.n++] = a; this.a[this.n++] = b; this.a[this.n++] = c; this.a[this.n++] = d; }
  out() { return this.a.slice(0, this.n); }
}

// ---------------------------------------------------------------------------
// Buildings
// ---------------------------------------------------------------------------
let CUR_VAC = 0; // vacancy code of the building being emitted (0 unknown, 1 occupied, 2 vacant)

class BuildingChunk {
  constructor(cx, cz, ox, oz, Q) {
    Object.assign(this, { cx, cz, ox, oz, Q });
    this.pos = new Grow(Int16Array);
    this.ue = new Grow(Int16Array); // u*20, elevation*10, wall top*10, 0
    this.meta = new Grow(Uint8Array);
    this.ctr = new Grow(Int16Array); // building centroid (chunk-local quanta) for the clear-radius switch
    this.idx = new Grow(Uint32Array);
    this.cur = [0, 0];
    this.vcount = 0;
    this.maxY = 0;
    this.minY = 1e9;
  }
  // x/z world metres, y metres relative to the building base; u = centred facade coordinate
  vert(x, y, z, u, elev, wallTop, type, seed) {
    const Q = this.Q;
    this.pos.push3(Math.round((x - this.ox) / Q), Math.round(y / Q), Math.round((z - this.oz) / Q));
    this.ue.push4(Math.round(u * 20), Math.round(elev * 10), Math.round(wallTop * 10), CUR_VAC);
    this.meta.push2(type, seed);
    this.ctr.push2(this.cur[0], this.cur[1]);
    const top = elev + y;
    if (top > this.maxY) this.maxY = top;
    if (elev < this.minY) this.minY = elev;
    return this.vcount++;
  }
}

function ringArea(p) {
  let a = 0;
  const n = p.length / 2;
  for (let i = 0, j = n - 1; i < n; j = i++) a += p[2 * j] * p[2 * i + 1] - p[2 * i] * p[2 * j + 1];
  return a / 2;
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

function scaleRing(p, c, s) {
  const out = new Float64Array(p.length);
  for (let i = 0; i < p.length; i += 2) {
    out[i] = c[0] + (p[i] - c[0]) * s;
    out[i + 1] = c[1] + (p[i + 1] - c[1]) * s;
  }
  return out;
}

// Upward-facing triangle: normal.y = -2 * signedArea_xz, so we want a negative xz area.
function triUp(ch, P, a, b, c) {
  const ax = P[a][0], az = P[a][1];
  const s = (P[b][0] - ax) * (P[c][1] - az) - (P[c][0] - ax) * (P[b][1] - az);
  if (s > 0) ch.idx.push3(P[a][2], P[c][2], P[b][2]);
  else ch.idx.push3(P[a][2], P[b][2], P[c][2]);
}

/** Wall quads per edge (4 own vertices so each facade gets its own centred window grid). */
function emitWalls(ch, base, top, y0, y1, elev, wallTop, type, seed) {
  const n = base.length / 2;
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    const x0 = base[2 * i], z0 = base[2 * i + 1], x1 = base[2 * j], z1 = base[2 * j + 1];
    const L = Math.hypot(x1 - x0, z1 - z0);
    if (L < 0.05) continue;
    const h = L / 2;
    const b0 = ch.vert(x0, y0, z0, -h, elev, wallTop, type, seed);
    const b1 = ch.vert(x1, y0, z1, h, elev, wallTop, type, seed);
    const t1 = ch.vert(top[2 * j], y1, top[2 * j + 1], h, elev, wallTop, type, seed);
    const t0 = ch.vert(top[2 * i], y1, top[2 * i + 1], -h, elev, wallTop, type, seed);
    // provoking (last) vertex of each triangle carries |u| = half facade length
    ch.idx.push3(b0, b1, t1);
    ch.idx.push3(b0, t1, t0);
  }
}

function emitCap(ch, rings, y, elev, wallTop, type, seed) {
  const flat = [];
  const holes = [];
  for (let r = 0; r < rings.length; r++) {
    if (r > 0) holes.push(flat.length / 2);
    for (let i = 0; i < rings[r].length; i++) flat.push(rings[r][i]);
  }
  const tri = earcut(flat, holes.length ? holes : undefined, 2);
  const P = new Array(flat.length / 2);
  for (let i = 0; i < P.length; i++) {
    P[i] = [flat[2 * i], flat[2 * i + 1], ch.vert(flat[2 * i], y, flat[2 * i + 1], 0, elev, wallTop, type, seed)];
  }
  for (let t = 0; t < tri.length; t += 3) triUp(ch, P, tri[t], tri[t + 1], tri[t + 2]);
}

function emitHipRoof(ch, p, y, rise, elev, type, seed) {
  // p: 4-vertex ring. Ridge runs along the longer axis, inset by half the width (a true hip).
  const pt = (i) => [p[2 * (i % 4)], p[2 * (i % 4) + 1]];
  const len = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  const e0 = (len(pt(0), pt(1)) + len(pt(2), pt(3))) / 2;
  const e1 = (len(pt(1), pt(2)) + len(pt(3), pt(0))) / 2;
  const k = e0 >= e1 ? 0 : 1; // edges k and k+2 are the long sides
  const A = pt(k), B = pt(k + 1), C = pt(k + 2), D = pt(k + 3);
  const mA = [(D[0] + A[0]) / 2, (D[1] + A[1]) / 2];
  const mB = [(B[0] + C[0]) / 2, (B[1] + C[1]) / 2];
  const L = len(mA, mB);
  const w = Math.min(e0, e1);
  const inset = Math.min(w / 2, L / 2 - 0.01);
  const ax = (mB[0] - mA[0]) / (L || 1), az = (mB[1] - mA[1]) / (L || 1);
  const r0 = [mA[0] + ax * inset, mA[1] + az * inset];
  const r1 = [mB[0] - ax * inset, mB[1] - az * inset];
  const top = y + rise;
  const V = (q, yy) => [q[0], q[1], ch.vert(q[0], yy, q[1], 0, elev, y, type, seed)];
  // separate vertices per face keeps the flat-shaded facets crisp
  const face = (pts) => {
    const P = pts.map(([q, yy]) => V(q, yy));
    for (let i = 1; i + 1 < P.length; i++) triUp(ch, P, 0, i, i + 1);
  };
  face([[A, y], [B, y], [r1, top], [r0, top]]);
  face([[B, y], [C, y], [r1, top]]);
  face([[C, y], [D, y], [r0, top], [r1, top]]);
  face([[D, y], [A, y], [r0, top]]);
}

function buildBuildings(buf, CH, EXT, post, part = 'both', vacancy = null) {
  const doFull = part !== 'lod', doLod = part !== 'full';
  const dv = new DataView(buf);
  const count = dv.getUint32(4, true);
  const Q = dv.getFloat32(8, true);
  let off = 12;
  const chunks = new Map();
  const lodChunks = new Map();
  const getChunk = (x, z, map = chunks) => {
    const cx = Math.floor((x + EXT) / CH), cz = Math.floor((z + EXT) / CH);
    const key = cx * 1000 + cz;
    let c = map.get(key);
    if (!c) {
      c = new BuildingChunk(cx, cz, -EXT + (cx + 0.5) * CH, -EXT + (cz + 0.5) * CH, Q);
      map.set(key, c);
    }
    c.cur[0] = Math.round((x - c.ox) / Q);
    c.cur[1] = Math.round((z - c.oz) / Q);
    return c;
  };
  let seedState = 1234567;
  for (let b = 0; b < count; b++) {
    const type = dv.getUint8(off);
    const nr = dv.getUint8(off + 1);
    const h = dv.getUint16(off + 2, true) / 10;
    const elev = dv.getInt16(off + 4, true) / 10;
    const bottom = dv.getInt16(off + 6, true) / 10;
    const roof = dv.getUint16(off + 8, true) / 10;
    const kind = dv.getUint8(off + 10);
    const extra = dv.getUint8(off + 11);
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
    CUR_VAC = vacancy && b < vacancy.length ? vacancy[b] : 0;
    seedState = (seedState * 1103515245 + 12345) & 0x7fffffff;
    const seed = seedState & 255;
    const outer = rings[0];
    const c = ringCentroid(outer);
    if (doLod) emitLod(getChunk(c[0], c[1], lodChunks), rings, c, h, bottom, elev, roof, kind, extra, type, seed);
    if ((b & 8191) === 0) post({ type: 'progress', stage: doFull ? 'buildings' : 'lod', p: b / count });
    if (!doFull) continue;
    const ch = getChunk(c[0], c[1]);

    if (kind === 2) {
      // Tapered tower: footprint shrinks linearly to 12 % at the top.
      const topRing = scaleRing(outer, c, 0.12);
      const bot = scaleRing(outer, c, 1.0);
      emitWalls(ch, bot, topRing, Math.min(bottom, 0), h, elev, h, type, seed);
      emitCap(ch, [topRing], h, elev, h, type, seed);
    } else if (kind === 3) {
      // Stepped: podium + inset tower block.
      const s = extra / 255;
      for (const r of rings) emitWalls(ch, r, r, bottom, h, elev, h, type, seed);
      emitCap(ch, rings, h, elev, h, type, seed);
      const up = scaleRing(outer, c, s);
      emitWalls(ch, up, up, h - 0.3, h + roof, elev, h + roof, type, seed);
      emitCap(ch, [up], h + roof, elev, h + roof, type, seed);
    } else {
      for (const r of rings) emitWalls(ch, r, r, bottom, h, elev, h, type, seed);
      if (kind === 1 && rings.length === 1 && outer.length === 8 && roof > 0) {
        emitHipRoof(ch, outer, h, roof, elev, type, seed);
      } else {
        emitCap(ch, rings, h, elev, h, type, seed);
      }
    }
  }
  const pack = (c) => ({
    cx: c.cx, cz: c.cz, ox: c.ox, oz: c.oz, Q: c.Q, minY: c.minY, maxY: c.maxY,
    pos: c.pos.out(), ue: c.ue.out(), meta: c.meta.out(), ctr: c.ctr.out(), idx: c.idx.out(),
  });
  return { full: [...chunks.values()].map(pack), lod: [...lodChunks.values()].map(pack) };
}

// ---------------------------------------------------------------------------
// Low-detail stand-ins used outside the clear radius: simplified footprint prism,
// shared vertices, no holes, no facade quads.
// ---------------------------------------------------------------------------
function rdpOpen(pts, eps) {
  const n = pts.length / 2;
  if (n < 3) return pts;
  const keep = new Uint8Array(n);
  keep[0] = keep[n - 1] = 1;
  const stack = [[0, n - 1]];
  while (stack.length) {
    const [a, b] = stack.pop();
    if (b <= a + 1) continue;
    const ax = pts[2 * a], az = pts[2 * a + 1], dx = pts[2 * b] - ax, dz = pts[2 * b + 1] - az;
    const L = Math.hypot(dx, dz) || 1e-9;
    let best = -1, bi = -1;
    for (let i = a + 1; i < b; i++) {
      const d = Math.abs((pts[2 * i] - ax) * dz - (pts[2 * i + 1] - az) * dx) / L;
      if (d > best) { best = d; bi = i; }
    }
    if (best > eps) { keep[bi] = 1; stack.push([a, bi], [bi, b]); }
  }
  const out = [];
  for (let i = 0; i < n; i++) if (keep[i]) out.push(pts[2 * i], pts[2 * i + 1]);
  return out;
}

function simplifyRing(p, eps) {
  const n = p.length / 2;
  if (n <= 4) return p;
  let far = 0, fd = -1;
  for (let i = 1; i < n; i++) {
    const d = Math.hypot(p[2 * i] - p[0], p[2 * i + 1] - p[1]);
    if (d > fd) { fd = d; far = i; }
  }
  const a = rdpOpen(Array.from(p.subarray(0, 2 * far + 2)), eps);
  const b = rdpOpen([...Array.from(p.subarray(2 * far)), p[0], p[1]], eps);
  const ring = a.slice(0, -2).concat(b.slice(0, -2));
  return ring.length >= 6 ? new Float64Array(ring) : p;
}

function emitPrism(ch, ring, y0, y1, elev, type, seed) {
  const n = ring.length / 2;
  const bot = new Array(n), top = new Array(n);
  for (let i = 0; i < n; i++) {
    bot[i] = ch.vert(ring[2 * i], y0, ring[2 * i + 1], 0, elev, y1, type, seed);
    top[i] = ch.vert(ring[2 * i], y1, ring[2 * i + 1], 0, elev, y1, type, seed);
  }
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    ch.idx.push3(bot[i], bot[j], top[j]);
    ch.idx.push3(bot[i], top[j], top[i]);
  }
  const tri = earcut(Array.from(ring), undefined, 2);
  const P = top.map((v, i) => [ring[2 * i], ring[2 * i + 1], v]);
  for (let t = 0; t < tri.length; t += 3) triUp(ch, P, tri[t], tri[t + 1], tri[t + 2]);
}

function emitLod(ch, rings, c, h, bottom, elev, roof, kind, extra, type, seed) {
  const outer = rings[0];
  if (Math.abs(ringArea(outer)) < 25 && h < 6) return; // sheds vanish in the blur anyway
  const ring = simplifyRing(outer, 2.0);
  if (kind === 2) {
    const topRing = scaleRing(ring, c, 0.12);
    emitWalls(ch, ring, topRing, Math.min(bottom, 0), h, elev, h, type, seed);
    emitCap(ch, [topRing], h, elev, h, type, seed);
  } else if (kind === 3) {
    emitPrism(ch, ring, bottom, h, elev, type, seed);
    emitPrism(ch, scaleRing(ring, c, extra / 255), h - 0.3, h + roof, elev, type, seed);
  } else {
    emitPrism(ch, ring, bottom, kind === 1 ? h + roof * 0.5 : h, elev, type, seed);
  }
}

// ---------------------------------------------------------------------------
// Roads: two stacked ribbons (sidewalk below, asphalt above) + bridge structure
// ---------------------------------------------------------------------------
class RoadLayer {
  constructor() {
    this.pos = new Grow(Float32Array);
    this.road = new Grow(Float32Array);
    this.idx = new Grow(Uint32Array);
    this.v = 0;
  }
  vert(x, y, z, along, across, elev, code) {
    this.pos.push3(x, y, z);
    this.road.push4(along, across, elev, code);
    return this.v++;
  }
  out() {
    return { pos: this.pos.out(), road: this.road.out(), idx: this.idx.out() };
  }
}

const CLASS_ORDER = [8, 7, 6, 5, 4, 3, 2, 1, 0]; // draw rank: higher class sits slightly higher

function ribbon(layer, xs, zs, es, along, halfW, yOff, code) {
  const n = xs.length;
  let prevL = -1, prevR = -1;
  for (let i = 0; i < n; i++) {
    // averaged segment normals with a clamped miter
    let dx0 = 0, dz0 = 0, dx1 = 0, dz1 = 0;
    if (i > 0) { dx0 = xs[i] - xs[i - 1]; dz0 = zs[i] - zs[i - 1]; const l = Math.hypot(dx0, dz0) || 1; dx0 /= l; dz0 /= l; }
    if (i < n - 1) { dx1 = xs[i + 1] - xs[i]; dz1 = zs[i + 1] - zs[i]; const l = Math.hypot(dx1, dz1) || 1; dx1 /= l; dz1 /= l; }
    if (i === 0) { dx0 = dx1; dz0 = dz1; }
    if (i === n - 1) { dx1 = dx0; dz1 = dz0; }
    let nx = -(dz0 + dz1), nz = dx0 + dx1;
    const nl = Math.hypot(nx, nz) || 1;
    nx /= nl; nz /= nl;
    const cosA = Math.max(0.4, nx * -dz1 + nz * dx1);
    const m = halfW / cosA;
    const L = layer.vert(xs[i] + nx * m, yOff, zs[i] + nz * m, along[i], -halfW, es[i], code);
    const R = layer.vert(xs[i] - nx * m, yOff, zs[i] - nz * m, along[i], halfW, es[i], code);
    if (i > 0) {
      // last vertex of each triangle carries +-halfW for the flat varying
      layer.idx.push3(prevL, R, prevR);
      layer.idx.push3(prevL, L, R);
    }
    prevL = L;
    prevR = R;
  }
}

function boxPillar(layer, x, z, dirx, dirz, w, d, groundElev, deckElev, code) {
  // 4 vertical faces; bottom vertices follow the terrain (code without bridge flag),
  // top vertices use the absolute deck height (bridge flag).
  const px = -dirz, pz = dirx;
  const corners = [
    [x + dirx * d + px * w, z + dirz * d + pz * w],
    [x - dirx * d + px * w, z - dirz * d + pz * w],
    [x - dirx * d - px * w, z - dirz * d - pz * w],
    [x + dirx * d - px * w, z + dirz * d - pz * w],
  ];
  for (let i = 0; i < 4; i++) {
    const a = corners[i], b = corners[(i + 1) % 4];
    const v0 = layer.vert(a[0], -2, a[1], 0, 0, groundElev, code);
    const v1 = layer.vert(b[0], -2, b[1], 0, 0, groundElev, code);
    const v2 = layer.vert(b[0], -0.4, b[1], 0, 0, deckElev, code + 16);
    const v3 = layer.vert(a[0], -0.4, a[1], 0, 0, deckElev, code + 16);
    layer.idx.push3(v0, v1, v2);
    layer.idx.push3(v0, v2, v3);
  }
}

function buildRoads(buf, CH, EXT, sampleGround, post) {
  const dv = new DataView(buf);
  const count = dv.getUint32(4, true);
  const Q = dv.getFloat32(8, true);
  let off = 12;
  const chunks = new Map();
  const getChunk = (x, z) => {
    const cx = Math.floor((x + EXT) / CH), cz = Math.floor((z + EXT) / CH);
    const key = cx * 1000 + cz;
    let c = chunks.get(key);
    if (!c) {
      c = { cx, cz, side: new RoadLayer(), asphalt: new RoadLayer(), struct: new RoadLayer() };
      chunks.set(key, c);
    }
    return c;
  };
  for (let r = 0; r < count; r++) {
    const cls = dv.getUint8(off);
    const flags = dv.getUint8(off + 1);
    const width = dv.getUint8(off + 2) / 5;
    const side = dv.getUint8(off + 3) / 10;
    const n = dv.getUint16(off + 4, true);
    off += 6;
    const xs = new Float64Array(n), zs = new Float64Array(n), es = new Float64Array(n), along = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      xs[i] = dv.getInt16(off, true) * Q;
      zs[i] = dv.getInt16(off + 2, true) * Q;
      es[i] = dv.getInt16(off + 4, true) / 10;
      off += 6;
      if (i > 0) along[i] = along[i - 1] + Math.hypot(xs[i] - xs[i - 1], zs[i] - zs[i - 1]);
    }
    const mid = n >> 1;
    const ch = getChunk(xs[mid], zs[mid]);
    const bridge = flags & 1;
    const oneway = (flags >> 1) & 1;
    const code = cls + 16 * bridge + 32 * oneway;
    const rank = CLASS_ORDER.indexOf(cls) * 0.03;
    const yA = (bridge ? 0.05 : 0.55) + rank;
    if (side > 0) ribbon(ch.side, xs, zs, es, along, width / 2 + side, yA - 0.12, code + 64);
    ribbon(ch.asphalt, xs, zs, es, along, width / 2, yA, code);

    if (bridge) {
      const halfW = width / 2 + side + 0.3;
      // fascia: vertical side skirts under both deck edges
      for (const sgn of [-1, 1]) {
        let prevT = -1, prevB = -1;
        for (let i = 0; i < n; i++) {
          let dx = 0, dz = 0;
          if (i < n - 1) { dx = xs[i + 1] - xs[i]; dz = zs[i + 1] - zs[i]; } else { dx = xs[i] - xs[i - 1]; dz = zs[i] - zs[i - 1]; }
          const l = Math.hypot(dx, dz) || 1;
          const nx = -dz / l * sgn, nz = dx / l * sgn;
          const x = xs[i] + nx * halfW, z = zs[i] + nz * halfW;
          const T = ch.struct.vert(x, 0.9, z, along[i], 0, es[i], 15 + 16);
          const B = ch.struct.vert(x, -1.9, z, along[i], 0, es[i], 15 + 16);
          if (i > 0) {
            ch.struct.idx.push3(prevB, B, T);
            ch.struct.idx.push3(prevB, T, prevT);
          }
          prevT = T;
          prevB = B;
        }
      }
      // pillars every ~42 m where the deck clears the ground (not mid-water spans)
      let next = 20;
      for (let i = 1; i < n; i++) {
        while (along[i] >= next) {
          const t = (next - along[i - 1]) / Math.max(1e-6, along[i] - along[i - 1]);
          const x = xs[i - 1] + (xs[i] - xs[i - 1]) * t;
          const z = zs[i - 1] + (zs[i] - zs[i - 1]) * t;
          const deck = es[i - 1] + (es[i] - es[i - 1]) * t;
          const g = sampleGround(x, z);
          if (g > 0.5 && deck - g > 3.5) {
            const dx = xs[i] - xs[i - 1], dz = zs[i] - zs[i - 1];
            const l = Math.hypot(dx, dz) || 1;
            boxPillar(ch.struct, x, z, dx / l, dz / l, Math.min(width * 0.32, 4), 1.1, g, deck, 15);
          }
          next += 42;
        }
      }
    }
    if ((r & 2047) === 0) post({ type: 'progress', stage: 'roads', p: r / count });
  }
  return [...chunks.values()].map((c) => ({
    cx: c.cx, cz: c.cz, side: c.side.out(), asphalt: c.asphalt.out(), struct: c.struct.out(),
  }));
}

self.onmessage = (e) => {
  // part: 'full' = detailed buildings + roads, 'lod' = low-detail stand-ins (run in a second worker)
  const { buildingsBuf, roadsBuf, terrain, chunkSize, part = 'both', vacancy = null } = e.data;
  const post = (m) => self.postMessage(m);
  const { n, grid, extent, heights } = terrain;
  const sampleGround = (x, z) => {
    let fx = (x + extent) / grid, fz = (z + extent) / grid;
    fx = Math.min(Math.max(fx, 0), n - 1.001);
    fz = Math.min(Math.max(fz, 0), n - 1.001);
    const i = Math.floor(fx), j = Math.floor(fz), u = fx - i, v = fz - j;
    const a = heights[j * n + i] * (1 - u) + heights[j * n + i + 1] * u;
    const b = heights[(j + 1) * n + i] * (1 - u) + heights[(j + 1) * n + i + 1] * u;
    return a * (1 - v) + b * v;
  };
  try {
    const buildings = buildBuildings(buildingsBuf, chunkSize, extent, post, part, vacancy);
    const roads = part === 'lod' ? [] : buildRoads(roadsBuf, chunkSize, extent, sampleGround, post);
    const transfers = [];
    for (const c of [...buildings.full, ...buildings.lod]) transfers.push(c.pos.buffer, c.ue.buffer, c.meta.buffer, c.ctr.buffer, c.idx.buffer);
    for (const c of roads) for (const k of ['side', 'asphalt', 'struct']) transfers.push(c[k].pos.buffer, c[k].road.buffer, c[k].idx.buffer);
    self.postMessage({ type: 'done', buildings, roads }, transfers);
  } catch (err) {
    self.postMessage({ type: 'error', message: String(err && err.stack || err) });
  }
};
