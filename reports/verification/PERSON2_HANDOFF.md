# Person 2 handoff check — ready for functional testing

Checked main `4cebb7fe3afc478fde8cd7f8be010f4b09620404` and the new
[parcel-planner-data-0.1.1.zip](https://drive.google.com/file/d/1jFsIE2-kJqE9oNqLvLO6G0MjYBFApg3g/view)
from the team Drive. This is a bounded integrity check, not final release sign-off.

## Confirmed

- ZIP SHA256 matches `DATA.md`:
  `3131d323931951a78f9b3b9668349aa42ddfd75961c35c91f067da2258d50578`.
- Version is `0.1.1+2026-11-15.1+ad464d957df7`.
- All five published files match their declared hashes; all four Parquet tables
  also match declared row counts: 155,027 parcels, 147,429 Places, 3,204
  demographic rows and 775,135 review predictions.
- All 160 category-response rows match the actual supplied Places Parquet.
  All 45 previously rejected mappings are removed from candidate supply.
- The unchanged Data suite passes **39 tests**, 4.07 seconds, Python 3.11.
- The ZIP includes the frozen Backend release, fitted-model file and model report.
  The model pickle was not loaded and no SF evaluation was rerun during this check.

[Machine-readable evidence](person2-handoff-check.json) records the exact checks.

## Follow-up for final validation

Person 2 explicitly leaves 34 owner/policy decisions pending and one changed
mapping for re-review. The published ZIP omits the full canonical tables,
`model_normalization.json`, frozen held-out predictions and splits. The code now
exports normalization evidence, but that file is not packaged in this ZIP.
Those gaps affect final data/model acceptance; they need not hold up functional
UI/API testing with the supplied release.

Person 1 also pushed new inspection/vacancy UI code. Earlier browser findings
predate that push; a new integrated browser run has not been performed here.

## Scope

The team can start functional testing using the new ZIP and the instructions in
`DATA.md` and `simulator/README.md`. My overlapping draft Data changes were saved
in a local stash and were not reapplied or pushed. This update changes verification
notes only. Earlier statements about unavailable artifacts concerned GitHub;
the Drive bundle is now located and verified. The old 0.1.0 ZIP was an initial
serving handoff, and this 0.1.1 ZIP is its updated replacement.
