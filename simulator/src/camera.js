import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

/**
 * THREE.JS DUAL-CAMERA & MULTI-ANGLE PROJECTION CONTROLLER
 * Supports:
 *  1. 'isometric' -> True Crossy Road Orthographic Isometric (35.264° elevation, 45° corner yaw, 90° Q/E rotation)
 *  2. 'topdown'   -> Top-Down 2D Orthographic Plan (90° overhead)
 *  3. 'clash'     -> Strategy 3/4 Locked Perspective (48° elevation, 36° FOV, ground-plane pan)
 *  4. 'street'    -> Low-Altitude Hero Perspective (62° wide FOV looking upward at tall structures)
 *  5. 'drone'     -> Free 3D Orbit Perspective (46° FOV, full 360° orbit + pan + zoom)
 *  6. 'ride'      -> Dynamic First/Third-Person Moving Target Follow Camera
 *
 * Adapted for a 1 unit = 1 metre city:
 *  - every distance constant from the original is multiplied by `options.worldScale` (S),
 *  - the zoom-out ceilings come from `options.maxWorldHeight` so the whole city fits on screen,
 *  - near/far planes follow the camera distance so depth precision holds from street level to city scale.
 * The projection angles, FOVs, controls mapping and tween logic are unchanged.
 */
export function createCameraAngleController(renderer, options = {}) {
  // ===========================================================================
  // 1. EXACT PROJECTION & ELEVATION ANGLE CONSTANTS
  // ===========================================================================
  const S = options.worldScale ?? 1;
  const MAX_H = options.maxWorldHeight ?? 220 * S;
  // Polar angle (phi) is measured from the +Y zenith axis:
  // acos(1 / sqrt(3)) = 54.7356° from +Y -> exact 35.264° true isometric elevation above ground
  const ISO_PHI = Math.acos(1 / Math.sqrt(3));
  const PLAN_PHI = 0.002;                          // ~0° from zenith = 90° straight overhead 2D plan
  const CLASH_PHI = THREE.MathUtils.degToRad(42);  // 42° from zenith = 48° Strategy 3/4 elevation
  const ORTHO_HALF_H = options.orthoHalfHeight ?? 65 * S;
  const ORTHO_RADIUS = options.orthoRadius ?? 190 * S;

  // 4 Hero low-angle street viewpoints cycled via rotateView90()
  // (may also be a function returning the list, so vantages can track terrain changes)
  const HERO_VANTAGES = options.heroVantages ?? [
    { pos: new THREE.Vector3(44, 4.5, -18), target: new THREE.Vector3(24, 25, -22) },
    { pos: new THREE.Vector3(29, 4.8, -44), target: new THREE.Vector3(19, 25, -34) },
    { pos: new THREE.Vector3(-56, 4.2, -65), target: new THREE.Vector3(-72, 22, -84) },
    { pos: new THREE.Vector3(42, 4.5, 5), target: new THREE.Vector3(68, 17, -12) }
  ];

  // ===========================================================================
  // 2. DUAL CAMERAS (ORTHOGRAPHIC + PERSPECTIVE) & ORBITCONTROLS
  // ===========================================================================
  const aspect = window.innerWidth / window.innerHeight;

  const perspCam = new THREE.PerspectiveCamera(46, aspect, 0.5 * S, 950 * S);
  const orthoCam = new THREE.OrthographicCamera(
    -ORTHO_HALF_H * aspect,
    ORTHO_HALF_H * aspect,
    ORTHO_HALF_H,
    -ORTHO_HALF_H,
    options.orthoNear ?? -450 * S,
    options.orthoFar ?? 950 * S
  );
  orthoCam.zoom = 1.05;
  orthoCam.updateProjectionMatrix();

  // Default to Orthographic Camera for true zero-vanishing-point Crossy Road Isometric
  let activeCamera = orthoCam;

  const controls = new OrbitControls(activeCamera, renderer.domElement);
  controls.target.copy(options.initialTarget ?? new THREE.Vector3(6, 4, -26));
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.zoomToCursor = options.zoomToCursor ?? false;

  const cameraState = {
    angle: 'isometric', // 'isometric' | 'topdown' | 'clash' | 'street' | 'drone' | 'ride'
    isoCorner: 0        // 0..3 -> 45°, 135°, 225°, 315° corner azimuths
  };

  // ===========================================================================
  // 3. SPHERICAL MATH & SMOOTH PROJECTION-PRESERVING TWEEN
  // ===========================================================================
  function sphericalToPos(target, theta, phi, radius) {
    const sinPhi = Math.sin(phi);
    return new THREE.Vector3(
      target.x + radius * sinPhi * Math.sin(theta),
      target.y + radius * Math.cos(phi),
      target.z + radius * sinPhi * Math.cos(theta)
    );
  }

  // Computes the visible world height at controls.target so Orthographic <-> Perspective
  // switches maintain identical visual framing scale without jarring zoom jumps
  function getCurrentWorldHeight() {
    if (activeCamera.isOrthographicCamera) {
      return (2 * ORTHO_HALF_H) / activeCamera.zoom;
    }
    const d = activeCamera.position.distanceTo(controls.target);
    return 2 * d * Math.tan(THREE.MathUtils.degToRad(activeCamera.fov * 0.5));
  }

  function normalizeAngleDelta(from, to) {
    let diff = ((to - from + Math.PI) % (Math.PI * 2)) - Math.PI;
    if (diff < -Math.PI) diff += Math.PI * 2;
    return from + diff;
  }

  const camTween = {
    active: false,
    progress: 0,
    duration: 0.72,
    mode: 'spherical', // 'spherical' | 'cartesian'
    startTarget: new THREE.Vector3(),
    endTarget: new THREE.Vector3(),
    startTheta: 0,
    endTheta: 0,
    startPhi: 0,
    endPhi: 0,
    startWorldH: 120,
    endWorldH: 120,
    startPos: new THREE.Vector3(),
    endPos: new THREE.Vector3(),
    startFov: 46,
    endFov: 46
  };

  // Cancel automatic tween if the user grabs/drags the camera manually
  controls.addEventListener('start', () => {
    camTween.active = false;
  });

  // ===========================================================================
  // 4. CAMERA ANGLE & PROJECTION SWITCHER
  // ===========================================================================
  function applyCameraAngle(angleId, animateTransition = true, focusAnchor = null, focusWorldH = null) {
    const prevAngle = cameraState.angle;
    cameraState.angle = angleId;

    const curWorldH = getCurrentWorldHeight();
    const curOffset = new THREE.Vector3().subVectors(activeCamera.position, controls.target);
    const curSph = new THREE.Spherical().setFromVector3(curOffset);
    const curTarget = controls.target.clone();

    if (angleId === 'isometric') {
      // CROSSY ROAD ISOMETRIC: Orthographic camera, 35.264° pitch, 45° + 90°*n yaw, left-drag pan
      activeCamera = orthoCam;
      controls.object = orthoCam;
      const isoTheta = Math.PI / 4 + cameraState.isoCorner * (Math.PI / 2);

      controls.minPolarAngle = ISO_PHI;
      controls.maxPolarAngle = ISO_PHI;
      controls.minAzimuthAngle = isoTheta;
      controls.maxAzimuthAngle = isoTheta;
      controls.enableRotate = false;
      controls.enablePan = true;
      controls.screenSpacePanning = false;
      controls.mouseButtons = { LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
      controls.touches = { ONE: THREE.TOUCH.PAN, TWO: THREE.TOUCH.DOLLY_PAN };
      controls.minZoom = (2 * ORTHO_HALF_H) / MAX_H;
      controls.maxZoom = options.maxOrthoZoom ?? 4.8;

      const destTarget = focusAnchor ? focusAnchor.clone() : curTarget;
      const destWorldH = focusAnchor ? (focusWorldH ?? 54 * S) : THREE.MathUtils.clamp(curWorldH, 32 * S, MAX_H);

      if (!animateTransition) {
        orthoCam.zoom = (2 * ORTHO_HALF_H) / destWorldH;
        orthoCam.updateProjectionMatrix();
        controls.target.copy(destTarget);
        orthoCam.position.copy(sphericalToPos(destTarget, isoTheta, ISO_PHI, ORTHO_RADIUS));
        controls.update();
      } else {
        Object.assign(camTween, {
          active: true,
          mode: 'spherical',
          progress: 0,
          duration: 0.72,
          startTheta: curSph.theta,
          endTheta: normalizeAngleDelta(curSph.theta, isoTheta),
          startPhi: curSph.phi,
          endPhi: ISO_PHI,
          startWorldH: curWorldH,
          endWorldH: destWorldH
        });
        camTween.startTarget.copy(curTarget);
        camTween.endTarget.copy(destTarget);
      }
    } else if (angleId === 'topdown') {
      // TOP-DOWN 2D PLAN: Orthographic camera straight overhead (90°)
      activeCamera = orthoCam;
      controls.object = orthoCam;
      const planTheta = cameraState.isoCorner * (Math.PI / 2);

      controls.minPolarAngle = PLAN_PHI;
      controls.maxPolarAngle = PLAN_PHI;
      controls.minAzimuthAngle = planTheta;
      controls.maxAzimuthAngle = planTheta;
      controls.enableRotate = false;
      controls.enablePan = true;
      controls.screenSpacePanning = false;
      controls.mouseButtons = { LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
      controls.touches = { ONE: THREE.TOUCH.PAN, TWO: THREE.TOUCH.DOLLY_PAN };
      controls.minZoom = (2 * ORTHO_HALF_H) / MAX_H;
      controls.maxZoom = options.maxOrthoZoom ?? 5.0;

      const destTarget = focusAnchor ? focusAnchor.clone() : new THREE.Vector3(curTarget.x, 0, curTarget.z);
      const destWorldH = focusAnchor ? (focusWorldH ?? 58 * S) : THREE.MathUtils.clamp(curWorldH, 32 * S, MAX_H);

      Object.assign(camTween, {
        active: true,
        mode: 'spherical',
        progress: 0,
        duration: 0.72,
        startTheta: curSph.theta,
        endTheta: normalizeAngleDelta(curSph.theta, planTheta),
        startPhi: curSph.phi,
        endPhi: PLAN_PHI,
        startWorldH: curWorldH,
        endWorldH: destWorldH
      });
      camTween.startTarget.copy(curTarget);
      camTween.endTarget.copy(destTarget);
    } else if (angleId === 'clash') {
      // STRATEGY 3/4 (CLASH STYLE): Locked 48° elevation, narrow 36° FOV perspective, ground pan
      activeCamera = perspCam;
      controls.object = perspCam;
      const clashTheta = Math.PI / 4 + cameraState.isoCorner * (Math.PI / 2);

      controls.minPolarAngle = CLASH_PHI;
      controls.maxPolarAngle = CLASH_PHI;
      controls.minAzimuthAngle = clashTheta;
      controls.maxAzimuthAngle = clashTheta;
      controls.enableRotate = false;
      controls.enablePan = true;
      controls.screenSpacePanning = false;
      controls.mouseButtons = { LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
      controls.touches = { ONE: THREE.TOUCH.PAN, TWO: THREE.TOUCH.DOLLY_PAN };
      controls.minDistance = 28 * S;
      controls.maxDistance = MAX_H / (2 * Math.tan(THREE.MathUtils.degToRad(18)));

      const destTarget = focusAnchor ? focusAnchor.clone() : curTarget;
      const destWorldH = focusAnchor ? (focusWorldH ?? 44 * S) : THREE.MathUtils.clamp(curWorldH, 28 * S, MAX_H);

      Object.assign(camTween, {
        active: true,
        mode: 'spherical',
        progress: 0,
        duration: 0.75,
        startTheta: curSph.theta,
        endTheta: normalizeAngleDelta(curSph.theta, clashTheta),
        startPhi: curSph.phi,
        endPhi: CLASH_PHI,
        startWorldH: curWorldH,
        endWorldH: destWorldH,
        startFov: perspCam.fov,
        endFov: 36
      });
      camTween.startTarget.copy(curTarget);
      camTween.endTarget.copy(destTarget);
    } else if (angleId === 'street') {
      // STREET / HERO ANGLE: Low-altitude 62° wide-angle perspective looking up at buildings
      activeCamera = perspCam;
      controls.object = perspCam;

      controls.minPolarAngle = 0.05;
      controls.maxPolarAngle = Math.PI * 0.65;
      controls.minAzimuthAngle = -Infinity;
      controls.maxAzimuthAngle = Infinity;
      controls.enableRotate = true;
      controls.enablePan = true;
      controls.screenSpacePanning = true;
      controls.mouseButtons = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
      controls.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN };
      controls.minDistance = 6;
      controls.maxDistance = options.streetMaxDistance ?? 190 * S;

      if (prevAngle === 'isometric' || prevAngle === 'topdown') {
        const dInit = curWorldH / (2 * Math.tan(THREE.MathUtils.degToRad(perspCam.fov * 0.5)));
        perspCam.position.copy(sphericalToPos(curTarget, curSph.theta, curSph.phi, dInit));
      }

      const vantages = typeof HERO_VANTAGES === 'function' ? HERO_VANTAGES() : HERO_VANTAGES;
      const spot = vantages[cameraState.isoCorner % vantages.length];
      Object.assign(camTween, {
        active: true,
        mode: 'cartesian',
        progress: 0,
        duration: 0.85,
        startFov: perspCam.fov,
        endFov: 62
      });
      camTween.startPos.copy(perspCam.position);
      camTween.endPos.copy(spot.pos);
      camTween.startTarget.copy(curTarget);
      camTween.endTarget.copy(spot.target);
    } else if (angleId === 'drone') {
      // FREE 3D DRONE: Full 360° orbit + pan + zoom perspective camera
      activeCamera = perspCam;
      controls.object = perspCam;

      controls.minPolarAngle = 0.02;
      controls.maxPolarAngle = Math.PI * 0.48;
      controls.minAzimuthAngle = -Infinity;
      controls.maxAzimuthAngle = Infinity;
      controls.enableRotate = true;
      controls.enablePan = true;
      controls.screenSpacePanning = false;
      controls.mouseButtons = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
      controls.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN };
      controls.minDistance = 14 * S;
      controls.maxDistance = MAX_H / (2 * Math.tan(THREE.MathUtils.degToRad(23)));

      const destTarget = focusAnchor ? focusAnchor.clone() : curTarget;
      const destWorldH = focusAnchor ? (focusWorldH ?? 42 * S) : THREE.MathUtils.clamp(curWorldH, 30 * S, MAX_H);
      const destPhi = curSph.phi < 0.2 ? 0.85 : curSph.phi;

      Object.assign(camTween, {
        active: true,
        mode: 'spherical',
        progress: 0,
        duration: 0.75,
        startTheta: curSph.theta,
        endTheta: curSph.theta,
        startPhi: curSph.phi,
        endPhi: destPhi,
        startWorldH: curWorldH,
        endWorldH: destWorldH,
        startFov: perspCam.fov,
        endFov: 46
      });
      camTween.startTarget.copy(curTarget);
      camTween.endTarget.copy(destTarget);
    } else if (angleId === 'ride') {
      // LIVE RIDE CAM: Third/first-person chase camera behind a moving vehicle
      activeCamera = perspCam;
      controls.object = perspCam;
      perspCam.fov = 56;
      perspCam.updateProjectionMatrix();

      controls.minPolarAngle = 0.05;
      controls.maxPolarAngle = Math.PI * 0.55;
      controls.minAzimuthAngle = -Infinity;
      controls.maxAzimuthAngle = Infinity;
      controls.enableRotate = true;
      controls.enablePan = false;
      camTween.active = false;
    }
  }

  // Rotate 90° Counter-Clockwise (-1, Key 'Q') or Clockwise (+1, Key 'E')
  function rotateView90(deltaStep) {
    cameraState.isoCorner = (cameraState.isoCorner + deltaStep + 4) % 4;
    if (cameraState.angle !== 'ride') {
      applyCameraAngle(cameraState.angle, true);
    }
  }

  // Keep depth precision usable from 2 m to 20 km: perspective near/far follow the orbit distance.
  function updateClipPlanes() {
    if (activeCamera !== perspCam) return;
    const d = perspCam.position.distanceTo(controls.target);
    const near = THREE.MathUtils.clamp(d * 0.012, 0.25, 60);
    const far = Math.max(d * 4 + 6000, 9000);
    if (Math.abs(perspCam.near - near) > near * 0.05 || Math.abs(perspCam.far - far) > far * 0.05) {
      perspCam.near = near;
      perspCam.far = far;
      perspCam.updateProjectionMatrix();
    }
  }

  // ===========================================================================
  // 5. PER-FRAME UPDATE & VIEWPORT RESIZE HANDLER
  // ===========================================================================
  const rideDesiredPos = new THREE.Vector3();
  const rideDesiredTarget = new THREE.Vector3();

  function update(rawDt, rideEntity = null) {
    if (cameraState.angle === 'ride' && rideEntity) {
      rideDesiredPos
        .copy(rideEntity.position)
        .addScaledVector(rideEntity.forward, -5.5)
        .add(new THREE.Vector3(0, 3.2, 0));
      rideDesiredTarget
        .copy(rideEntity.position)
        .addScaledVector(rideEntity.forward, 18)
        .add(new THREE.Vector3(0, 4.5, 0));

      perspCam.position.lerp(rideDesiredPos, 1 - Math.exp(-rawDt * 6));
      controls.target.lerp(rideDesiredTarget, 1 - Math.exp(-rawDt * 6));
    } else if (camTween.active) {
      camTween.progress = Math.min(1, camTween.progress + rawDt / camTween.duration);
      const t = camTween.progress;
      const ease = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

      controls.target.lerpVectors(camTween.startTarget, camTween.endTarget, ease);

      if (camTween.mode === 'spherical') {
        const theta = THREE.MathUtils.lerp(camTween.startTheta, camTween.endTheta, ease);
        const phi = THREE.MathUtils.lerp(camTween.startPhi, camTween.endPhi, ease);
        // Interpolate framing geometrically so long zooms (street -> whole city) feel even.
        const worldH = camTween.startWorldH * Math.pow(camTween.endWorldH / camTween.startWorldH, ease);

        if (activeCamera.isOrthographicCamera) {
          orthoCam.zoom = (2 * ORTHO_HALF_H) / worldH;
          orthoCam.updateProjectionMatrix();
          orthoCam.position.copy(sphericalToPos(controls.target, theta, phi, ORTHO_RADIUS));
        } else {
          perspCam.fov = THREE.MathUtils.lerp(camTween.startFov, camTween.endFov, ease);
          perspCam.updateProjectionMatrix();
          const d = worldH / (2 * Math.tan(THREE.MathUtils.degToRad(perspCam.fov * 0.5)));
          perspCam.position.copy(sphericalToPos(controls.target, theta, phi, d));
        }
      } else {
        perspCam.fov = THREE.MathUtils.lerp(camTween.startFov, camTween.endFov, ease);
        perspCam.updateProjectionMatrix();
        perspCam.position.lerpVectors(camTween.startPos, camTween.endPos, ease);
      }

      if (camTween.progress >= 1) camTween.active = false;
    }

    controls.update();
    updateClipPlanes();
    return activeCamera;
  }

  function resize(width = window.innerWidth, height = window.innerHeight) {
    const newAspect = width / height;
    perspCam.aspect = newAspect;
    perspCam.updateProjectionMatrix();

    orthoCam.left = -ORTHO_HALF_H * newAspect;
    orthoCam.right = ORTHO_HALF_H * newAspect;
    orthoCam.top = ORTHO_HALF_H;
    orthoCam.bottom = -ORTHO_HALF_H;
    orthoCam.updateProjectionMatrix();
  }

  // Initialize immediately in Crossy Road Isometric mode
  applyCameraAngle('isometric', false, options.initialTarget ?? null, options.initialWorldHeight ?? null);

  return {
    getActiveCamera: () => activeCamera,
    perspCam,
    orthoCam,
    controls,
    state: cameraState,
    applyCameraAngle,
    rotateView90,
    getCurrentWorldHeight,
    isTweening: () => camTween.active,
    update,
    resize
  };
}
