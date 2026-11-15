"""Export a complete, bounded DataSF parcel snapshot with stable pagination."""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from .datasf import DataSF, DataSFParcels, ProviderUnavailable, collect_parcels, datasf_client, parcel_query
from .geometry import validate_bbox


def write_atomic(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", delete=False, mode="w", encoding="utf-8", newline="\n"
        ) as handle:
            temporary = Path(handle.name)
            handle.write(contents)
            handle.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", required=True, help="WGS84 west,south,east,north")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    token = os.getenv("DATASF_APP_TOKEN")
    if not token:
        parser.error("Set DATASF_APP_TOKEN before querying DataSF V3")
    try:
        bounds = validate_bbox(args.bbox)
        with datasf_client(token) as client:
            provider = DataSFParcels(DataSF(client))
            rows = provider.api.paginate(provider.url, parcel_query(bounds))
            features = [parcel.model_dump(mode="json") for parcel in collect_parcels(rows)]
        collection = {
            "type": "FeatureCollection",
            "features": features,
            "source": {
                "version": provider.version,
                "freshness": "live",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        write_atomic(args.output, json.dumps(collection, allow_nan=False, separators=(",", ":")))
    except (OSError, ProviderUnavailable, ValueError) as exc:
        parser.error(str(exc))
    print(f"Exported {len(features)} physical parcels to {args.output}")


if __name__ == "__main__":
    main()
