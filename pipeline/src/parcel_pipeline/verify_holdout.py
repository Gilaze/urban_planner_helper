"""Independently recompute the committed holdout metrics from its row-level CSV."""
import csv
import hashlib
import json
import math
from pathlib import Path
from .settings import REPO_ROOT


def verify(directory: Path):
    report = json.loads((directory/'results.json').read_text())
    raw = (directory/'predictions.csv').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == report['predictions_sha256']
    with (directory/'predictions.csv').open() as source:
        rows = list(csv.DictReader(source))
    ids = [r['place_id'] for r in rows]
    assert len(ids) == len(set(ids)) == report['n']
    assert hashlib.sha256(('\n'.join(sorted(ids))+'\n').encode()).hexdigest() == report['cohort_place_id_sha256']
    assert not {'Oakland','Berkeley','San Francisco'} & {r['city'] for r in rows}
    assert all(r['county_fips'] in {'001','013','041','081'} for r in rows)
    def rmse(group, key):
        pairs = [(float(r['target_log1p_reviews_count']),float(r[key])) for r in group if r[key]]
        return math.sqrt(math.fsum((y-p)**2 for y,p in pairs)/len(pairs)) if pairs else None
    def close(actual,expected):
        assert actual is None and expected is None or actual is not None and expected is not None and math.isclose(actual,expected,rel_tol=1e-12,abs_tol=1e-12),(actual,expected)
    for category, values in report['categories'].items():
        group=[r for r in rows if r['category']==category]
        for label,key in [('model','pred_model'),('baseline','pred_baseline'),('deployment_policy','pred_selected')]:
            close(rmse(group,key),values[label]['rmse'])
    county_groups=0
    for county in ['001','013','041','081']:
        group=[r for r in rows if r['county_fips']==county]
        result=report['subgroups']['county:'+county]
        assert len(group)==result['n'] and len(group)>=30
        close(rmse(group,'pred_selected'),result['deployment_policy']['rmse'])
        close(rmse(group,'pred_baseline'),result['baseline']['rmse'])
        county_groups+=1
    for key,prediction in [('model_macro_rmse','pred_model'),('baseline_macro_rmse','pred_baseline')]:
        close(sum(rmse([r for r in rows if r['category']==c],prediction) for c in ['clinic','grocery'])/2,report['primary'][key])
    return {'status':'pass','rows':len(rows),'distinct_places':len(set(ids)),
            'original_cities_present':False,'county_subgroups_verified':county_groups,
            'category_and_primary_rmse_independently_recomputed':True,'predictions_sha256':hashlib.sha256(raw).hexdigest()}


def main():
    print(json.dumps(verify(REPO_ROOT/'reports/advanced-track/final-holdout'),indent=2))

if __name__=='__main__':
    main()
