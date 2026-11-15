import * as THREE from 'three';
import './style.css';
import { createCameraAngleController } from './camera.js';
import { loadCityData, makeHeightSampler, makeProjection } from './data.js';
import {
  createShared, createBuildingMaterials, createTerrainMaterials, createWaterMaterial, createRoadMaterials, elevOfJS,
} from './materials.js';
import { buildTerrain, buildWater, buildBuildingMeshes, buildRoadMeshes, buildLakes, buildTrees, buildLandmarks } from './world.js';
import { createUI, MODES } from './ui.js';
import { createFocusBlur } from './focusBlur.js';
import { createBuildingIndex, pickBuilding, buildingParts, createSelectionHighlight } from './picking.js';
import GeometryWorker from './geometryWorker.js?worker';

const CHUNK = 1000;      // metres per mesh chunk (frustum-culling granularity)
const WORLD_SCALE = 5;   // camera controller distance multiplier (original rig was built for ~1/5 scale)

const ui = createUI(document.getElementById('ui'));

// ---------------------------------------------------------------------------
// Renderer, scene, lighting
// ---------------------------------------------------------------------------
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.NeutralToneMapping; // keeps the type colours saturated
renderer.toneMappingExposure = 1.0;
renderer.setClearColor(0x000000, 0);
document.getElementById('app').appendChild(renderer.domElement);

const scene = new THREE.Scene();

// Sky-gradient environment so glass and water pick up believable reflections.
function skyEnvironment() {
  const envScene = new THREE.Scene();
  const mat = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    vertexShader: 'varying vec3 vDir; void main(){ vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
    fragmentShader: `varying vec3 vDir;
      void main(){
        float y = vDir.y;
        vec3 zenith = vec3(0.23, 0.48, 0.80), horizon = vec3(0.80, 0.89, 0.95), ground = vec3(0.42, 0.42, 0.38);
        vec3 c = y > 0.0 ? mix(horizon, zenith, pow(y, 0.6)) : mix(horizon, ground, pow(-y, 0.5));
        c += vec3(1.0, 0.9, 0.7) * pow(max(dot(vDir, normalize(vec3(-0.55, 0.78, 0.42))), 0.0), 64.0) * 4.0;
        gl_FragColor = vec4(c, 1.0);
      }`,
  });
  envScene.add(new THREE.Mesh(new THREE.SphereGeometry(10, 48, 24), mat));
  const pmrem = new THREE.PMREMGenerator(renderer);
  const tex = pmrem.fromScene(envScene, 0.02).texture;
  pmrem.dispose();
  return tex;
}
scene.environment = skyEnvironment();
scene.environmentIntensity = 0.35;

const hemi = new THREE.HemisphereLight(0xd8e9ff, 0xbfae8c, 0.9);
scene.add(hemi);
const SUN_DIR = new THREE.Vector3(-0.55, 0.78, 0.42).normalize(); // afternoon sun from the south-west
const sun = new THREE.DirectionalLight(0xfff0d6, 3.4);
sun.castShadow = true;
const maxTex = renderer.capabilities.maxTextureSize;
sun.shadow.mapSize.set(Math.min(4096, maxTex), Math.min(4096, maxTex));
sun.shadow.bias = -0.00025;
sun.shadow.normalBias = 0;
scene.add(sun, sun.target);

const sunBasis = new THREE.Matrix4().lookAt(SUN_DIR, new THREE.Vector3(), new THREE.Vector3(0, 1, 0));
const sunX = new THREE.Vector3().setFromMatrixColumn(sunBasis, 0);
const sunY = new THREE.Vector3().setFromMatrixColumn(sunBasis, 1);
let shadowRadius = 0;
function updateSun(center, radius) {
  const r = THREE.MathUtils.clamp(radius, 120, 9000);
  if (Math.abs(r - shadowRadius) > shadowRadius * 0.08) {
    shadowRadius = r;
    const c = sun.shadow.camera;
    c.left = -r; c.right = r; c.top = r; c.bottom = -r;
    c.near = 1;
    c.far = 2 * (r + 2500);
    c.updateProjectionMatrix();
  }
  // snap to the shadow texel grid so edges do not shimmer while panning
  const texel = (2 * shadowRadius) / sun.shadow.mapSize.x;
  const lx = center.dot(sunX), ly = center.dot(sunY);
  const p = center.clone()
    .addScaledVector(sunX, Math.round(lx / texel) * texel - lx)
    .addScaledVector(sunY, Math.round(ly / texel) * texel - ly);
  sun.target.position.copy(p);
  sun.position.copy(p).addScaledVector(SUN_DIR, shadowRadius + 2500);
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
async function boot() {
  const data = await loadCityData((p) => ui.setProgress(`Downloading city data · ${Math.round(p * 100)}%`, p * 0.45));
  const { meta, terrain } = data;
  const proj = makeProjection(meta);
  const sampleGround = makeHeightSampler(terrain);
  const shared = createShared(meta, terrain);
  shared.uSunDir.value.copy(SUN_DIR);

  // geometry generation runs in a worker while we build terrain/water/trees here
  // Two workers in parallel: detailed buildings + roads, and the low-detail stand-ins.
  const runWorker = (part, msg, transfer) => {
    const worker = new GeometryWorker();
    return new Promise((resolve, reject) => {
      worker.onmessage = (e) => {
        const m = e.data;
        if (m.type === 'progress' && part === 'full') {
          const base = m.stage === 'buildings' ? 0.45 : 0.85;
          const span = m.stage === 'buildings' ? 0.4 : 0.1;
          ui.setProgress(`Generating ${m.stage} · ${Math.round(m.p * 100)}%`, base + span * m.p);
        } else if (m.type === 'done') { worker.terminate(); resolve(m); }
        else if (m.type === 'error') reject(new Error(m.message));
      };
      worker.onerror = (e) => reject(new Error(e.message));
      worker.postMessage({ ...msg, part }, transfer);
    });
  };
  // Building index for click picking (built before the buffer is transferred to the worker).
  const buildingIndex = createBuildingIndex(data.buildingsBuf.slice(0), terrain.extent);
  const terrainMsg = { n: terrain.n, grid: terrain.grid, extent: terrain.extent, heights: terrain.heights };
  const lodPromise = runWorker('lod', {
    buildingsBuf: data.buildingsBuf.slice(0), terrain: terrainMsg, chunkSize: CHUNK, vacancy: data.vacancy,
  });
  const fullPromise = runWorker('full', {
    buildingsBuf: data.buildingsBuf, roadsBuf: data.roadsBuf, terrain: terrainMsg, chunkSize: CHUNK, vacancy: data.vacancy,
  }, [data.buildingsBuf, data.roadsBuf]);
  const geomPromise = Promise.all([fullPromise, lodPromise]).then(([f, l]) => ({
    buildings: { full: f.buildings.full, lod: l.buildings.lod },
    roads: f.roads,
  }));

  const texLoader = new THREE.TextureLoader();
  const loadTex = (url) => new Promise((res, rej) => texLoader.load(url, res, undefined, rej));
  const [landcover, urban, vacantLots] = await Promise.all([
    loadTex(`${import.meta.env.BASE_URL}data/landcover.png`),
    loadTex(`${import.meta.env.BASE_URL}data/urban.png`),
    loadTex(`${import.meta.env.BASE_URL}data/vacant.png`),
  ]);
  for (const t of [landcover, urban, vacantLots]) {
    t.flipY = false;
    t.colorSpace = THREE.NoColorSpace; // masks, not colours
    t.anisotropy = renderer.capabilities.getMaxAnisotropy();
    t.minFilter = THREE.LinearMipmapLinearFilter;
    t.needsUpdate = true;
  }
  shared.uLandcover.value = landcover;
  shared.uUrban.value = urban;
  shared.uVacant.value = vacantLots;

  scene.add(buildTerrain(terrain, createTerrainMaterials(shared)));
  scene.add(buildWater(createWaterMaterial(shared, false)));
  scene.add(buildLakes(data.lakesBuf, createWaterMaterial(shared, true)));
  const trees = buildTrees(data.treesBuf, shared, CHUNK, terrain.extent);
  scene.add(trees.group);
  const landmarks = buildLandmarks(proj, sampleGround, shared);
  scene.add(landmarks.group);

  const geom = await geomPromise;
  ui.setProgress('Uploading to GPU', 0.97);
  const buildingMats = createBuildingMaterials(shared, geom.buildings.full[0]?.Q ?? 0.25);
  const buildings = buildBuildingMeshes(geom.buildings, buildingMats, CHUNK);
  const roads = buildRoadMeshes(geom.roads, createRoadMaterials(shared));
  scene.add(buildings.group, buildings.lodGroup, roads.group);
  const blur = createFocusBlur(renderer);
  const highlight = createSelectionHighlight(scene, renderer);

  // -------------------------------------------------------------------------
  // Camera rig
  // -------------------------------------------------------------------------
  const ground = (lon, lat, up = 0) => {
    const p = proj.toWorld(lon, lat);
    return new THREE.Vector3(p.x, elevOfJS(shared, sampleGround(p.x, p.z)) + up, p.z);
  };
  const hero = () => [
    { pos: ground(-122.3984, 37.7887, 1.8), target: ground(-122.39695, 37.78977, 260) }, // Salesforce Tower from Mission & 1st
    { pos: ground(-122.4049, 37.7979, 1.8), target: ground(-122.40276, 37.79518, 170) }, // Transamerica from Columbus Ave
    { pos: ground(-122.4352, 37.7766, 2.0), target: ground(-122.4010, 37.7905, 140) },   // Alamo Square to downtown
    { pos: ground(-122.4664, 37.8052, 2.0), target: new THREE.Vector3(...(() => { const p = proj.toWorld(-122.47785, 37.81402); return [p.x, 150, p.z]; })()) }, // Golden Gate from Crissy Field
  ];
  const start = ground(-122.3990, 37.7905);
  const START_WORLD_H = 1100;
  const cam = createCameraAngleController(renderer, {
    worldScale: WORLD_SCALE,
    maxWorldHeight: 19000,
    orthoRadius: 6000,
    orthoNear: -30000,
    orthoFar: 60000,
    maxOrthoZoom: 9,
    initialTarget: start,
    initialWorldHeight: 1500,
    heroVantages: hero,
    streetMaxDistance: 4000,
    zoomToCursor: true,
  });

  const neighborhoods = [
    ['Financial District', -122.3999, 37.7946, 1100],
    ['SoMa & Salesforce Tower', -122.3985, 37.7865, 1300],
    ['Chinatown & North Beach', -122.4078, 37.7980, 1000],
    ['Fisherman\'s Wharf', -122.4177, 37.8080, 1100],
    ['Golden Gate Bridge', -122.4783, 37.8150, 2200],
    ['Presidio', -122.4662, 37.7989, 2200],
    ['Pacific Heights & Marina', -122.4360, 37.7960, 1400],
    ['Alamo Square & Painted Ladies', -122.4346, 37.7765, 700],
    ['Haight-Ashbury', -122.4469, 37.7692, 900],
    ['Golden Gate Park', -122.4862, 37.7694, 3000],
    ['Twin Peaks & Sutro Tower', -122.4500, 37.7530, 1800],
    ['Castro', -122.4350, 37.7609, 900],
    ['Mission District', -122.4148, 37.7599, 1300],
    ['Outer Sunset & Ocean Beach', -122.5000, 37.7550, 2200],
    ['Richmond District', -122.4750, 37.7800, 2000],
    ['Mission Bay & Oracle Park', -122.3920, 37.7740, 1500],
    ['Bayview & Hunters Point', -122.3800, 37.7300, 2400],
    ['Treasure Island', -122.3706, 37.8235, 2400],
    ['Whole city', -122.4400, 37.7650, 15500],
  ].map(([name, lon, lat, h]) => ({ name, lon, lat, h }));

  // -------------------------------------------------------------------------
  // HUD wiring
  // -------------------------------------------------------------------------
  // Default view = the strategy-game 3/4 camera ('clash'), snapped into place without a fly-in.
  cam.applyCameraAngle('clash', true, start, START_WORLD_H);
  cam.update(10);
  let currentMode = 'clash';
  let treesOn = true;
  // Clear radius: full detail + shadows + trees inside, blurred low-detail city outside.
  let clearOn = true;
  let clearRadius = 1200;
  let outlinesOn = true;
  // Compile every material for the off-screen (linear, un-tonemapped) target up front, in
  // parallel where the browser supports it, instead of stalling the first frame.
  ui.setProgress('Compiling shaders', 0.985);
  renderer.setRenderTarget(blur.target);
  await renderer.compileAsync(scene, cam.getActiveCamera());
  renderer.setRenderTarget(null);

  const hud = ui.ready({
    meta,
    stats: { buildings: meta.buildingCount, tris: buildings.tris + roads.tris, trees: trees.count },
    neighborhoods,
    onMode: setMode,
    onRotate: (d) => cam.rotateView90(d),
    onJump: (n) => {
      const mode = currentMode === 'street' ? 'clash' : currentMode;
      currentMode = mode;
      hud.setMode(mode);
      cam.applyCameraAngle(mode, true, ground(n.lon, n.lat), n.h);
    },
    onToggleType: (i, off) => {
      shared.uHidden.value[i] = off ? 1 : 0;
      if (off && selected >= 0 && buildingIndex.type[selected] === i) select(-1);
    },
    onFocusType: (i) => { shared.uFocus.value = i; },
    onShowAll: () => shared.uHidden.value.fill(0),
    onTerrain: (on) => {
      shared.uTerrain.value = on ? 1 : 0;
      trees.place();
      landmarks.place();
      if (selected >= 0) select(selected); // base heights change with the hills toggle
      const t = cam.controls.target;
      const dy = elevOfJS(shared, sampleGround(t.x, t.z)) - t.y;
      t.y += dy;
      cam.getActiveCamera().position.y += dy;
      if (currentMode === 'street') setMode('street');
    },
    onTrees: (on) => { treesOn = on; },
    onClear: (on) => { clearOn = on; },
    onRadius: (m) => { clearRadius = m; },
    onOutlines: (on) => { outlinesOn = on; },
    onColorBy: (mode) => { shared.uVacancyMode.value = mode === 'vacancy' ? 1 : 0; shared.uFocus.value = -1; },
    onToggleVac: (code, off) => { shared.uHiddenVac.value[code] = off ? 1 : 0; },
    onFocusVac: (code) => { shared.uFocus.value = code; },
    radius: clearRadius,
  });
  hud.setMode('clash');
  {
    const gl = renderer.getContext();
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    const name = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : '';
    const m = /(NVIDIA[^(,]*|AMD[^(,]*|Radeon[^(,]*|Intel[^(,]*|Apple[^(,]*)/.exec(name);
    if (m) hud.setGpu(m[1].replace(/\s*\(0x[0-9A-F]+\)/i, '').trim());
  }

  // -------------------------------------------------------------------------
  // Click a building: yellow glow + info card (type and lot status, never location)
  // -------------------------------------------------------------------------
  let selected = -1;
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  const groundAt = (x, z) => elevOfJS(shared, sampleGround(x, z));
  // in the vacancy view, classes hidden from the legend are not clickable either
  buildingIndex.skip = (b) => shared.uVacancyMode.value === 1 && data.vacancy && shared.uHiddenVac.value[data.vacancy[b]] > 0.5;
  function select(b) {
    selected = b;
    if (b < 0) {
      highlight.clear();
      hud.hideBuilding();
      return;
    }
    highlight.show(buildingParts(buildingIndex, b, shared));
    hud.showBuilding({
      type: buildingIndex.type[b],
      lot: data.vacancy ? data.vacancy[b] : null,
    }, () => select(-1));
  }
  const canvas = renderer.domElement;
  let down = null;
  canvas.addEventListener('pointerdown', (e) => {
    down = e.button === 0 ? { x: e.clientX, y: e.clientY, t: performance.now() } : null;
  });
  canvas.addEventListener('pointerup', (e) => {
    if (!down || e.button !== 0) return;
    const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
    const quick = performance.now() - down.t < 600;
    down = null;
    if (moved > 5 || !quick) return; // a drag pans the camera; only treat taps as clicks
    const rect = canvas.getBoundingClientRect();
    ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(ndc, cam.getActiveCamera());
    select(pickBuilding(buildingIndex, raycaster.ray, shared, groundAt));
  });
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && selected >= 0) select(-1); });

  function setMode(id) {
    currentMode = id;
    hud.setMode(id);
    cam.applyCameraAngle(id, true);
  }

  // -------------------------------------------------------------------------
  // Keyboard: modes, rotation, WASD ground panning
  // -------------------------------------------------------------------------
  const keys = new Set();
  window.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'SELECT' || e.target.tagName === 'INPUT') return;
    const k = e.key.toLowerCase();
    if (k === 'q') cam.rotateView90(-1);
    else if (k === 'e') cam.rotateView90(1);
    else if (k === 't') hud.toggleTerrain();
    else if (k === 'g') hud.toggleTrees();
    else if (k === 'f') hud.toggleFocus();
    else if (k === 'o') hud.toggleOutlines();
    else if (k === 'v') hud.toggleVacancy();
    else if (['1', '2', '3', '4', '5'].includes(k)) setMode(MODES[Number(k) - 1].id);
    keys.add(k);
  });
  window.addEventListener('keyup', (e) => keys.delete(e.key.toLowerCase()));
  window.addEventListener('blur', () => keys.clear());

  window.addEventListener('resize', () => {
    renderer.setSize(window.innerWidth, window.innerHeight);
    cam.resize();
    blur.resize();
  });

  // -------------------------------------------------------------------------
  // Frame loop
  // -------------------------------------------------------------------------
  // Adaptive resolution: trade pixel density for frame rate on weaker GPUs.
  const maxRatio = Math.min(window.devicePixelRatio, 2);
  let ratio = maxRatio, acc = 0, frames = 0;
  function adaptResolution(dt) {
    acc += dt;
    frames++;
    if (acc < 1.0) return;
    const ms = (acc / frames) * 1000;
    acc = 0;
    frames = 0;
    let next = ratio;
    if (ms > 30) next = Math.max(0.75, ratio - 0.25);
    else if (ms < 15) next = Math.min(maxRatio, ratio + 0.25);
    if (next !== ratio) {
      ratio = next;
      renderer.setPixelRatio(ratio);
      renderer.setSize(window.innerWidth, window.innerHeight);
      blur.resize();
    }
  }

  const clock = new THREE.Clock();
  const clearCenter = new THREE.Vector3();
  const fwd = new THREE.Vector3(), right = new THREE.Vector3(), move = new THREE.Vector3();
  const LIMIT = terrain.extent - 1200;
  function frame() {
    const dt = Math.min(clock.getDelta(), 0.1);
    shared.uTime.value += dt;
    const camera = cam.getActiveCamera();
    const worldH = cam.getCurrentWorldHeight();

    // WASD / arrow keys pan along the ground relative to the view
    move.set(0, 0, 0);
    if (keys.has('w') || keys.has('arrowup')) move.z -= 1;
    if (keys.has('s') || keys.has('arrowdown')) move.z += 1;
    if (keys.has('a') || keys.has('arrowleft')) move.x -= 1;
    if (keys.has('d') || keys.has('arrowright')) move.x += 1;
    if (move.lengthSq() > 0) {
      camera.getWorldDirection(fwd);
      fwd.y = 0;
      if (fwd.lengthSq() < 1e-6) fwd.set(0, 0, -1).applyQuaternion(camera.quaternion).setY(0);
      fwd.normalize();
      right.crossVectors(fwd, camera.up).normalize();
      const speed = Math.max(40, worldH * 0.7) * dt;
      const delta = new THREE.Vector3().addScaledVector(fwd, -move.z * speed).addScaledVector(right, move.x * speed);
      cam.controls.target.add(delta);
      camera.position.add(delta);
    }

    cam.update(dt);

    // keep the view over the city
    const t = cam.controls.target;
    const cx = THREE.MathUtils.clamp(t.x, -LIMIT, LIMIT) - t.x;
    const cz = THREE.MathUtils.clamp(t.z, -LIMIT, LIMIT) - t.z;
    if (cx || cz) {
      t.x += cx; t.z += cz;
      camera.position.x += cx; camera.position.z += cz;
    }

    // ---- clear radius: which chunks need full detail, which need stand-ins
    const focusPt = currentMode === 'street' ? clearCenter.set(camera.position.x, t.y, camera.position.z) : clearCenter.copy(t);
    const R = clearOn ? clearRadius : 1e9;
    shared.uClearCenter.value.copy(focusPt);
    shared.uClearRadius.value = R;
    const H = CHUNK / 2;
    for (const c of buildings.chunks) {
      const ax = Math.abs(focusPt.x - c.x), az = Math.abs(focusPt.z - c.z);
      const near = Math.hypot(Math.max(ax - H, 0), Math.max(az - H, 0)); // closest point of the chunk
      const far = Math.hypot(ax + H, az + H);                             // farthest corner
      if (c.full) c.full.visible = near <= R;
      if (c.lod) c.lod.visible = far > R;
    }
    trees.group.visible = treesOn && worldH < 6500;
    for (const im of trees.meshes) {
      const ax = Math.abs(focusPt.x - im.userData.center.x), az = Math.abs(focusPt.z - im.userData.center.y);
      im.visible = Math.hypot(Math.max(ax - H, 0), Math.max(az - H, 0)) <= R;
    }

    // shadows follow the part of the city in view (and never extend past the clear radius);
    // once buildings are a few pixels tall the shadow pass is pointless, so shrink it to nothing
    // (cheaper than toggling castShadow, which would recompile every shader)
    const aspect = window.innerWidth / window.innerHeight;
    if (worldH > 7000 && currentMode !== 'street' && !clearOn) {
      updateSun(new THREE.Vector3(t.x, -5000, t.z), 1);
    } else if (currentMode === 'street') {
      const mid = camera.position.clone().lerp(t, 0.5);
      updateSun(clearOn ? focusPt : mid, Math.min(R * 1.1, Math.max(250, camera.position.distanceTo(t) * 0.9)));
    } else {
      const persp = !camera.isOrthographicCamera;
      updateSun(t, Math.min(R * 1.1, worldH * (persp ? 1.0 : 0.62) * Math.max(1, aspect)));
    }
    // always through the blur pass (radius = infinity when Focus is off) so only one set of
    // shader variants exists - switching render targets would recompile every material
    // outlines stay crisp until buildings get tiny on screen, then fade out (they would turn to mush)
    const mpp = worldH / (window.innerHeight * ratio);
    const outline = outlinesOn ? 1 - THREE.MathUtils.smoothstep(mpp, 2.5, 7) : 0;
    if (selected >= 0) {
      highlight.update(shared.uTime.value);
      const p = highlight.center.clone().project(camera);
      const onScreen = p.z > -1 && p.z < 1 && Math.abs(p.x) < 1.2 && Math.abs(p.y) < 1.2;
      hud.placeBuilding((p.x + 1) / 2 * window.innerWidth, (1 - p.y) / 2 * window.innerHeight, onScreen);
    }
    blur.render(scene, camera, focusPt, R, clearOn ? 3.2 * ratio : 0, outline, mpp);
    highlight.render(camera);
    adaptResolution(dt);
    requestAnimationFrame(frame);
  }
  frame();
  // console / automation hook
  window.__sf = {
    scene, cam, shared, renderer, setMode,
    select, buildingIndex,
    jump(lon, lat, worldH, mode = currentMode) {
      currentMode = mode;
      hud.setMode(mode);
      cam.applyCameraAngle(mode, true, ground(lon, lat), worldH);
    },
  };
}

boot().catch((err) => {
  console.error(err);
  ui.fail(`Could not load the city data.\n${err.message}\nRun "npm run data" first, then reload.`);
});
