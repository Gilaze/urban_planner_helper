// Loading of the binary city data produced by pipeline/build_data.py.

async function fetchBuffer(url, onProgress) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  const total = Number(res.headers.get('content-length')) || 0;
  if (!res.body || !total || !onProgress) return res.arrayBuffer();
  const reader = res.body.getReader();
  const out = new Uint8Array(total);
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (got + value.length > out.length) return new Blob([out.subarray(0, got), value]).arrayBuffer(); // compressed size mismatch
    out.set(value, got);
    got += value.length;
    onProgress(got / total);
  }
  return out.buffer.slice(0, got);
}

function checkMagic(buf, magic) {
  const m = new TextDecoder().decode(new Uint8Array(buf, 0, 4));
  if (m !== magic) throw new Error(`Unexpected data file header ${m}, expected ${magic}. Re-run: npm run data`);
}

export async function loadCityData(onProgress = () => {}) {
  const base = `${import.meta.env.BASE_URL}data/`;
  const parts = { meta: 0, terrain: 0, buildings: 0, roads: 0, trees: 0, lakes: 0, vacancy: 0 };
  const report = (k) => (p) => {
    parts[k] = p;
    const w = { meta: 0.01, terrain: 0.15, buildings: 0.58, roads: 0.12, trees: 0.08, lakes: 0.04, vacancy: 0.02 };
    onProgress(Object.keys(parts).reduce((s, key) => s + parts[key] * w[key], 0));
  };
  const meta = await (await fetch(base + 'meta.json')).json();
  report('meta')(1);
  const [terrainBuf, buildingsBuf, roadsBuf, treesBuf, lakesBuf, vacancyBuf] = await Promise.all([
    fetchBuffer(base + 'terrain.bin', report('terrain')),
    fetchBuffer(base + 'buildings.bin', report('buildings')),
    fetchBuffer(base + 'roads.bin', report('roads')),
    fetchBuffer(base + 'trees.bin', report('trees')),
    fetchBuffer(base + 'lakes.bin', report('lakes')),
    fetchBuffer(base + 'vacancy.bin', report('vacancy')),
  ]);
  checkMagic(vacancyBuf, 'SFV1');
  // one byte per building, in buildings.bin order: 0 unknown, 1 occupied, 2 vacant
  const vacancy = new Uint8Array(vacancyBuf, 8, new DataView(vacancyBuf).getUint32(4, true));
  checkMagic(terrainBuf, 'SFH1');
  checkMagic(buildingsBuf, 'SFB2');
  checkMagic(roadsBuf, 'SFR1');
  checkMagic(treesBuf, 'SFT1');
  checkMagic(lakesBuf, 'SFL1');

  const tv = new DataView(terrainBuf);
  const n = tv.getUint32(4, true);
  const grid = tv.getFloat32(8, true);
  const extent = tv.getFloat32(12, true);
  const raw = new Int16Array(terrainBuf.slice(16, 16 + n * n * 2));
  const heights = new Float32Array(n * n);
  for (let i = 0; i < raw.length; i++) heights[i] = raw[i] / 10;

  return {
    meta,
    terrain: { n, grid, extent, heights },
    buildingsBuf,
    roadsBuf,
    treesBuf,
    lakesBuf,
    vacancy,
  };
}

/** Bilinear terrain sample (metres) at world x/z - identical to the pipeline's sampler. */
export function makeHeightSampler(terrain) {
  const { n, grid, extent, heights } = terrain;
  return function sample(x, z) {
    let fx = (x + extent) / grid;
    let fz = (z + extent) / grid;
    fx = Math.min(Math.max(fx, 0), n - 1.001);
    fz = Math.min(Math.max(fz, 0), n - 1.001);
    const i = Math.floor(fx);
    const j = Math.floor(fz);
    const u = fx - i;
    const v = fz - j;
    const a = heights[j * n + i] * (1 - u) + heights[j * n + i + 1] * u;
    const b = heights[(j + 1) * n + i] * (1 - u) + heights[(j + 1) * n + i + 1] * u;
    return a * (1 - v) + b * v;
  };
}

export function makeProjection(meta) {
  const { lon, lat, metersPerDegLon, metersPerDegLat } = meta.origin;
  return {
    toWorld: (lo, la) => ({ x: (lo - lon) * metersPerDegLon, z: -(la - lat) * metersPerDegLat }),
    toLonLat: (x, z) => ({ lon: lon + x / metersPerDegLon, lat: lat - z / metersPerDegLat }),
  };
}
