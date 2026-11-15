"""Review-activity model: train in Oakland, select on Berkeley, test once on SF.

Target y = log1p(Places.reviews_count). Within each category the target is
standardized with training mean/std (std 0 -> 1); predictions are inverted to
the log-count scale for every metric. Missing counts are unlabeled.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import sklearn
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor

from .features import CATEGORICAL_FEATURES, FEATURE_COLUMNS, assert_no_leakage

log = logging.getLogger(__name__)

SEED = 42
BASE_PARAMS = dict(learning_rate=0.05, max_iter=200, min_samples_leaf=20, early_stopping=False, random_state=SEED)
GRID = {"max_leaf_nodes": [15, 31], "l2_regularization": [0.0, 1.0]}
MIN_TRAIN_LABELED, MIN_VALID_LABELED = 100, 30
GATE_IMPROVEMENT = 0.05
MIN_SLICE_ROWS = 30
TARGET = "target_log1p_reviews_count"


# ---------------------------------------------------------------- baselines
@dataclass
class Baselines:
    category_median: dict[str, float]
    category_city_median: dict[tuple[str, str], float]

    @classmethod
    def fit(cls, train: pd.DataFrame) -> "Baselines":
        return cls(
            category_median=train.groupby("category")[TARGET].median().to_dict(),
            category_city_median={k: v for k, v in train.groupby(["category", "city"])[TARGET].median().items()},
        )

    def predict_category(self, df: pd.DataFrame) -> np.ndarray:
        return df["category"].map(self.category_median).astype(float).to_numpy()

    def predict_category_city(self, df: pd.DataFrame) -> np.ndarray:
        cc = np.array([self.category_city_median.get((c, city), np.nan) for c, city in zip(df["category"], df["city"])])
        # Unseen city/category cells fall back to the training category median.
        return np.where(np.isnan(cc), self.predict_category(df), cc)


# ---------------------------------------------------------------- model
@dataclass
class ReviewActivityModel:
    params: dict
    categories: list[str]
    norm: dict[str, tuple[float, float]] = field(default_factory=dict)
    estimator: HistGradientBoostingRegressor | None = None

    def fit(self, train: pd.DataFrame) -> "ReviewActivityModel":
        assert_no_leakage(FEATURE_COLUMNS)
        train = train[train["category"].isin(self.categories)]
        self.norm = {}
        for c, g in train.groupby("category"):
            sd = float(g[TARGET].std(ddof=0))
            self.norm[c] = (float(g[TARGET].mean()), sd if sd > 0 else 1.0)
        z = self._standardize(train)
        cat_mask = [c in CATEGORICAL_FEATURES for c in FEATURE_COLUMNS]
        self.estimator = HistGradientBoostingRegressor(**BASE_PARAMS, **self.params, categorical_features=cat_mask)
        self.estimator.fit(train[FEATURE_COLUMNS].to_numpy(float), z)
        return self

    def _standardize(self, df: pd.DataFrame) -> np.ndarray:
        mu = df["category"].map(lambda c: self.norm[c][0]).to_numpy(float)
        sd = df["category"].map(lambda c: self.norm[c][1]).to_numpy(float)
        return (df[TARGET].to_numpy(float) - mu) / sd

    def predict_log_count(self, df: pd.DataFrame) -> np.ndarray:
        out = np.full(len(df), np.nan)
        ok = df["category"].isin(self.categories).to_numpy()
        if ok.any():
            sub = df[ok]
            z = self.estimator.predict(sub[FEATURE_COLUMNS].to_numpy(float))
            mu = sub["category"].map(lambda c: self.norm[c][0]).to_numpy(float)
            sd = sub["category"].map(lambda c: self.norm[c][1]).to_numpy(float)
            # log1p(count) >= 0: clamp so inverted predictions are never negative counts.
            out[ok] = np.maximum(z * sd + mu, 0.0)
        return out


def selected_predictions(df: pd.DataFrame, baseline: Baselines, model: ReviewActivityModel | None = None):
    """Apply the same model/category-baseline policy in evaluation and serving."""
    category = baseline.predict_category(df)
    learned = model.predict_log_count(df) if model is not None else np.full(len(df), np.nan)
    available_model = ~np.isnan(learned)
    predicted = np.where(available_model, learned, category)
    source = np.where(available_model, "model", np.where(~np.isnan(category), "category_baseline", "unavailable"))
    return predicted, source


# ---------------------------------------------------------------- metrics
def rmse(y: np.ndarray, p: np.ndarray) -> float | None:
    m = ~(np.isnan(y) | np.isnan(p))
    return float(np.sqrt(np.mean((y[m] - p[m]) ** 2))) if m.any() else None


def spearman(y: np.ndarray, p: np.ndarray) -> float | None:
    m = ~(np.isnan(y) | np.isnan(p))
    if m.sum() < 2 or np.unique(y[m]).size < 2 or np.unique(p[m]).size < 2:
        return None  # constant ranks -> undefined
    return float(spearmanr(y[m], p[m]).statistic)


def per_category(df: pd.DataFrame, pred_col: str, categories: list[str]) -> dict:
    out = {}
    for c in categories:
        g = df[df["category"] == c]
        y, p = g[TARGET].to_numpy(float), g[pred_col].to_numpy(float)
        out[c] = {"n": int(len(g)), "n_predicted": int((~np.isnan(p)).sum()),
                  "rmse_log_count": rmse(y, p), "spearman": spearman(y, p)}
    return out


def macro_rmse(stats: dict) -> float | None:
    vals = [s["rmse_log_count"] for s in stats.values() if s["rmse_log_count"] is not None]
    return float(np.mean(vals)) if vals else None


def slice_metrics(df: pd.DataFrame, pred_col: str, thresholds: dict[str, float]) -> dict:
    out = {}
    for feat, thr in thresholds.items():
        if thr is None or np.isnan(thr):
            out[feat] = {"threshold": None, "status": "unavailable (no training values)"}
            continue
        v = df[feat].to_numpy(float)
        for name, mask in (("low", v < thr), ("high", v >= thr), ("missing", np.isnan(v))):
            g = df[mask]
            key = f"{feat}:{name}"
            if len(g) < MIN_SLICE_ROWS:
                out[key] = {"n": int(len(g)), "status": "insufficient"}
                continue
            y, p = g[TARGET].to_numpy(float), g[pred_col].to_numpy(float)
            sp = [spearman(gg[TARGET].to_numpy(float), gg[pred_col].to_numpy(float))
                  for _, gg in g.groupby("category") if len(gg) >= MIN_SLICE_ROWS]
            sp = [s for s in sp if s is not None]
            out[key] = {"n": int(len(g)), "threshold": float(thr), "rmse_log_count": rmse(y, p),
                        "mean_within_category_spearman": float(np.mean(sp)) if sp else None}
    return out


def fit_slice_thresholds(train: pd.DataFrame) -> dict[str, float]:
    return {c: float(train[c].median()) if train[c].notna().any() else np.nan
            for c in ("median_household_income_usd", "population_density_per_km2")}


def worst_errors(df: pd.DataFrame, pred_col: str, n: int = 10) -> list[dict]:
    d = df.assign(abs_error=(df[TARGET] - df[pred_col]).abs()).dropna(subset=["abs_error"])
    d = d.sort_values(["abs_error", "place_id"], ascending=[False, True]).head(n)
    return d[["place_id", "category", "city", TARGET, pred_col, "abs_error"]].to_dict("records")


# ---------------------------------------------------------------- pipeline
def train_and_evaluate(place_features: pd.DataFrame, candidates: list[str]) -> dict:
    """Run the full selection protocol. Returns a dict of results and fitted objects."""
    lab = place_features[place_features["labeled"]].copy()
    # Freeze target-quality decisions on Oakland alone. Held-out labels must
    # never change training eligibility, including for all-zero collections.
    training_labels = lab[lab["split"] == "train"]
    degenerate = sorted(c for c, g in training_labels.groupby("category") if (g[TARGET] == 0).all())
    lab = lab[~lab["category"].isin(degenerate)]
    train, valid, test = (lab[lab["split"] == s] for s in ("train", "validation", "test"))

    counts = {c: {"train": int((train["category"] == c).sum()), "validation": int((valid["category"] == c).sum()),
                  "test": int((test["category"] == c).sum())} for c in candidates}
    model_cats = [c for c in candidates
                  if counts[c]["train"] >= MIN_TRAIN_LABELED and counts[c]["validation"] >= MIN_VALID_LABELED]
    unsupported = [c for c in candidates if c not in model_cats]

    report: dict = {
        "estimator": f"sklearn.ensemble.HistGradientBoostingRegressor (scikit-learn {sklearn.__version__})",
        "base_params": BASE_PARAMS, "grid": GRID, "seed": SEED,
        "labeled_counts": counts, "unlabeled_rows": int((~place_features["labeled"]).sum()),
        "model_categories": model_cats, "unsupported_categories": unsupported,
        "degenerate_target_categories": degenerate,
        "target_quality_fit_on": "train (Oakland) only",
        "gate": {"metric": "macro-average Berkeley category log-count RMSE",
                 "required_improvement_vs_category_baseline": GATE_IMPROVEMENT},
    }

    # Stage 1: fit on Oakland, select on Berkeley.
    base1 = Baselines.fit(train)
    v = valid.copy()
    v["pred_category_baseline"] = base1.predict_category(v)
    v["pred_category_city_baseline"] = base1.predict_category_city(v)
    report["validation"] = {
        "category_baseline": per_category(v, "pred_category_baseline", model_cats),
        "category_city_baseline": per_category(v, "pred_category_city_baseline", model_cats),
        "candidates": [],
    }
    best = None
    if model_cats:
        for leaves, l2 in itertools.product(GRID["max_leaf_nodes"], GRID["l2_regularization"]):
            params = {"max_leaf_nodes": leaves, "l2_regularization": l2}
            m = ReviewActivityModel(params, model_cats).fit(train)
            v["pred_model"] = m.predict_log_count(v)
            stats = per_category(v, "pred_model", model_cats)
            entry = {"params": params, "per_category": stats, "macro_rmse": macro_rmse(stats)}
            report["validation"]["candidates"].append(entry)
            if entry["macro_rmse"] is not None and (best is None or entry["macro_rmse"] < best["macro_rmse"]):
                best = entry
    base_macro = macro_rmse(report["validation"]["category_baseline"])
    passed = bool(best and base_macro and best["macro_rmse"] <= (1 - GATE_IMPROVEMENT) * base_macro)
    report["gate"].update({
        "category_baseline_macro_rmse": base_macro,
        "best_model_macro_rmse": best["macro_rmse"] if best else None,
        "best_params": best["params"] if best else None,
        "relative_improvement": (1 - best["macro_rmse"] / base_macro) if best and base_macro else None,
        "passed": passed,
    })
    selected = "model" if passed else "category_baseline"
    report["selected"] = selected
    thr_valid = fit_slice_thresholds(train)
    selection_model = ReviewActivityModel(best["params"], model_cats).fit(train) if passed else None
    v["pred_selected"], v["prediction_source"] = selected_predictions(v, base1, selection_model)
    report["validation"]["selected"] = per_category(v, "pred_selected", candidates)
    report["validation"]["slices"] = slice_metrics(v, "pred_selected", thr_valid)

    # Stage 2: refit on Oakland + Berkeley, evaluate SF exactly once.
    refit = pd.concat([train, valid])
    base2 = Baselines.fit(refit)
    final_model = ReviewActivityModel(best["params"], model_cats).fit(refit) if passed else None
    t = test.copy()
    t["pred_category_baseline"] = base2.predict_category(t)
    t["pred_category_city_baseline"] = base2.predict_category_city(t)
    t["pred_selected"], t["prediction_source"] = selected_predictions(t, base2, final_model)
    report["test"] = {
        "evaluated_once": True,
        "selected": per_category(t, "pred_selected", candidates),
        "category_baseline": per_category(t, "pred_category_baseline", candidates),
        "category_city_baseline": per_category(t, "pred_category_city_baseline", candidates),
        "slices": slice_metrics(t, "pred_selected", fit_slice_thresholds(refit)),
        "worst_errors": worst_errors(t, "pred_selected"),
    }
    report["test"]["selected_macro_rmse"] = macro_rmse(report["test"]["selected"])
    report["test"]["category_baseline_macro_rmse"] = macro_rmse(report["test"]["category_baseline"])
    report["validation"]["worst_errors"] = worst_errors(v, "pred_selected")
    report["baselines"] = {
        "category_median_log_count": base2.category_median,
        "category_city_median_log_count": {f"{c}|{city}": val for (c, city), val in base2.category_city_median.items()},
        "fit_on": "train+validation (Oakland + Berkeley)",
    }

    eval_preds = pd.concat([
        v.assign(stage="validation")[["place_id", "category", "city", "split", "stage", TARGET, "pred_selected",
                                      "pred_category_baseline", "pred_category_city_baseline", "prediction_source"]],
        t.assign(stage="test")[["place_id", "category", "city", "split", "stage", TARGET, "pred_selected",
                                "pred_category_baseline", "pred_category_city_baseline", "prediction_source"]],
    ], ignore_index=True)
    return {"report": report, "final_model": final_model, "baselines": base2, "selected": selected,
            "eval_predictions": eval_preds, "model_categories": model_cats}


def predict_parcels(parcel_features: pd.DataFrame, result: dict) -> pd.DataFrame:
    out = parcel_features[["parcel_id", "category"]].rename(columns={"category": "category_id"}).copy()
    model = result["final_model"] if result["selected"] == "model" else None
    pred, source = selected_predictions(parcel_features, result["baselines"], model)
    out["predicted_log1p_reviews_count"] = pred
    out["predicted_reviews_count"] = np.expm1(pred)
    out["source"] = source
    return out.reset_index(drop=True)
