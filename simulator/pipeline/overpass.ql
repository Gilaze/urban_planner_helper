[out:json][timeout:300][bbox:37.6835,-122.544,37.8565,-122.326];
(
 way[highway~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|pedestrian|service|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link)$"];
 way[leisure~"^(park|garden|golf_course|pitch|playground|nature_reserve|recreation_ground|dog_park)$"];
 relation[leisure~"^(park|garden|golf_course|nature_reserve|recreation_ground)$"];
 way[landuse~"^(grass|forest|cemetery|recreation_ground|meadow|village_green)$"];
 relation[landuse~"^(grass|forest|cemetery|recreation_ground|meadow)$"];
 way[natural~"^(wood|scrub|grassland|heath|beach|sand|water)$"];
 relation[natural~"^(wood|scrub|grassland|heath|beach|sand|water)$"];
 way[man_made=pier];
 relation[man_made=pier];
);
out body geom;
