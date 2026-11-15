// Building mesh emission shared by the geometry worker and the main thread (user edits).
// Vertex layout: see BuildingChunk.vert. Moved out of geometryWorker.js unchanged, plus a
// per-vertex building id (aBid) so individual buildings can be hidden in the shader.
import earcut from 'earcut';

export class Grow {
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

// State of the building currently being emitted (set before emitting each building).
// vac: vacancy code (0 unknown, 1 occupied, 2 vacant lot, 3 closed); bid: building index, -1 for user-added.
export const emitState = { vac: 0, bid: -1 };

export class BuildingChunk {
  constructor(cx, cz, ox, oz, Q) {
    Object.assign(this, { cx, cz, ox, oz, Q });
    this.pos = new Grow(Int16Array);
    this.ue = new Grow(Int16Array); // u*20, elevation*10, wall top*10, 0
    this.meta = new Grow(Uint8Array);
    this.ctr = new Grow(Int16Array); // building centroid (chunk-local quanta) for the clear-radius switch
    this.idx = new Grow(Uint32Array);
    this.bid = new Grow(Float32Array); // building index per vertex (-1 = user-added)
    this.cur = [0, 0];
    this.vcount = 0;
    this.maxY = 0;
    this.minY = 1e9;
  }
  // x/z world metres, y metres relative to the building base; u = centred facade coordinate
  vert(x, y, z, u, elev, wallTop, type, seed) {
    const Q = this.Q;
    this.pos.push3(Math.round((x - this.ox) / Q), Math.round(y / Q), Math.round((z - this.oz) / Q));
    this.ue.push4(Math.round(u * 20), Math.round(elev * 10), Math.round(wallTop * 10), emitState.vac);
    this.bid.push1(emitState.bid);
    this.meta.push2(type, seed);
    this.ctr.push2(this.cur[0], this.cur[1]);
    const top = elev + y;
    if (top > this.maxY) this.maxY = top;
    if (elev < this.minY) this.minY = elev;
    return this.vcount++;
  }
}

export function ringArea(p) {
  let a = 0;
  const n = p.length / 2;
  for (let i = 0, j = n - 1; i < n; j = i++) a += p[2 * j] * p[2 * i + 1] - p[2 * i] * p[2 * j + 1];
  return a / 2;
}

export function ringCentroid(p) {
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

export function scaleRing(p, c, s) {
  const out = new Float64Array(p.length);
  for (let i = 0; i < p.length; i += 2) {
    out[i] = c[0] + (p[i] - c[0]) * s;
    out[i + 1] = c[1] + (p[i + 1] - c[1]) * s;
  }
  return out;
}

// Upward-facing triangle: normal.y = -2 * signedArea_xz, so we want a negative xz area.
export function triUp(ch, P, a, b, c) {
  const ax = P[a][0], az = P[a][1];
  const s = (P[b][0] - ax) * (P[c][1] - az) - (P[c][0] - ax) * (P[b][1] - az);
  if (s > 0) ch.idx.push3(P[a][2], P[c][2], P[b][2]);
  else ch.idx.push3(P[a][2], P[b][2], P[c][2]);
}

/** Wall quads per edge (4 own vertices so each facade gets its own centred window grid). */
export function emitWalls(ch, base, top, y0, y1, elev, wallTop, type, seed) {
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

export function emitCap(ch, rings, y, elev, wallTop, type, seed) {
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

export function emitPrism(ch, ring, y0, y1, elev, type, seed) {
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

/** Transferable/plain form of a chunk for building a mesh. */
export function packChunk(c) {
  return {
    cx: c.cx, cz: c.cz, ox: c.ox, oz: c.oz, Q: c.Q, minY: c.minY, maxY: c.maxY,
    pos: c.pos.out(), ue: c.ue.out(), meta: c.meta.out(), ctr: c.ctr.out(), idx: c.idx.out(), bid: c.bid.out(),
  };
}
