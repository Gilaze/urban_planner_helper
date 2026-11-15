[out:json][timeout:300][bbox:37.7075,-122.52,37.835,-122.355];
(
 way[building](if: number(t["height"]) >= 18 || number(t["building:levels"]) >= 6);
 relation[building](if: number(t["height"]) >= 18 || number(t["building:levels"]) >= 6);
);
out body geom;
