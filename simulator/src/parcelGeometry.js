export function polygons(geometry) {
  if (geometry?.type === 'Polygon') return [geometry.coordinates];
  return geometry?.type === 'MultiPolygon' ? geometry.coordinates : [];
}

function ringContains(ring, { lon, lat }) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [x, y] = ring[i];
    const [previousX, previousY] = ring[j];
    if ((y > lat) !== (previousY > lat) && lon < (previousX - x) * (lat - y) / (previousY - y) + x) {
      inside = !inside;
    }
  }
  return inside;
}

export function containsPoint(geometry, point) {
  return polygons(geometry).some(([outer, ...holes]) =>
    ringContains(outer, point) && !holes.some((hole) => ringContains(hole, point)));
}
