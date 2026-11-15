import numpy as np
import pandas as pd
import pytest
from pyproj import Transformer

from parcel_pipeline.features import (
    FEATURE_COLUMNS, SupplyIndex, assert_no_leakage, build_place_features, spatial_features,
)
from parcel_pipeline.model import Baselines, TARGET, predict_parcels, spearman, train_and_evaluate

CANDS = ("clinic", "pharmacy", "grocery", "bank_credit_union", "school")
_inv = Transformer.from_crs("EPSG:26910", "EPSG:4326", always_xy=True)
X0, Y0 = 565_000.0, 4_184_000.0  # downtown Oakland in UTM 10N


def _pt(dx, dy):
    return _inv.transform(X0 + dx, Y0 + dy)


def _place(pid, dx, dy, tags, primary=None, current=True, city="Oakland", reviews=1):
    lon, lat = _pt(dx, dy)
    return {"place_id": pid, "lon": lon, "lat": lat, "service_tags": tags, "is_current_supply": current,
            "primary_category": primary or (tags[0] if tags else "other"), "city": city,
            "closure_status": "open" if current else "permanently_closed", "reviews_count": reviews}


def test_counts_exclude_focal_and_respect_radii():
    pl = pd.DataFrame([
        _place("F", 0, 0, ["clinic"]),
        _place("C1", 300, 0, ["clinic"]),
        _place("C2", 700, 0, ["clinic"]),
        _place("C3", 1500, 0, ["clinic"]),
        _place("CLOSED", 10, 0, ["clinic"], current=False),
        _place("G", 100, 0, ["grocery"]),
        _place("T", 0, 450, ["transit"]),
    ])
    idx = SupplyIndex(pl, CANDS)
    lon, lat = _pt(0, 0)
    f = spatial_features(pd.DataFrame({"lon": [lon], "lat": [lat], "focal_place_id": ["F"]}), "clinic", idx, None).iloc[0]
    assert (f["same_category_count_400m"], f["same_category_count_800m"], f["same_category_count_1600m"]) == (1, 2, 3)
    assert f["other_service_count_400m"] == 1  # grocery; transit excluded from other services
    assert (f["transit_count_400m"], f["transit_count_800m"]) == (0, 1)
    assert f["nearest_peer_distance_m"] == pytest.approx(300, abs=1)
    # Same point as a parcel (no focal): the focal clinic now counts.
    g = spatial_features(pd.DataFrame({"lon": [lon], "lat": [lat]}), "clinic", idx, None).iloc[0]
    assert g["same_category_count_400m"] == 2 and g["nearest_peer_distance_m"] == pytest.approx(0, abs=1e-6)
    assert list(g.index) == FEATURE_COLUMNS


def test_no_peer_is_null_and_flagged():
    pl = pd.DataFrame([_place("F", 0, 0, ["pharmacy"])])
    lon, lat = _pt(0, 0)
    f = spatial_features(pd.DataFrame({"lon": [lon], "lat": [lat], "focal_place_id": ["F"]}),
                         "pharmacy", SupplyIndex(pl, CANDS), None).iloc[0]
    assert np.isnan(f["nearest_peer_distance_m"]) and f["nearest_peer_missing"] == 1


def test_leakage_guard():
    assert_no_leakage(FEATURE_COLUMNS)
    with pytest.raises(AssertionError):
        assert_no_leakage([*FEATURE_COLUMNS, "mean_rating"])


def test_spearman_constant_is_undefined():
    assert spearman(np.array([1.0, 1.0, 1.0]), np.array([1.0, 2.0, 3.0])) is None


def _synthetic_places(n_oak=150, n_berk=40, n_sf=40, seed=0, signal=True):
    rng = np.random.default_rng(seed)
    rows = []
    for city, n, off in (("Oakland", n_oak, 0), ("Berkeley", n_berk, 20_000), ("San Francisco", n_sf, -15_000)):
        for i in range(n):
            for cat in ("clinic", "grocery"):
                dx, dy = rng.uniform(0, 3000, 2)
                rows.append(_place(f"{city[:2]}{cat[:2]}{i}", off + dx, dy, [cat], city=city, reviews=0))
        for i in range(n):  # transit stops drive the signal
            dx, dy = rng.uniform(0, 3000, 2)
            rows.append(_place(f"{city[:2]}T{i}", off + dx, dy, ["transit"], city=city))
    pl = pd.DataFrame(rows)
    idx = SupplyIndex(pl, CANDS)
    pf = build_place_features(pl, idx, None, {"Oakland": "train", "Berkeley": "validation", "San Francisco": "test"})
    noise = np.random.default_rng(seed + 1).normal(0, 0.3, len(pf))
    base = pf["transit_count_800m"] * 0.5 if signal else 0
    pf[TARGET] = np.clip(base + noise + 1, 0, None)
    pf["labeled"] = True
    return pf


def test_train_select_and_gate_pass_with_signal():
    pf = _synthetic_places()
    res = train_and_evaluate(pf, list(CANDS))
    rep = res["report"]
    assert rep["model_categories"] == ["clinic", "grocery"]
    assert set(rep["unsupported_categories"]) == {"pharmacy", "bank_credit_union", "school"}
    assert len(rep["validation"]["candidates"]) == 4
    assert rep["gate"]["passed"] and res["selected"] == "model"
    assert rep["test"]["evaluated_once"]


def test_gate_fails_without_signal_and_falls_back_to_baseline():
    pf = _synthetic_places(signal=False)
    res = train_and_evaluate(pf, list(CANDS))
    assert not res["report"]["gate"]["passed"] and res["selected"] == "category_baseline"
    qf = pf[FEATURE_COLUMNS].head(3).assign(parcel_id=["a", "b", "c"], category=["clinic", "school", "grocery"])
    preds = predict_parcels(qf, res).set_index("parcel_id")
    assert preds.loc["a", "review_activity_source"] == "category_baseline"
    assert preds.loc["b", "review_activity_source"] is None and np.isnan(preds.loc["b", "review_activity_log1p"])


def test_repeat_runs_are_identical():
    pf = _synthetic_places()
    a = train_and_evaluate(pf, list(CANDS))
    b = train_and_evaluate(pf, list(CANDS))
    pd.testing.assert_frame_equal(a["eval_predictions"], b["eval_predictions"])


def test_category_city_baseline_falls_back():
    tr = pd.DataFrame({"category": ["clinic"] * 3, "city": ["Oakland"] * 3, TARGET: [1.0, 2.0, 3.0]})
    b = Baselines.fit(tr)
    q = pd.DataFrame({"category": ["clinic", "school"], "city": ["San Francisco", "Oakland"]})
    out = b.predict_category_city(q)
    assert out[0] == 2.0 and np.isnan(out[1])


def test_all_zero_category_is_unlabeled_not_predicted_zero():
    pf = _synthetic_places()
    pf.loc[pf["category"] == "grocery", TARGET] = 0.0
    res = train_and_evaluate(pf, list(CANDS))
    assert res["report"]["degenerate_target_categories"] == ["grocery"]
    assert "grocery" not in res["model_categories"]
    qf = pf[FEATURE_COLUMNS].head(1).assign(parcel_id=["a"], category=["grocery"])
    assert predict_parcels(qf, res)["review_activity_source"].iloc[0] is None
