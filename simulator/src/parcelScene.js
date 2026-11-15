import * as THREE from 'three';
import { polygons } from './parcelGeometry.js';

export function createParcelScene(scene, projection, groundAt, terrain) {
  const group = new THREE.Group();
  group.name = 'parcel-scenario';
  scene.add(group);
  const outlineMaterial = new THREE.LineBasicMaterial({ color: 0x155fd2 });
  const volumeMaterial = new THREE.MeshStandardMaterial({ color: 0x2279ed, roughness: 0.75 });
  const roofMaterial = new THREE.LineBasicMaterial({ color: 0xc6e2ff });
  const maxHeight = terrain.heights.reduce((max, height) => Math.max(max, height), 1) + 1;
  const bound = new THREE.Box3(
    new THREE.Vector3(-terrain.extent, -1, -terrain.extent),
    new THREE.Vector3(terrain.extent, maxHeight, terrain.extent),
  );

  function drawOutline(rings, material, elevation) {
    for (const ring of rings) {
      const points = ring.slice(0, -1).map(([lon, lat]) => {
        const { x, z } = projection.toWorld(lon, lat);
        return new THREE.Vector3(x, elevation(x, z), z);
      });
      group.add(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(points), material));
    }
  }

  return {
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
