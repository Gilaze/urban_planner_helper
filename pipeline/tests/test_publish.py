import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq
import pytest
from shapely.geometry import box

from parcel_pipeline.cli import publish


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def release(tmp_path):
    root = tmp_path / "artifacts/release"
    root.mkdir(parents=True)
    lots = gpd.GeoDataFrame({"parcel_id": ["sf:good", "sf:unusable", "sf:invalid*"],
                            "geometry_quality": ["valid", "unusable", "valid"],
                            "geometry": [box(-122.42, 37.77, -122.419, 37.771), None,
                                         box(-122.43, 37.78, -122.429, 37.781)]}, crs=4326)
    lots.to_parquet(root / "parcels.geoparquet", index=False)
    pd.DataFrame([{"parcel_id": id, "category_id": category, "predicted_log1p_reviews_count": 1.0, "source": "category_baseline"}
                  for id in lots["parcel_id"] for category in ("clinic", "school")]).to_parquet(root / "review_predictions.parquet", index=False)
    pd.DataFrame({"place_id": ["known-place"]}).to_parquet(root / "places.parquet", index=False)
    gpd.GeoDataFrame({"geoid": ["060750101001"], "population": [100],
                      "geometry": [box(-122.44, 37.76, -122.4, 37.8)]}, crs=4326).to_parquet(root / "demographics.geoparquet", index=False)
    (root / "backend_manifest.json").write_text(json.dumps({"data_version": "canonical-test", "coverage": {"census": "complete"}, "spatial_coverage": {"type": "Polygon"}}))
    outputs = {p.name: {"rows": pq.ParquetFile(p).metadata.num_rows, "sha256": digest(p), "grain": "test"}
               for p in root.iterdir() if p.suffix in {".parquet", ".geoparquet"}}
    outputs["unpublished-model.joblib"] = {"rows": 1, "sha256": "0" * 64}
    (root / "source_manifest.json").write_text(json.dumps({"pipeline_version": "canonical-version", "data_version": "canonical-test",
                "census_skipped": False, "sources": {"synthetic": {"url": "synthetic fixture"}}, "outputs": outputs}))
    return SimpleNamespace(artifacts_dir=tmp_path / "artifacts"), root, tmp_path / "published"


def test_published_bytes_counts_and_canonical_lineage_match(release):
    settings, source, output = release
    canonical_bytes = (source / "source_manifest.json").read_bytes()
    canonical = json.loads(canonical_bytes)
    publish(settings, output)
    result = json.loads((output / "source_manifest.json").read_text())
    assert set(result["outputs"]) == {"backend_manifest.json", "parcels.geoparquet", "places.parquet", "demographics.geoparquet", "review_predictions.parquet"}
    for name, meta in result["outputs"].items():
        assert meta["sha256"] == digest(output / name)
        if name.endswith((".parquet", ".geoparquet")):
            assert meta["rows"] == pq.ParquetFile(output / name).metadata.num_rows
    assert result["outputs"]["parcels.geoparquet"]["rows"] == 1
    assert result["outputs"]["review_predictions.parquet"]["rows"] == 2
    assert set(pd.read_parquet(output / "review_predictions.parquet")["parcel_id"]) == {"sf:good"}
    assert (source / "source_manifest.json").read_bytes() == canonical_bytes
    lineage = result["publication"]
    assert lineage["canonical_manifest_sha256"] == hashlib.sha256(canonical_bytes).hexdigest()
    assert lineage["canonical_data_version"] == result["data_version"] == "canonical-test"
    assert lineage["canonical_outputs"] == canonical["outputs"]
    assert lineage["omitted_files"] == ["unpublished-model.joblib"]
    assert lineage["omitted_parcels"]["total"] == 2
    assert lineage["omitted_parcels"]["unusable_geometry"] == lineage["omitted_parcels"]["id_outside_contract"] == 1


def test_publication_is_repeatable(release):
    settings, _, output = release
    publish(settings, output)
    before = {p.name: digest(p) for p in output.iterdir()}
    publish(settings, output)
    assert before == {p.name: digest(p) for p in output.iterdir()}


def test_explicit_missing_census_removes_old_copy_and_updates_coverage(release):
    settings, source, output = release
    publish(settings, output)
    (source / "demographics.geoparquet").unlink()
    publish(settings, output, allow_missing_census=True)
    manifest = json.loads((output / "source_manifest.json").read_text())
    descriptor = json.loads((output / "backend_manifest.json").read_text())
    assert not (output / "demographics.geoparquet").exists()
    assert "demographics.geoparquet" not in manifest["outputs"] and manifest["census_skipped"] is True
    assert "demographics.geoparquet" in manifest["publication"]["omitted_files"]
    assert descriptor["coverage"]["census"] == "skipped" and descriptor["spatial_coverage"] is None
    assert manifest["outputs"]["backend_manifest.json"]["sha256"] == digest(output / "backend_manifest.json")


def test_missing_required_file_leaves_previous_publication_intact(release):
    settings, source, output = release
    publish(settings, output)
    before = {p.name: digest(p) for p in output.iterdir()}
    (source / "places.parquet").unlink()
    with pytest.raises(SystemExit, match="places.parquet"):
        publish(settings, output)
    assert before == {p.name: digest(p) for p in output.iterdir()}


def test_corrupt_canonical_fingerprint_is_rejected_before_output_changes(release):
    settings, source, output = release
    publish(settings, output)
    before = {p.name: digest(p) for p in output.iterdir()}
    path = source / "source_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["outputs"]["parcels.geoparquet"]["sha256"] = "f" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Canonical fingerprint"):
        publish(settings, output)
    assert before == {p.name: digest(p) for p in output.iterdir()}


def test_publishing_cannot_overwrite_the_canonical_directory(release):
    settings, source, _ = release
    before = digest(source / "source_manifest.json")
    with pytest.raises(ValueError, match="must differ"):
        publish(settings, source)
    assert digest(source / "source_manifest.json") == before
