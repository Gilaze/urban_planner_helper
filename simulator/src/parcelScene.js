import * as THREE from 'three';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { polygons } from './parcelGeometry.js';
import { SERVICE_COLORS } from './planningStyle.js';

export function createParcelScene(scene, projection, groundAt, terrain) {
  const group = new THREE.Group();
  group.name = 'parcel-scenario';
  scene.add(group);
  const areaGroup = new THREE.Group();
  areaGroup.name = 'tract-site-planning';
  scene.add(areaGroup);
  const areaMaterial = new THREE.LineBasicMaterial({ color: 0x5b9eab, transparent: true, opacity: 0.5 });
  const selectedAreaMaterial = new LineMaterial({ color: 0x1249dc, linewidth: 9, depthTest: false });
  const selectedAreaHalo = new LineMaterial({ color: 0xffffff, linewidth: 15, depthTest: false });
  selectedAreaMaterial.resolution.set(window.innerWidth, window.innerHeight);
  selectedAreaHalo.resolution.set(window.innerWidth, window.innerHeight);
  const selectedSiteMaterial = new THREE.LineBasicMaterial({ color: 0x1649c7 });
  const siteMaterial = new THREE.LineBasicMaterial({ color: 0x009950 });
  const rankMarkers = [];
  const arrowMarkers = [];
  const outlineMaterial = new THREE.LineBasicMaterial({ color: 0x155fd2 });
  const volumeMaterial = new THREE.MeshStandardMaterial({ color: 0x2279ed, roughness: 0.75 });
  const roofMaterial = new THREE.LineBasicMaterial({ color: 0xc6e2ff });
  const maxHeight = terrain.heights.reduce((max, height) => Math.max(max, height), 1) + 1;
  const bound = new THREE.Box3(
    new THREE.Vector3(-terrain.extent, -1, -terrain.extent),
    new THREE.Vector3(terrain.extent, maxHeight, terrain.extent),
  );

  function drawOutline(rings, material, elevation, destination = group) {
    for (const ring of rings) {
      const points = ring.slice(0, -1).map(([lon, lat]) => {
        const { x, z } = projection.toWorld(lon, lat);
        return new THREE.Vector3(x, elevation(x, z), z);
      });
      if (material.isLineMaterial) {
        const closed = [...points, points[0]].flatMap((p) => [p.x, p.y, p.z]);
        const line = new Line2(new LineGeometry().setPositions(closed), material);
        line.renderOrder = material === selectedAreaHalo ? 900 : 901;
        destination.add(line);
      } else destination.add(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(points), material));
    }
  }

  return {
    drawAreas(areas, selectedId, designs, selectedUse) {
      rankMarkers.length = 0;
      arrowMarkers.length = 0;
      for (const child of [...areaGroup.children]) {
        areaGroup.remove(child);
        if (child.userData.rankMarker || child.userData.siteArrow) {
          child.material.map.dispose();
          child.material.dispose();
        } else child.geometry?.dispose();
      }
      for (const area of areas) {
        for (const rings of polygons(area.geometry)) {
          if (area.id === selectedId) drawOutline(rings, selectedAreaHalo, (x, z) => groundAt(x, z) + 0.7, areaGroup);
          drawOutline(rings, area.id === selectedId ? selectedAreaMaterial : areaMaterial,
            (x, z) => groundAt(x, z) + 0.7, areaGroup);
        }
      }
      const sharedLocations = new Map();
      for (const design of designs) {
        const selected = design.category_id === selectedUse;
        for (const rings of polygons(design.geometry)) {
          drawOutline(rings, selected ? selectedSiteMaterial : siteMaterial, (x, z) => groundAt(x, z) + 1, areaGroup);
        }
        const ring = polygons(design.geometry)[0]?.[0]?.slice(0, -1);
        if (ring?.length) {
          const lon = ring.reduce((sum, p) => sum + p[0], 0) / ring.length;
          const lat = ring.reduce((sum, p) => sum + p[1], 0) / ring.length;
          const { x, z } = projection.toWorld(lon, lat);
          const canvas = document.createElement('canvas');
          canvas.width = canvas.height = 128;
          const context = canvas.getContext('2d');
          context.shadowColor = '#10233b'; context.shadowBlur = 5; context.shadowOffsetY = 3;
          const color = SERVICE_COLORS[design.category_id];
          context.fillStyle = color;
          context.beginPath();
          context.moveTo(64, 121); context.bezierCurveTo(48, 99, 14, 79, 14, 52);
          context.arc(64, 52, 50, Math.PI, 0);
          context.bezierCurveTo(114, 79, 80, 99, 64, 121); context.closePath();
          context.fill(); context.shadowBlur = 0; context.shadowOffsetY = 0;
          context.strokeStyle = '#fff'; context.lineWidth = 8; context.stroke();
          context.fillStyle = '#fff'; context.font = 'bold 66px sans-serif';
          context.textAlign = 'center'; context.textBaseline = 'middle'; context.fillText(String(design.rank), 64, 54);
          const texture = new THREE.CanvasTexture(canvas);
          const marker = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false, depthWrite: false }));
          marker.position.set(x, groundAt(x, z) + 2, z);
          const stack = sharedLocations.get(design.parcel_id) || 0;
          sharedLocations.set(design.parcel_id, stack + 1);
          marker.center.set(.5, -stack * 1.15);
          marker.renderOrder = 1000;
          marker.userData = { rankMarker: true, parcelId: design.parcel_id, categoryId: design.category_id, rank: design.rank, selected };
          rankMarkers.push(marker);
          areaGroup.add(marker);
          const arrowCanvas = document.createElement('canvas');
          arrowCanvas.width = 96; arrowCanvas.height = 128;
          const arrowContext = arrowCanvas.getContext('2d');
          arrowContext.fillStyle = color; arrowContext.strokeStyle = '#fff'; arrowContext.lineWidth = 8;
          arrowContext.shadowColor = '#10233b'; arrowContext.shadowBlur = 4; arrowContext.shadowOffsetY = 2;
          arrowContext.beginPath(); arrowContext.moveTo(34, 10); arrowContext.lineTo(62, 10);
          arrowContext.lineTo(62, 66); arrowContext.lineTo(84, 66); arrowContext.lineTo(48, 116);
          arrowContext.lineTo(12, 66); arrowContext.lineTo(34, 66); arrowContext.closePath();
          arrowContext.fill(); arrowContext.stroke();
          const arrow = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(arrowCanvas), depthTest: false, depthWrite: false }));
          arrow.position.copy(marker.position);
          arrow.renderOrder = 1001;
          arrow.userData = { siteArrow: true, categoryId: design.category_id, rank: design.rank, stack, selected };
          arrowMarkers.push(arrow);
          areaGroup.add(arrow);
        }
      }
    },
    updateLabels(camera, viewportHeight) {
      selectedAreaMaterial.resolution.set(window.innerWidth, viewportHeight);
      selectedAreaHalo.resolution.set(window.innerWidth, viewportHeight);
      for (const marker of rankMarkers) {
        const height = camera.isOrthographicCamera ? (camera.top-camera.bottom)/camera.zoom
          : 2 * camera.position.distanceTo(marker.position) * Math.tan(camera.fov * Math.PI / 360);
        const size = height * (marker.userData.selected ? 54 : 46) / viewportHeight;
        marker.scale.set(size, size, 1);
      }
      for (const arrow of arrowMarkers) {
        const height = camera.isOrthographicCamera ? (camera.top-camera.bottom)/camera.zoom
          : 2 * camera.position.distanceTo(arrow.position) * Math.tan(camera.fov * Math.PI / 360);
        const pixel = height / viewportHeight;
        arrow.scale.set(pixel * 32, pixel * 44, 1);
        const pinHeight = arrow.userData.selected ? 54 : 46;
        const bounce = 5 + 7 * (1 + Math.sin(performance.now() * .0035 + arrow.userData.rank * .7));
        arrow.center.set(.5, -(pinHeight + arrow.userData.stack * 54 + bounce) / 44);
      }
    },
    draw(parcel, massing) {
      for (const child of [...group.children]) {
        group.remove(child);
        child.geometry.dispose();
      }
      for (const rings of polygons(parcel?.geometry)) {
        drawOutline(rings, outlineMaterial, (x, z) => groundAt(x, z) + 0.4);
      }
      for (const [outer, ...holes] of polygons(massing?.geometry)) {
        const toShape = (ring) => ring.slice(0, -1).map(([lon, lat]) => {
          const { x, z } = projection.toWorld(lon, lat);
          return new THREE.Vector2(x, -z);
        });
        const shape = new THREE.Shape(toShape(outer));
        shape.holes = holes.map((ring) => new THREE.Path(toShape(ring)));
        const geometry = new THREE.ExtrudeGeometry(shape, { depth: massing.height_m, bevelEnabled: false });
        geometry.rotateX(-Math.PI / 2);
        const position = geometry.attributes.position;
        for (let i = 0; i < position.count; i++) {
          position.setY(i, position.getY(i) + groundAt(position.getX(i), position.getZ(i)) + 0.15);
        }
        geometry.computeVertexNormals();
        group.add(new THREE.Mesh(geometry, volumeMaterial));
        drawOutline([outer, ...holes], roofMaterial, (x, z) => groundAt(x, z) + massing.height_m + 0.2);
      }
    },

    groundPoint(ray) {
      const point = new THREE.Vector3();
      const start = bound.containsPoint(ray.origin) ? ray.origin : ray.intersectBox(bound, point);
      if (!start) return null;
      const first = start.distanceTo(ray.origin);
      const gap = (t) => {
        ray.at(t, point);
        return point.y - groundAt(point.x, point.z);
      };
      if (gap(first) < 0) return null;
      const step = terrain.grid / 2;
      const limit = first + terrain.extent * 3 + maxHeight;
      for (let t = first + step; t <= limit; t += step) {
        if (gap(t) <= 0) {
          let low = t - step;
          let high = t;
          for (let i = 0; i < 10; i++) {
            const mid = (low + high) / 2;
            if (gap(mid) > 0) low = mid;
            else high = mid;
          }
          ray.at(high, point);
          if (Math.abs(point.x) > terrain.extent || Math.abs(point.z) > terrain.extent) return null;
          return projection.toLonLat(point.x, point.z);
        }
        if (Math.abs(point.x) > terrain.extent || Math.abs(point.z) > terrain.extent ||
            point.y > maxHeight || point.y < -1) break;
      }
      return null;
    },
  };
}
