import * as THREE from 'three';

// ---------------------------------------------------------------------------
// Shared uniforms (one object, referenced by every material, so toggles are instant)
// ---------------------------------------------------------------------------
export function createShared(meta, terrain) {
  const { n, grid, extent, heights } = terrain;
  const half = new Uint16Array(n * n);
  for (let i = 0; i < half.length; i++) half[i] = THREE.DataUtils.toHalfFloat(heights[i]);
  const heightTex = new THREE.DataTexture(half, n, n, THREE.RedFormat, THREE.HalfFloatType);
  heightTex.minFilter = THREE.LinearFilter;
  heightTex.magFilter = THREE.LinearFilter;
  heightTex.wrapS = heightTex.wrapT = THREE.ClampToEdgeWrapping;
  heightTex.needsUpdate = true;

  const palette = [];
  for (let i = 0; i < 16; i++) palette.push(new THREE.Color(meta.types[i] ? meta.types[i].color : '#cccccc'));
  return {
    uTerrain: { value: 1 },
    uTime: { value: 0 },
    uHeightTex: { value: heightTex },
    uTerrainInfo: { value: new THREE.Vector4(extent, grid, n, 0) },
    uPalette: { value: palette },
    uHidden: { value: new Float32Array(16) },
    uFocus: { value: -1 },
    uLandcover: { value: null },
    uUrban: { value: null },
    // clear radius: full detail inside, low-detail stand-ins outside (switched per building)
    uClearCenter: { value: new THREE.Vector3() },
    uClearRadius: { value: 1e9 },
    uSunDir: { value: new THREE.Vector3(-0.55, 0.78, 0.42).normalize() },
    // colour-by-vacancy view: palette indexed by vacancy code (0 unknown, 1 occupied, 2 vacant)
    uVacancyMode: { value: 0 },
    // user edits: one texel per building (red > 0 = original hidden, deleted or replaced)
    uEdited: { value: createEditTexture(meta.buildingCount) },
    uVacColors: { value: [new THREE.Color('#E9E4DB'), new THREE.Color('#E4E6E9'), new THREE.Color('#E02424'), new THREE.Color('#1C1D21')] },
    uHiddenVac: { value: new Float32Array(4) },
    uVacant: { value: null },
  };
}

export const EDIT_TEX_W = 512;
function createEditTexture(count) {
  const h = Math.max(1, Math.ceil((count || 1) / EDIT_TEX_W));
  const tex = new THREE.DataTexture(new Uint8Array(EDIT_TEX_W * h), EDIT_TEX_W, h, THREE.RedFormat, THREE.UnsignedByteType);
  tex.minFilter = tex.magFilter = THREE.NearestFilter;
  tex.needsUpdate = true;
  return tex;
}

/** JS twin of the GLSL elevOf(): flat mode keeps the sea floor but levels land to 0.6 m. */
export function elevOfJS(shared, h) {
  const t = shared.uTerrain.value;
  return h > 0.6 ? 0.6 + (h - 0.6) * t : h;
}

const COMMON = /* glsl */ `
uniform float uTerrain;
uniform float uTime;
uniform sampler2D uHeightTex;
uniform vec4 uTerrainInfo; // extent, grid, n, -
float elevOf(float h) { return h > 0.6 ? mix(0.6, h, uTerrain) : h; }
float rawHeightAt(vec2 xz) {
  vec2 uv = ((xz + uTerrainInfo.x) / uTerrainInfo.y + 0.5) / uTerrainInfo.z;
  return texture2D(uHeightTex, uv).r;
}
float hash12(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
float vnoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash12(i), hash12(i + vec2(1.0, 0.0)), u.x),
             mix(hash12(i + vec2(0.0, 1.0)), hash12(i + vec2(1.0, 1.0)), u.x), u.y);
}
float fbm(vec2 p) {
  float s = 0.0, a = 0.5;
  for (int i = 0; i < 4; i++) { s += a * vnoise(p); p = p * 2.03 + 17.1; a *= 0.5; }
  return s;
}
vec3 srgb(vec3 c) { return pow(c, vec3(2.2)); }
`;

// Fragment-only helpers (derivatives are not available in vertex shaders).
const FRAG_COMMON = /* glsl */ `
// anti-aliased box: 1 inside |d| < halfW, smooth over one pixel footprint
float aaBox(float d, float halfW, float aa) { return 1.0 - smoothstep(halfW - aa, halfW + aa, abs(d)); }
// anti-aliased periodic lines of half-width w (in cell units) on coordinate p
float aaLines(float p, float w) {
  float aa = fwidth(p);
  float d = abs(fract(p - 0.5) - 0.5);
  return (1.0 - smoothstep(w - aa, w + aa, d)) * (1.0 - smoothstep(0.2, 0.45, aa));
}
// threshold a soft mask at 0.5 with a one-pixel anti-aliased edge (vector-sharp at any zoom)
float crisp(float m) { float w = max(fwidth(m), 1e-4); return clamp((m - 0.5) / w + 0.5, 0.0, 1.0); }
`;

// Flat-shaded meshes without a normal attribute still need a finite vertex normal: three's
// shadow-coordinate code normalises it, and normalize(vec3(0)) is NaN (= no shadows received).
const DUMMY_NORMAL = 'vec3 objectNormal = vec3(0.0, 1.0, 0.0);';

function withShared(shader, shared, keys) {
  for (const k of keys) shader.uniforms[k] = shared[k];
}

// ---------------------------------------------------------------------------
// Buildings
// ---------------------------------------------------------------------------
const BUILDING_VERT_HEAD = /* glsl */ `
attribute vec4 aUE;   // u*20 (centred facade coordinate), elevation*10, wall top*10, -
attribute vec2 aMeta; // type id, per-building seed
uniform float uHidden[16];
varying vec3 vWPos;
varying float vRelY;
varying float vU;
flat varying float vHalfLen;
varying float vWallTop;
flat varying float vType;
flat varying float vSeed;
flat varying float vVac;
uniform float uVacancyMode;
uniform float uHiddenVac[4];
`;

// Collapse a vertex to a degenerate point when its building is on the wrong side of the clear radius.
const clearSwitch = (lod) => /* glsl */ `
{
  int bid = int(floor(aBid + 0.5));
  if (bid >= 0 && texelFetch(uEdited, ivec2(bid % ${512}, bid / ${512}), 0).r > 0.5) transformed = vec3(0.0);
}
{
  vec2 bCtr = (modelMatrix * vec4(aCtr.x, 0.0, aCtr.y, 1.0)).xz;
  float bD = distance(bCtr, uClearCenter.xz);
  if (${lod ? 'bD <= uClearRadius' : 'bD > uClearRadius'}) transformed = vec3(0.0);
}
`;
const CLEAR_HEAD = /* glsl */ `
attribute vec2 aCtr;
attribute float aBid; // building index, -1 for user-added buildings
uniform sampler2D uEdited;
#ifndef VAC_DECLARED
uniform float uVacancyMode;
uniform float uHiddenVac[4];
#endif
uniform vec3 uClearCenter;
uniform float uClearRadius;
`;

const buildingBegin = (Q, lod = false) => /* glsl */ `
vec3 transformed = vec3(position);
transformed.y += elevOf(aUE.y * 0.1) / ${Q.toFixed(4)};
if (uHidden[int(aMeta.x + 0.5)] > 0.5) transformed = vec3(0.0);
if (uVacancyMode > 0.5 && uHiddenVac[int(aUE.w + 0.5)] > 0.5) transformed = vec3(0.0);
${clearSwitch(lod)}
vVac = aUE.w;
vRelY = position.y * ${Q.toFixed(4)};
vU = aUE.x * 0.05;
vHalfLen = abs(vU);
vWallTop = aUE.z * 0.1;
vType = aMeta.x;
vSeed = aMeta.y / 255.0;
vWPos = (modelMatrix * vec4(transformed, 1.0)).xyz;
`;

const buildingDepthBegin = (Q) => /* glsl */ `
vec3 transformed = vec3(position);
transformed.y += elevOf(aUE.y * 0.1) / ${Q.toFixed(4)};
if (uHidden[int(aMeta.x + 0.5)] > 0.5) transformed = vec3(0.0);
if (uVacancyMode > 0.5 && uHiddenVac[int(aUE.w + 0.5)] > 0.5) transformed = vec3(0.0);
${clearSwitch(false)}
`;

const BUILDING_FRAG_HEAD = /* glsl */ `
uniform vec3 uPalette[16];
uniform float uFocus;
varying vec3 vWPos;
varying float vRelY;
varying float vU;
flat varying float vHalfLen;
varying float vWallTop;
flat varying float vType;
flat varying float vSeed;
flat varying float vVac;
uniform float uVacancyMode;
uniform vec3 uVacColors[4];
float gWin = 0.0;
// colour key for this building: its type palette, or its parcel's vacancy class
vec3 keyColor(int t) { return uVacancyMode > 0.5 ? uVacColors[int(vVac + 0.5)] : uPalette[t]; }
float keyId() { return uVacancyMode > 0.5 ? vVac : vType; }

// Facade grid per building type: floor height, column width, window width / column, window height / floor
vec4 facadeParams(int t) {
  if (t == 0) return vec4(3.9, 1.55, 0.86, 0.74);  // tower: curtain wall
  if (t == 1) return vec4(3.8, 2.0, 0.80, 0.52);   // office: ribbon windows
  if (t == 2) return vec4(3.0, 2.7, 0.46, 0.50);   // apartment
  if (t == 3) return vec4(3.3, 2.5, 0.44, 0.60);   // flats: tall Victorian sash windows
  if (t == 4) return vec4(3.0, 3.1, 0.40, 0.48);   // house
  if (t == 5) return vec4(3.1, 2.7, 0.48, 0.52);   // mixed-use upper floors
  if (t == 6) return vec4(4.2, 3.4, 0.62, 0.48);   // retail
  if (t == 7) return vec4(3.1, 2.2, 0.52, 0.55);   // hotel
  if (t == 8) return vec4(4.4, 3.3, 0.48, 0.62);   // civic: tall windows
  if (t == 9) return vec4(3.8, 2.3, 0.62, 0.48);   // medical
  if (t == 10) return vec4(6.5, 5.0, 0.72, 0.20);  // industrial: clerestory strip
  return vec4(3.0, 3.2, 0.34, 0.40);               // other / accessory
}
`;

const BUILDING_COLOR = /* glsl */ `
vec3 wN = normalize(cross(dFdx(vWPos), dFdy(vWPos)));
float upness = abs(wN.y);
int t = int(vType + 0.5);
vec3 base = keyColor(t) * (0.9 + 0.2 * vSeed);
vec3 trim = mix(base, vec3(1.0), 0.5);
vec3 col;
if (upness > 0.6) {
  // ---------------- roofs ----------------
  float nz = fbm(vWPos.xz * 0.45 + vSeed * 40.0);
  col = mix(base * 1.12, vec3(1.0), 0.06) * (1.0 + 0.08 * (nz - 0.5));
  if (upness < 0.985) {
    // hipped roof: shingle courses follow the contour lines of the slope
    float c = vRelY * 3.0;
    col *= 0.9 * mix(1.0, 0.82, aaLines(c, 0.08));
    col *= 0.96 + 0.08 * hash12(vec2(floor(c), floor(dot(vWPos.xz, vec2(0.71, 0.7)) * 1.4)));
  } else if (t == 12) {
    // pier deck planks
    float pl = vWPos.x * 1.4;
    col *= mix(1.0, 0.86, aaLines(pl, 0.06)) * (0.95 + 0.1 * hash12(vec2(floor(pl), 3.0)));
  } else {
    // flat roof: membrane seams + a slightly lighter parapet ring look
    vec2 g = vWPos.xz / 6.0;
    float seam = max(aaLines(g.x, 0.035), aaLines(g.y, 0.035));
    col *= mix(1.0, 0.94, seam);
  }
} else {
  // ---------------- walls ----------------
  vec4 fp = facadeParams(t);
  float y = vRelY;
  float yy = y;
  bool store = (t == 5 || t == 6 || t == 7 || t == 1) && vWallTop > 6.0;
  float storeTop = 4.4;
  if (store && y < storeTop) fp = vec4(storeTop, 3.8, 0.84, 0.66);
  else if (store) yy = y - storeTop;

  float fy = yy / fp.x;
  float fx = vU / fp.y;
  float colId = floor(fx + 0.5);
  float rowId = floor(fy);
  float cx = fx - colId;
  float cy = fract(fy) - (store && y < storeTop ? 0.42 : 0.56);
  float aaX = fwidth(fx), aaY = fwidth(fy);

  float wx = aaBox(cx, fp.z * 0.5, aaX);
  float wy = aaBox(cy, fp.w * 0.5, aaY);
  float fits = step(abs(colId) * fp.y + fp.y * fp.z * 0.5, vHalfLen - 0.45);
  float winTop = (store && y < storeTop) ? storeTop : (store ? storeTop : 0.0) + (rowId + 0.56 + fp.w * 0.5) * fp.x;
  float rows = step(0.35, y) * step(winTop, vWallTop - 0.45);
  float win = wx * wy * fits * rows;
  float frame = clamp(aaBox(cx, fp.z * 0.5 + 0.05 / fp.y * 2.0, aaX) * aaBox(cy, fp.w * 0.5 + 0.12 / fp.x * 2.0, aaY) * fits * rows - win, 0.0, 1.0);

  // fade the grid to its average once a window is smaller than a couple of pixels
  float lod = 1.0 - smoothstep(0.16, 0.42, max(aaX, aaY));
  float avg = fp.z * fp.w * 0.7 * step(4.5, vWallTop);

  // per-window variation only once windows are resolvable, otherwise it turns into pixel noise
  float wr = mix(0.5, hash12(vec2(colId, rowId) + vec2(vSeed * 91.7, floor(vHalfLen * 7.0))), lod);
  vec3 glass = mix(srgb(vec3(0.30, 0.40, 0.52)), srgb(vec3(0.66, 0.78, 0.90)), 0.25 + 0.45 * wr);
  glass += srgb(vec3(0.20, 0.22, 0.25)) * smoothstep(-0.25, 0.3, cy);   // sky reflection on the upper pane
  glass = mix(glass, base * 0.85, t == 0 ? 0.45 : 0.12);                // tint so the type colour still reads
  glass = mix(glass, srgb(vec3(0.93, 0.88, 0.76)), step(0.94, wr) * 0.55); // drawn blinds
  if (store && y < storeTop) glass = mix(glass, srgb(vec3(0.95, 0.86, 0.62)), 0.18);

  float w = mix(avg, win, lod);
  col = mix(base, glass, w);
  col = mix(col, trim, frame * lod * 0.55);
  gWin = w;

  if (store) {
    // awning / sign band above the shop fronts
    float band = aaBox(y - (storeTop - 0.35), 0.28, fwidth(y));
    col = mix(col, base * 0.62, band);
  }
  // cornice and plinth
  float aaY1 = fwidth(y);
  float corn = smoothstep(vWallTop - 0.5 - aaY1, vWallTop - 0.5 + aaY1, y) * step(3.5, vWallTop) * step(float(t), 11.5);
  col = mix(col, trim, corn * 0.85);
  col *= mix(0.78, 1.0, smoothstep(0.0, 0.6, y));
  // ambient occlusion: ground contact, darker lower storeys, crisp facade corners
  col *= mix(0.6, 1.0, smoothstep(-0.3, 2.8, y));
  col *= 0.93 + 0.07 * smoothstep(0.0, 30.0, y);
  col *= mix(0.8, 1.0, smoothstep(0.0, 0.7, vHalfLen - abs(vU)));
}
if (uFocus >= 0.0 && abs(keyId() - uFocus) > 0.5) {
  float l = dot(col, vec3(0.299, 0.587, 0.114));
  col = mix(col, vec3(l) * 0.9 + 0.12, 0.85);
}
diffuseColor.rgb = col;
`;

const VAC_KEYS = ['uVacancyMode', 'uVacColors', 'uHiddenVac'];

export function createBuildingMaterials(shared, Q) {
  const mat = new THREE.MeshStandardMaterial({ roughness: 0.84, metalness: 0.0, flatShading: true });
  mat.onBeforeCompile = (shader) => {
    withShared(shader, shared, ['uTerrain', 'uTime', 'uHeightTex', 'uTerrainInfo', 'uPalette', 'uHidden', 'uFocus', 'uClearCenter', 'uClearRadius', 'uEdited', ...VAC_KEYS]);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${COMMON}\n${BUILDING_VERT_HEAD}\n#define VAC_DECLARED\n${CLEAR_HEAD}`)
      .replace('#include <beginnormal_vertex>', DUMMY_NORMAL)
      .replace('#include <begin_vertex>', buildingBegin(Q));
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${COMMON}\n${FRAG_COMMON}\n${BUILDING_FRAG_HEAD}`)
      .replace('#include <color_fragment>', BUILDING_COLOR)
      .replace('#include <roughnessmap_fragment>', '#include <roughnessmap_fragment>\nroughnessFactor = mix(roughnessFactor, 0.16, gWin);')
      .replace('#include <metalnessmap_fragment>', '#include <metalnessmap_fragment>\nmetalnessFactor = mix(metalnessFactor, 0.25, gWin);');
  };
  mat.customProgramCacheKey = () => 'sf-building';

  const depth = new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking });
  depth.onBeforeCompile = (shader) => {
    withShared(shader, shared, ['uTerrain', 'uHeightTex', 'uTerrainInfo', 'uHidden', 'uClearCenter', 'uClearRadius', 'uEdited', ...VAC_KEYS]);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${COMMON}\nattribute vec4 aUE;\nattribute vec2 aMeta;\nuniform float uHidden[16];\n${CLEAR_HEAD}`)
      .replace('#include <begin_vertex>', buildingDepthBegin(Q));
  };
  depth.customProgramCacheKey = () => 'sf-building-depth';

  // Low-detail stand-ins outside the clear radius: palette colour, roof highlight and height
  // shading only - no window grid, no noise, no shadow lookups.
  const lod = new THREE.MeshLambertMaterial({ flatShading: true });
  lod.onBeforeCompile = (shader) => {
    withShared(shader, shared, ['uTerrain', 'uHeightTex', 'uTerrainInfo', 'uPalette', 'uHidden', 'uFocus', 'uClearCenter', 'uClearRadius', 'uEdited', ...VAC_KEYS]);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${COMMON}\n${BUILDING_VERT_HEAD}\n#define VAC_DECLARED\n${CLEAR_HEAD}`)
      .replace('#include <beginnormal_vertex>', DUMMY_NORMAL)
      .replace('#include <begin_vertex>', buildingBegin(Q, true));
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${COMMON}\n${BUILDING_FRAG_HEAD}`)
      .replace('#include <color_fragment>', LOD_COLOR);
  };
  lod.customProgramCacheKey = () => 'sf-building-lod';
  return { mat, depth, lod };
}

const LOD_COLOR = /* glsl */ `
vec3 wN = normalize(cross(dFdx(vWPos), dFdy(vWPos)));
vec3 col = keyColor(int(vType + 0.5)) * (0.9 + 0.2 * vSeed);
col = abs(wN.y) > 0.6 ? mix(col * 1.12, vec3(1.0), 0.06) : col * (0.86 + 0.14 * smoothstep(0.0, 30.0, vRelY));
if (uFocus >= 0.0 && abs(keyId() - uFocus) > 0.5) col = mix(col, vec3(dot(col, vec3(0.299, 0.587, 0.114))) * 0.9 + 0.12, 0.85);
diffuseColor.rgb = col;
`;

// ---------------------------------------------------------------------------
// Terrain (heightmap displaced in the vertex shader; landcover painted procedurally)
// ---------------------------------------------------------------------------
const TERRAIN_VERT_HEAD = /* glsl */ `
varying vec3 vWPos;
varying float vH;
varying float vSlope;
`;

const TERRAIN_NORMAL = /* glsl */ `
vec2 wxz = (modelMatrix * vec4(position, 1.0)).xz;
float eStep = uTerrainInfo.y;
float hL = elevOf(rawHeightAt(wxz - vec2(eStep, 0.0)));
float hR = elevOf(rawHeightAt(wxz + vec2(eStep, 0.0)));
float hD = elevOf(rawHeightAt(wxz - vec2(0.0, eStep)));
float hU = elevOf(rawHeightAt(wxz + vec2(0.0, eStep)));
vec3 objectNormal = normalize(vec3(hL - hR, 2.0 * eStep, hD - hU));
`;

const TERRAIN_BEGIN = /* glsl */ `
vec3 transformed = vec3(position);
float hRaw = rawHeightAt(wxz);
transformed.y = elevOf(hRaw);
vH = hRaw;
vWPos = vec3(wxz.x, transformed.y, wxz.y);
vSlope = 1.0 - objectNormal.y;
`;

const TERRAIN_FRAG_HEAD = /* glsl */ `
uniform sampler2D uLandcover;
uniform sampler2D uUrban;
uniform sampler2D uVacant;
uniform float uVacancyMode;
uniform vec3 uVacColors[4];
varying vec3 vWPos;
varying float vH;
varying float vSlope;
`;

const TERRAIN_COLOR = /* glsl */ `
vec2 luv = (vWPos.xz + uTerrainInfo.x) / (2.0 * uTerrainInfo.x);
vec3 lc = texture2D(uLandcover, luv).rgb;
float urb = smoothstep(0.25, 0.75, texture2D(uUrban, luv).r);
float park = crisp(lc.r);
float forest = crisp(lc.g);
float sand = crisp(lc.b);

float n1 = fbm(vWPos.xz * 0.012);
float n2 = fbm(vWPos.xz * 0.35);
// natural ground: green lowland grading to golden summer grass on the hills
vec3 meadow = mix(srgb(vec3(0.50, 0.66, 0.32)), srgb(vec3(0.80, 0.70, 0.43)), smoothstep(40.0, 170.0, vH + n1 * 60.0));
meadow = mix(meadow, srgb(vec3(0.42, 0.50, 0.30)), smoothstep(0.12, 0.35, vSlope) * 0.6);
meadow *= 0.92 + 0.16 * n2;
// city ground between buildings: warm light pavement with a 1.5 m paver grid
vec3 urbanCol = srgb(vec3(0.84, 0.82, 0.77)) * (0.95 + 0.07 * n2);
urbanCol *= mix(1.0, 0.93, max(aaLines(vWPos.x / 1.5, 0.03), aaLines(vWPos.z / 1.5, 0.03)));
// parks: saturated lawn with mowing stripes
vec3 parkCol = srgb(vec3(0.42, 0.70, 0.29)) * (0.93 + 0.12 * n2);
parkCol *= 0.96 + 0.06 * step(0.5, fract(vWPos.x / 7.0 + 0.15 * sin(vWPos.z * 0.01)));
vec3 forestCol = srgb(vec3(0.25, 0.43, 0.21)) * (0.85 + 0.25 * fbm(vWPos.xz * 0.08));
vec3 sandCol = mix(srgb(vec3(0.86, 0.76, 0.55)), srgb(vec3(0.95, 0.88, 0.68)), smoothstep(0.6, 3.0, vH)) * (0.96 + 0.06 * n2);
vec3 seabed = mix(srgb(vec3(0.80, 0.74, 0.56)), srgb(vec3(0.42, 0.50, 0.46)), smoothstep(0.0, 12.0, -vH));
vec3 cliff = srgb(vec3(0.62, 0.56, 0.48)) * (0.9 + 0.2 * n2);

vec3 col = mix(meadow, urbanCol, urb);
col = mix(col, cliff, smoothstep(0.38, 0.6, vSlope) * (1.0 - urb));
col = mix(col, parkCol, park);
col = mix(col, forestCol, forest);
col = mix(col, sandCol, sand);
col = mix(col, seabed, 1.0 - smoothstep(-0.2, 0.7, vH));
if (uVacancyMode > 0.5) {
  // vacant lots: solid red border with a faint diagonal hatch inside, so big parcels
  // (e.g. freeway right-of-way) read as outlined ground rather than a wash of red
  float vm = texture2D(uVacant, luv).r;
  float lot = crisp(vm);
  float border = lot * (1.0 - smoothstep(0.62, 0.9, vm));
  float hatch = step(0.75, fract((vWPos.x + vWPos.z) / 6.0));
  vec3 vc = uVacColors[2];
  col = mix(col, vc, lot * (0.08 + 0.22 * hatch));
  col = mix(col, vc * 0.85, border);
}
diffuseColor.rgb = col;
`;

export function createTerrainMaterials(shared) {
  const mat = new THREE.MeshStandardMaterial({ roughness: 0.95, metalness: 0.0 });
  mat.onBeforeCompile = (shader) => {
    withShared(shader, shared, ['uTerrain', 'uTime', 'uHeightTex', 'uTerrainInfo', 'uLandcover', 'uUrban', 'uVacant', 'uVacancyMode', 'uVacColors']);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${COMMON}\n${TERRAIN_VERT_HEAD}`)
      .replace('#include <beginnormal_vertex>', TERRAIN_NORMAL)
      .replace('#include <begin_vertex>', TERRAIN_BEGIN);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${COMMON}\n${FRAG_COMMON}\n${TERRAIN_FRAG_HEAD}`)
      .replace('#include <color_fragment>', TERRAIN_COLOR);
  };
  mat.customProgramCacheKey = () => 'sf-terrain';

  const depth = new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking });
  depth.onBeforeCompile = (shader) => {
    withShared(shader, shared, ['uTerrain', 'uHeightTex', 'uTerrainInfo']);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${COMMON}`)
      .replace('#include <begin_vertex>', `vec3 transformed = vec3(position);\nvec2 wxz = (modelMatrix * vec4(position, 1.0)).xz;\ntransformed.y = elevOf(rawHeightAt(wxz));`);
  };
  depth.customProgramCacheKey = () => 'sf-terrain-depth';
  return { mat, depth };
}

// ---------------------------------------------------------------------------
// Water: bay / ocean plane (depth from the heightmap) and lakes
// ---------------------------------------------------------------------------
const WATER_VERT_HEAD = /* glsl */ `
attribute float aElev;
varying vec3 vWPos;
`;

const waterFragHead = (lake) => /* glsl */ `
varying vec3 vWPos;
uniform vec3 uSunDir;
#define IS_LAKE ${lake ? 1 : 0}
float waterHeight(vec2 p) {
  float t = uTime;
  return fbm(p * 0.09 + vec2(t * 0.05, t * 0.03)) + 0.5 * fbm(p * 0.21 - vec2(t * 0.04, -t * 0.06));
}
`;

const WATER_COLOR = /* glsl */ `
float depth;
#if IS_LAKE
  depth = 2.5;
#else
  depth = max(0.0, -rawHeightAt(vWPos.xz));
  if (abs(vWPos.x) > uTerrainInfo.x - 30.0 || abs(vWPos.z) > uTerrainInfo.x - 30.0) depth = 60.0;
#endif
vec3 shallow = srgb(vec3(0.33, 0.76, 0.80));
vec3 deep = srgb(vec3(0.07, 0.30, 0.50));
vec3 col = mix(shallow, deep, smoothstep(0.0, 22.0, depth));
#if IS_LAKE
  col = mix(srgb(vec3(0.30, 0.62, 0.62)), deep, 0.25);
#else
  // animated surf where the water meets the shore
  float nShore = fbm(vWPos.xz * 0.04);
  float surf = sin(depth * 2.6 - uTime * 1.3 + nShore * 7.0) * 0.5 + 0.5;
  float foam = (1.0 - smoothstep(0.0, 2.2, depth)) * smoothstep(0.55, 0.95, surf);
  foam = max(foam, 1.0 - smoothstep(0.05, 0.45, depth));
  col = mix(col, srgb(vec3(0.96, 0.98, 0.98)), clamp(foam, 0.0, 1.0) * 0.9);
#endif
diffuseColor.rgb = col;

// ---- sun glitter: a sparse grid of wave facets that flash where they mirror the sun at the
// camera, re-rolling a few times a second so the water twinkles. Cells never shrink below
// ~1.5 px, so the sparkle stays crisp at every zoom instead of turning into shimmer noise.
{
  float pxw = max(fwidth(vWPos.x), fwidth(vWPos.z));
  float cell = max(1.3, pxw * 1.6);
  vec2 g = vWPos.xz / cell;
  vec2 id = floor(g);
  float ph = uTime * 2.2 + hash12(id) * 9.0;
  float slot = floor(ph);
  float pick = hash12(id + vec2(slot * 3.17, slot * 1.73));
  float glint = smoothstep(0.955, 0.99, pick);
  float pulse = sin(fract(ph) * 3.14159);
  vec2 f = fract(g) - 0.5 + (vec2(hash12(id + 5.1), hash12(id + 9.7)) - 0.5) * 0.3;
  float dotShape = 1.0 - smoothstep(0.08, 0.3, length(f));
  vec3 V = normalize(cameraPosition - vWPos);
  vec3 Hs = normalize(V + uSunDir);
  float sunSide = 0.25 + 0.75 * pow(max(Hs.y, 0.0), 4.0);  // strongest toward the sun's reflection
  float g2 = glint * dotShape * pulse * sunSide;
#if !IS_LAKE
  g2 *= 1.0 - clamp(foam, 0.0, 1.0);
#endif
  totalEmissiveRadiance += vec3(1.0, 0.96, 0.86) * g2 * 3.5;
}
`;

const WATER_NORMAL = /* glsl */ `
{
  float e = 0.6;
  float px = fwidth(vWPos.x);
  float amp = 0.35 * (1.0 - smoothstep(1.5, 10.0, px));  // calm the surface only when far out
  float h0 = waterHeight(vWPos.xz);
  float hx = waterHeight(vWPos.xz + vec2(e, 0.0));
  float hz = waterHeight(vWPos.xz + vec2(0.0, e));
  vec3 wn = normalize(vec3(-(hx - h0) / e * amp * 6.0, 1.0, -(hz - h0) / e * amp * 6.0));
  normal = normalize((viewMatrix * vec4(wn, 0.0)).xyz);
}
`;

export function createWaterMaterial(shared, lake = false) {
  const mat = new THREE.MeshStandardMaterial({ roughness: lake ? 0.2 : 0.14, metalness: 0.0 });
  if (lake) {
    mat.polygonOffset = true;
    mat.polygonOffsetFactor = -2;
    mat.polygonOffsetUnits = -6;
  }
  mat.onBeforeCompile = (shader) => {
    withShared(shader, shared, ['uTerrain', 'uTime', 'uHeightTex', 'uTerrainInfo', 'uSunDir']);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${COMMON}\n${WATER_VERT_HEAD}`)
      .replace('#include <begin_vertex>', lake
        ? 'vec3 transformed = vec3(position);\ntransformed.y += elevOf(aElev);\nvWPos = (modelMatrix * vec4(transformed, 1.0)).xyz;'
        : 'vec3 transformed = vec3(position);\nvWPos = (modelMatrix * vec4(transformed, 1.0)).xyz;');
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${COMMON}\n${FRAG_COMMON}\n${waterFragHead(lake)}`)
      .replace('#include <color_fragment>', WATER_COLOR)
      .replace('#include <normal_fragment_maps>', WATER_NORMAL);
  };
  mat.customProgramCacheKey = () => (lake ? 'sf-lake' : 'sf-water');
  return mat;
}

// ---------------------------------------------------------------------------
// Roads: sidewalk / asphalt ribbons and bridge structure
// ---------------------------------------------------------------------------
const ROAD_VERT_HEAD = /* glsl */ `
attribute vec4 aRoad; // along (m), across (m, signed), elevation (m), code
varying vec3 vWPos;
varying float vAlong;
varying float vAcross;
flat varying float vHalfW;
flat varying float vCode;
`;

const ROAD_BEGIN = /* glsl */ `
vec3 transformed = vec3(position);
float rIsBridge = mod(floor(aRoad.w / 16.0), 2.0);
transformed.y += rIsBridge > 0.5 ? aRoad.z : elevOf(aRoad.z);
vAlong = aRoad.x;
vAcross = aRoad.y;
vHalfW = abs(aRoad.y);
vCode = aRoad.w;
vWPos = (modelMatrix * vec4(transformed, 1.0)).xyz;
`;

const ROAD_FRAG_HEAD = /* glsl */ `
varying vec3 vWPos;
varying float vAlong;
varying float vAcross;
flat varying float vHalfW;
flat varying float vCode;
`;

const ROAD_COLOR = /* glsl */ `
int cls = int(mod(vCode, 16.0) + 0.5);
bool isSide = vCode >= 63.5;
bool oneway = mod(floor(vCode / 32.0), 2.0) > 0.5;
float x = vAcross;
float a = vAlong;
float aaX = fwidth(x);
float aaA = fwidth(a);
float lod = 1.0 - smoothstep(0.12, 0.35, aaX);
float nz = vnoise(vWPos.xz * 0.7);
vec3 col;
if (cls == 15) {
  // bridge structure: board-formed concrete
  col = srgb(vec3(0.74, 0.71, 0.66)) * (0.9 + 0.1 * nz);
  col *= mix(1.0, 0.88, aaLines(vWPos.y / 1.2, 0.04));
} else if (isSide) {
  // sidewalk: concrete slabs with expansion joints
  col = srgb(vec3(0.88, 0.86, 0.81)) * (0.96 + 0.06 * nz);
  col *= mix(1.0, 0.86, aaLines(a / 1.6, 0.03));
  col *= mix(0.86, 1.0, smoothstep(0.0, 0.25, vHalfW - abs(x)));
} else if (cls == 7) {
  // pedestrian street / plaza: warm pavers
  col = srgb(vec3(0.80, 0.70, 0.58)) * (0.94 + 0.1 * nz);
  col *= mix(1.0, 0.85, max(aaLines(a / 0.9, 0.05), aaLines(x / 0.9, 0.05)));
} else {
  vec3 asph = cls <= 2 || cls == 8 ? srgb(vec3(0.40, 0.42, 0.46)) : (cls <= 5 ? srgb(vec3(0.47, 0.49, 0.52)) : srgb(vec3(0.55, 0.56, 0.58)));
  col = asph * (0.93 + 0.12 * nz);
  // gutter / curb strip
  float curb = smoothstep(vHalfW - 0.32 - aaX, vHalfW - 0.32 + aaX, abs(x));
  col = mix(col, srgb(vec3(0.80, 0.79, 0.75)), curb * step(4.5, vHalfW * 2.0));

  float m = 0.0;
  vec3 mc = srgb(vec3(0.97, 0.97, 0.95));
  float dash = aaBox(fract(a / 10.0) - 0.5, 0.2, aaA / 10.0);
  if (!oneway && (cls >= 1 && cls <= 3)) {
    m = aaBox(min(abs(x - 0.17), abs(x + 0.17)), 0.065, aaX);          // double yellow
    mc = srgb(vec3(0.98, 0.78, 0.22));
  } else if (!oneway && cls == 4) {
    m = aaBox(x, 0.07, aaX) * dash;                                      // dashed yellow
    mc = srgb(vec3(0.98, 0.78, 0.22));
  } else if (oneway && (cls <= 3 || cls == 8) && vHalfW > 3.0) {
    float lanes = max(1.0, floor(2.0 * vHalfW / 3.6 + 0.5));
    float laneW = 2.0 * vHalfW / lanes;
    float lx = (x + vHalfW) / laneW;
    float d = abs(fract(lx + 0.5) - 0.5) * laneW;
    m = aaBox(d, 0.075, aaX) * dash * step(0.5, lx) * step(lx, lanes - 0.5);
  }
  if (cls <= 1 || cls == 8) m = max(m, aaBox(abs(x) - (vHalfW - 0.55), 0.08, aaX));  // edge lines
  // parking-lane line on wider city streets
  if (cls >= 2 && cls <= 5 && vHalfW > 5.5) m = max(m, aaBox(abs(x) - (vHalfW - 2.5), 0.05, aaX) * 0.7);
  col = mix(col, mc, m * lod);
}
diffuseColor.rgb = col;
`;

export function createRoadMaterials(shared) {
  const make = (key, offset, side = THREE.FrontSide) => {
    // flat shading derives normals from screen-space derivatives (ribbons carry no normal attribute)
    const mat = new THREE.MeshStandardMaterial({ roughness: 0.92, metalness: 0.0, side, flatShading: true });
    if (offset) {
      mat.polygonOffset = true;
      mat.polygonOffsetFactor = offset[0];
      mat.polygonOffsetUnits = offset[1];
    }
    mat.onBeforeCompile = (shader) => {
      withShared(shader, shared, ['uTerrain', 'uTime', 'uHeightTex', 'uTerrainInfo']);
      shader.vertexShader = shader.vertexShader
        .replace('#include <common>', `#include <common>\n${COMMON}\n${ROAD_VERT_HEAD}`)
        .replace('#include <beginnormal_vertex>', DUMMY_NORMAL)
        .replace('#include <begin_vertex>', ROAD_BEGIN);
      shader.fragmentShader = shader.fragmentShader
        .replace('#include <common>', `#include <common>\n${COMMON}\n${FRAG_COMMON}\n${ROAD_FRAG_HEAD}`)
        .replace('#include <color_fragment>', ROAD_COLOR);
    };
    mat.customProgramCacheKey = () => key;
    return mat;
  };
  const depth = new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking });
  depth.onBeforeCompile = (shader) => {
    withShared(shader, shared, ['uTerrain', 'uHeightTex', 'uTerrainInfo']);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${COMMON}\nattribute vec4 aRoad;`)
      .replace('#include <begin_vertex>', 'vec3 transformed = vec3(position);\nfloat rIsBridge = mod(floor(aRoad.w / 16.0), 2.0);\ntransformed.y += rIsBridge > 0.5 ? aRoad.z : elevOf(aRoad.z);');
  };
  depth.customProgramCacheKey = () => 'sf-road-depth';
  return {
    side: make('sf-road-side', [-1, -4]),
    asphalt: make('sf-road-asphalt', [-2, -8]),
    struct: make('sf-road-struct', null, THREE.DoubleSide),
    depth,
  };
}
