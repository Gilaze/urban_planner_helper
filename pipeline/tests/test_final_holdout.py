import hashlib
import json
import shutil

import numpy as np
import pandas as pd
import pytest

from parcel_pipeline.final_holdout import metrics
from parcel_pipeline.model import TARGET
from parcel_pipeline.settings import REPO_ROOT
from parcel_pipeline.verify_holdout import verify


def test_metrics_keep_zero_targets_and_do_not_invent_missing_predictions():
    frame = pd.DataFrame({TARGET: [0., 2., 8.], 'prediction': [0., 0., np.nan]})
    result = metrics(frame, 'prediction')
    assert result['n'] == 3
    assert result['n_predicted'] == 2
    assert result['rmse'] == pytest.approx(2**.5)
    assert result['mae'] == 1
    assert result['spearman'] is None


def test_first_run_metrics_independently_recompute():
    result = verify(REPO_ROOT/'reports/advanced-track/final-holdout')
    assert result['status'] == 'pass'
    assert result['rows'] == 10480
    assert result['county_subgroups_verified'] == 4


def test_verifier_rejects_county_outside_frozen_cohort_even_with_valid_file_hash(tmp_path):
    source = REPO_ROOT/'reports/advanced-track/final-holdout'
    shutil.copy(source/'results.json', tmp_path/'results.json')
    raw = (source/'predictions.csv').read_text().replace(',001,', ',075,', 1)
    (tmp_path/'predictions.csv').write_text(raw)
    report = json.loads((tmp_path/'results.json').read_text())
    report['predictions_sha256'] = hashlib.sha256((tmp_path/'predictions.csv').read_bytes()).hexdigest()
    (tmp_path/'results.json').write_text(json.dumps(report))
    with pytest.raises(AssertionError):
        verify(tmp_path)
