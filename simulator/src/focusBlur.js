import * as THREE from 'three';

/**
 * Focus blur: everything whose ground distance from the focus point exceeds the clear radius
 * is softly blurred (and hazed a touch). The scene is rendered into an MSAA half-float target
 * with a depth texture; a full-screen pass rebuilds each pixel's world position from depth,
 * blurs by distance, then applies tone mapping + sRGB output.
 */
export function createFocusBlur(renderer) {
  const size = renderer.getDrawingBufferSize(new THREE.Vector2());
  const depthTexture = new THREE.DepthTexture(size.x, size.y);
  depthTexture.type = THREE.FloatType; // 32-bit depth: clean crease detection for the outline pass
  const target = new THREE.WebGLRenderTarget(size.x, size.y, {
    type: THREE.HalfFloatType,
    samples: 4,
    depthBuffer: true,
    depthTexture,
  });

  const uniforms = {
    tColor: { value: target.texture },
    tDepth: { value: depthTexture },
    uInvProj: { value: new THREE.Matrix4() },
    uCamWorld: { value: new THREE.Matrix4() },
    uCenter: { value: new THREE.Vector3() },
    uRadius: { value: 1000 },
    uFeather: { value: 400 },
    uMaxBlur: { value: 3.5 },
    uTexel: { value: new THREE.Vector2(1 / size.x, 1 / size.y) },
    uHaze: { value: new THREE.Color(0xdbe9f3) },
    uOrtho: { value: 1 },
    uOutline: { value: 1 },   // outline opacity (faded out by the caller when zoomed far out)
    uEpsScale: { value: 1 },
    uLineW: { value: 1.5 },   // outline sample offset in pixels (~line thickness)  // metres per pixel (ortho) or radians per pixel (perspective)
    // golden-angle disc taps (unit radius), precomputed so the loop body is a single texture read -
    // Direct3D's shader compiler takes seconds on unrolled loops with per-tap maths/branches
    uTaps: { value: Array.from({ length: 16 }, (_, i) => {
      const a = i * 2.39996, r = Math.sqrt(i / 15);
      return new THREE.Vector2(Math.cos(a) * r, Math.sin(a) * r);
    }) },
  };

  const material = new THREE.ShaderMaterial({
    uniforms,
    depthTest: false,
    depthWrite: false,
    transparent: true,
    blending: THREE.NoBlending,
    vertexShader: /* glsl */ `
      varying vec2 vUv;
      void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }`,
    fragmentShader: /* glsl */ `
      uniform sampler2D tColor;
      uniform sampler2D tDepth;
      uniform mat4 uInvProj;
      uniform mat4 uCamWorld;
      uniform vec3 uCenter;
      uniform float uRadius;
      uniform float uFeather;
      uniform float uMaxBlur;
      uniform vec2 uTexel;
      uniform vec3 uHaze;
      uniform vec2 uTaps[16];
      uniform float uOrtho;
      uniform float uOutline;
      uniform float uEpsScale;
      uniform float uLineW;
      varying vec2 vUv;

      // distance-like quantity that is affine in screen space across any plane:
      // view depth for orthographic cameras, 1 / view depth for perspective ones
      float planeQ(vec2 uv) {
        float d = texture2D(tDepth, uv).x;
        vec4 v = uInvProj * vec4(uv * 2.0 - 1.0, d * 2.0 - 1.0, 1.0);
        float z = max(-v.z / v.w, 1e-3);
        return uOrtho > 0.5 ? z : 1.0 / z;
      }

      void main() {
        // ground distance of this pixel from the focus point -> blur amount
        float d = texture2D(tDepth, vUv).x;
        vec4 v = uInvProj * vec4(vUv * 2.0 - 1.0, d * 2.0 - 1.0, 1.0);
        vec3 w = (uCamWorld * vec4(v.xyz / v.w, 1.0)).xyz;
        float amt = d >= 0.999999 ? 0.0 : smoothstep(uRadius, uRadius + uFeather, length(w.xz - uCenter.xz));
        amt *= mix(1.0, 0.2, 1.0 - smoothstep(0.15, 0.45, abs(w.y)));   // keep the bay's glitter crisp
        vec2 step = uTexel * amt * uMaxBlur;
        vec4 col = vec4(0.0);
        for (int i = 0; i < 16; i++) col += texture2D(tColor, vUv + uTaps[i] * step);
        col *= 1.0 / 16.0;
        col.rgb = mix(col.rgb, uHaze * col.a, amt * 0.14);

        // ---- outlines: where the surface is not a single plane across the 3x3 neighbourhood
        // (silhouettes against ground/sky and wall/roof creases), draw a darker shade of the
        // surface colour. Second difference relative to first difference = scale-free kink test.
        if (d < 0.999999 && uOutline > 0.001) {
          vec2 ox = vec2(uTexel.x * uLineW, 0.0), oy = vec2(0.0, uTexel.y * uLineW);
          float qc = planeQ(vUv);
          float ql = planeQ(vUv - ox), qr = planeQ(vUv + ox);
          float qb = planeQ(vUv - oy), qt = planeQ(vUv + oy);
          float eps = (uOrtho > 0.5 ? 1.0 : qc) * uEpsScale * 0.05; // depth-noise tolerance
          float rx = abs(ql + qr - 2.0 * qc) / (abs(qr - ql) + eps);
          float ry = abs(qb + qt - 2.0 * qc) / (abs(qt - qb) + eps);
          float edge = smoothstep(0.22, 0.55, max(rx, ry)) * uOutline * (1.0 - 0.75 * amt);
          col.rgb = mix(col.rgb, col.rgb * 0.3, edge);
        }
        // colours are premultiplied (anti-aliased edges against the transparent sky): tone map and
        // sRGB-encode the straight colour, then premultiply again - otherwise edges get a light halo
        float a = col.a;
        gl_FragColor = vec4(col.rgb / max(a, 1e-4), 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
        gl_FragColor = vec4(gl_FragColor.rgb * a, a);
      }`,
  });

  const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material);
  quad.frustumCulled = false;
  const quadScene = new THREE.Scene();
  quadScene.add(quad);
  const quadCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

  return {
    target,
    render(scene, camera, center, radius, maxBlurPx, outline = 1, metersPerPixel = 1) {
      renderer.setRenderTarget(target);
      renderer.render(scene, camera);
      renderer.setRenderTarget(null);
      uniforms.uInvProj.value.copy(camera.projectionMatrixInverse);
      uniforms.uCamWorld.value.copy(camera.matrixWorld);
      uniforms.uCenter.value.copy(center);
      uniforms.uRadius.value = radius;
      uniforms.uFeather.value = Math.max(80, radius * 0.35);
      uniforms.uMaxBlur.value = maxBlurPx;
      uniforms.uOutline.value = outline;
      uniforms.uLineW.value = 0.8 * renderer.getPixelRatio();
      uniforms.uOrtho.value = camera.isOrthographicCamera ? 1 : 0;
      uniforms.uEpsScale.value = camera.isOrthographicCamera
        ? metersPerPixel
        : THREE.MathUtils.degToRad(camera.fov) / size.y;
      renderer.render(quadScene, quadCam);
    },
    resize() {
      renderer.getDrawingBufferSize(size);
      target.setSize(size.x, size.y);
      uniforms.uTexel.value.set(1 / size.x, 1 / size.y);
    },
  };
}
