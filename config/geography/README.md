# San Francisco tract boundaries

`sf-tracts.geojson` is an unchanged copy of the team's supplied tract boundary file:

- Source: [team Drive GeoJSON](https://drive.google.com/file/d/1X2oVuTAZH7kmUtnynN-LVVt4R1kZNbYx/view), originally named `tmph-tgz9.geojson`.
- 244 Census tract polygons with San Francisco GEOIDs (`06075…`). Properties contain Census identifiers, tract names, area measurements, representative coordinates, and source dates.
- Coordinates: WGS84 longitude/latitude (CRS84).
- Source `data_as_of`: 2021-02-01.
- Size: 457,367 bytes.
- SHA-256: `c613b0b9df678aa095981a988e045c7db070f9e0799113481b3937b0e9591889`.

This boundary file contains no parcel, business, review, or model records. The private parcel bundle stays in Drive. Run `python -m backend.build_planning` from the repository root after extracting that bundle to generate the local tract index.
