"""Frozen-model evaluation on previously excluded geographic labels; no downloads or fitting.

Run after reading reports/advanced-track/protocol.json. Never tune on these results.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import joblib
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from .categories import load_categories
from .features import FEATURE_COLUMNS, SupplyIndex, build_place_features, tract_covariates
from .model import TARGET
from .settings import REPO_ROOT


def sha(data):
    return hashlib.sha256(data).hexdigest()


def metrics(frame, prediction):
    y = frame[TARGET].to_numpy(float)
    p = frame[prediction].to_numpy(float)
    ok = np.isfinite(y) & np.isfinite(p)
    y, p = y[ok], p[ok]
    return {"n": len(frame), "n_predicted": int(ok.sum()),
            "rmse": float(np.sqrt(np.mean((y-p)**2))) if len(y) else None,
            "mae": float(np.mean(np.abs(y-p))) if len(y) else None,
            "spearman": float(spearmanr(y,p).statistic) if len(y)>1 and np.unique(y).size>1 and np.unique(p).size>1 else None}


def evaluate(root: Path, output: Path):
    protocol_path = root / 'reports/advanced-track/protocol.json'
    protocol = json.loads(protocol_path.read_text())
    # All checks and model choices are fixed before computing or inspecting holdout metrics.
    for name, expected in protocol['sha256'].items():
        if (root/name).is_file() and sha((root/name).read_bytes()) != expected:
            raise ValueError(f'Frozen input changed: {name}')
    with zipfile.ZipFile(root/'demo/parcel-planner-data-0.1.1.zip') as archive:
        def read(name):
            data = archive.read(name)
            if name in protocol['sha256'] and sha(data) != protocol['sha256'][name]:
                raise ValueError(f'Frozen archive member changed: {name}')
            return data
        model = joblib.load(io.BytesIO(read(protocol['model_source'].split(':',1)[1])))
        old = json.loads(read(protocol['report_source'].split(':',1)[1]))
        places = pd.read_parquet(io.BytesIO(read('artifacts/person2/places.parquet')))
        source = json.loads(archive.read('artifacts/person2/source_manifest.json'))
    raw_places = root/'data/raw/places/google_maps.csv'
    if sha(raw_places.read_bytes()) != source['sources']['google_places']['sha256']:
        raise ValueError('Raw Places does not match the frozen release source')
    assert model.categories == protocol['learned_categories']
    assert model.params == old['gate']['best_params']
    former_ids = set(places.loc[places.city.notna(), 'place_id'])
    assert set(places.city.dropna().unique()) == {'Oakland','Berkeley','San Francisco'}
    # Existing category audit was selected only from the original split cities.
    audit = pd.read_csv(root/'reports/data/category_audit_sample.csv')
    if not set(audit.city.dropna()).issubset({'Oakland','Berkeley','San Francisco'}):
        raise ValueError('Previously audited locations extend into proposed holdout')
    candidates = load_categories().candidates
    # Identify new city membership spatially, without using held-out target values.
    boundaries = gpd.read_file(f"zip://{root/'data/raw/census/tl_2024_06_place.zip'}")
    boundaries = boundaries[boundaries.LSAD == '25'][['NAME','geometry']].to_crs('EPSG:4326')
    outside = places[places.city.isna()].copy()
    pts = gpd.GeoDataFrame(outside, geometry=gpd.points_from_xy(outside.longitude,outside.latitude),crs='EPSG:4326')
    joined = gpd.sjoin(pts, boundaries, how='inner', predicate='within')
    if joined.place_id.duplicated().any():
        raise ValueError('Ambiguous city membership')
    joined['city'] = joined.NAME
    joined = joined[~joined.city.isin(['Oakland','Berkeley','San Francisco'])]
    tiger = gpd.read_file(f"zip://{root/'data/raw/census/tl_2024_06_tract.zip'}")
    tiger = tiger[tiger.COUNTYFP.isin(['001','013','041','075','081'])].rename(columns={'GEOID':'geoid','ALAND':'land_area_m2'})
    acs = pd.read_csv(root/'data/raw/census/acs_2024_tract.csv',dtype={'geoid':str})
    tracts = tiger.merge(acs,on='geoid',how='left').to_crs('EPSG:4326')
    tracts['population_density_per_km2'] = tracts.population / (tracts.land_area_m2/1e6).where(tracts.land_area_m2>0)
    xy = joined.rename(columns={'longitude':'lon','latitude':'lat'})
    cov = tract_covariates(xy,tracts)
    joined = joined[cov.tract_geoid.str[2:5].isin(protocol['holdout_county_fips'])].copy()
    xy = joined.rename(columns={'longitude':'lon','latitude':'lat'})
    supply = SupplyIndex(places.rename(columns={'longitude':'lon','latitude':'lat'}),candidates)
    features = build_place_features(xy,supply,tracts,dict.fromkeys(sorted(joined.city.unique()),'final_holdout'))
    features['county_fips'] = tract_covariates(features.assign(lon=features.place_id.map(places.set_index('place_id').longitude),lat=features.place_id.map(places.set_index('place_id').latitude)),tracts).tract_geoid.str[2:5].to_numpy()
    # School exclusion and baseline policy were set on original training data, never on new labels.
    test = features[features.labeled & ~features.category.isin(old['degenerate_target_categories'])].copy()
    if test.empty or test.place_id.duplicated().any() or set(test.place_id) & former_ids:
        raise ValueError('Empty, duplicated or overlapping holdout')
    for category in protocol['learned_categories']:
        if (test.category==category).sum() < protocol['minimum_subgroup_n']:
            raise ValueError('Insufficient final holdout; report this without choosing another cohort')
    test['pred_model'] = model.predict_log_count(test)
    baseline = old['baselines']['category_median_log_count']
    test['pred_baseline'] = test.category.map(baseline)
    test['pred_selected'] = test.pred_model.fillna(test.pred_baseline)
    test['prediction_source'] = np.where(test.pred_model.notna(),'model',np.where(test.pred_baseline.notna(),'category_baseline','unavailable'))
    by_category = {c:{'model':metrics(test[test.category==c],'pred_model'), 'baseline':metrics(test[test.category==c],'pred_baseline'), 'deployment_policy':metrics(test[test.category==c],'pred_selected')} for c in candidates if c not in old['degenerate_target_categories']}
    learned = protocol['learned_categories']
    model_rmse = float(np.mean([by_category[c]['model']['rmse'] for c in learned]))
    baseline_rmse = float(np.mean([by_category[c]['baseline']['rmse'] for c in learned]))
    groups = {}
    def group(name,frame):
        groups[name] = {'n':len(frame),'status':'reported' if len(frame)>=30 else 'insufficient (<30)',
                        **({'deployment_policy':metrics(frame,'pred_selected'), 'baseline':metrics(frame,'pred_baseline'),
                            'learned_model':metrics(frame[frame.category.isin(learned)],'pred_model')} if len(frame)>=30 else {})}
    for county in protocol['holdout_county_fips']:
        group('county:'+county,test[test.county_fips==county])
    for feat,threshold in [('median_household_income_usd',106938.),('population_density_per_km2',5583.172577533759)]:
        for name,mask in [('low',test[feat]<threshold),('high',test[feat]>=threshold),('missing',test[feat].isna())]:
            group(feat+':'+name,test[mask])
    test['absolute_error'] = (test[TARGET]-test.pred_selected).abs()
    worst = test.sort_values(['absolute_error','place_id'],ascending=[False,True]).head(10)
    columns = ['place_id','city','county_fips','category',TARGET,'pred_model','pred_baseline','pred_selected','prediction_source','absolute_error','median_household_income_usd','population_density_per_km2']
    cohort = '\n'.join(sorted(test.place_id))+'\n'
    report = {'protocol_version':protocol['version'],'protocol_sha256':sha(protocol_path.read_bytes()),
              'evaluated_at_utc':datetime.now(timezone.utc).isoformat(),'data_version':old['data_version'],
              'n':len(test),'n_cities':int(test.city.nunique()),'fit_or_tuning_performed':False,
              'prior_split_overlap':len(set(test.place_id)&former_ids),'cohort_place_id_sha256':sha(cohort.encode()),
              'primary':{'model_macro_rmse':model_rmse,'baseline_macro_rmse':baseline_rmse,'relative_rmse_improvement':1-model_rmse/baseline_rmse},
              'categories':by_category,'subgroups':groups,'failures':json.loads(worst[columns].to_json(orient='records')),
              'limitations':[protocol['context_disclosure'],'Prior global category rules were frozen after SF development; SF remains exposed.','Geographic transfer evaluation of review activity, not validated demand or causal social impact.','Judges determine Advanced Track eligibility.']}
    output.mkdir(parents=True,exist_ok=True)
    test[columns].to_csv(output/'predictions.csv',index=False,float_format='%.17g')
    (output/'cohort-place-ids.txt').write_text(cohort)
    report['predictions_sha256'] = sha((output/'predictions.csv').read_bytes())
    (output/'results.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit('Output exists; use a separate directory for an explicitly labeled reproducibility rerun, never overwrite first-run evidence.')
    report=evaluate(REPO_ROOT,args.output)
    print(json.dumps({'n':report['n'],'n_cities':report['n_cities'],'primary':report['primary'],'overlap':report['prior_split_overlap']},indent=2))

if __name__=='__main__':
    main()
