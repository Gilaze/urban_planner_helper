# Committed category sample review

Baseline: `35f3947`. Reviewer: Codex / Person 4. Input:
`reports/data/category_audit_sample.csv`, SHA256
`430ddabcb72d04c4cdbd39ce64fca346ba870538ccada4148470ceaa80bd124f`.
No external records, websites or acquired CSVs were used. The Data-owned sample
and `config/categories.yaml` remain unchanged.

Every one of the 160 distinct sampled places has an explicit verdict and note in
[category-review.csv](category-review.csv). Verdicts judge consistency of the
supplied name, source category, primary group and supply tags with the stated
candidate scope. An `accept` is a listing-label judgment; it does not certify
current opening status, public access, capacity or a distinct physical facility.
No classification error rate is inferred from this selected audit sample.

| Sample | Accept | Reject | Needs owner evidence |
| --- | ---: | ---: | ---: |
| Clinic candidate | 0 | 2 | 18 |
| Pharmacy candidate | 13 | 0 | 7 |
| Grocery candidate | 15 | 0 | 5 |
| Bank/credit union candidate | 17 | 0 | 3 |
| School candidate | 17 | 1 | 2 |
| Ambiguous | 18 | 2 | 0 |
| Clinic lookalikes | 0 | 20 | 0 |
| Grocery lookalikes | 0 | 20 | 0 |
| **Total** | **80** | **45** | **35** |

The main finding is **P4-DATA-005**, owned by Data / Person 2. All 20 clinic
lookalikes have a specialist/surgeon primary category mapped to `other` while
retaining `clinic` in the service tags. All 20 grocery lookalikes have
`Convenience store` mapped to `other` while retaining `grocery`. Primary-group
exclusion therefore does not exclude these records from supply. The taxonomy
labels the candidate “Primary care clinic” and explicitly says convenience stores
are not grocery supply. Data must reconcile the secondary-tag policy with that
scope and document any intentionally broader interpretation.

The other rejected listings are the MFT/OD credential cases mapped to primary
care, a college mapped to K-12 school, and two specialist/physical-therapy listings
that retain clinic supply. These are reviewer recommendations based on the
supplied fields, not externally established facts about the businesses. The
notes identify the specific conflict for owner resolution.

The 35 unresolved cases need additional evidence: individual clinicians or
pharmacists versus distinct facilities, specialty/wholesale/variety stores versus
ordinary public access, consumer branch scope, served school ages, and a clinic
tag on an elementary-school listing. The sample has no address or capacity field
with which to deduplicate practitioners sharing a facility. Person 4 has not
invented a capacity rule or changed the taxonomy.

The verifier accepts the review through `--category-review`. It rejects missing
or duplicate sample keys, changed source fields, stale source hashes, unsupported
verdicts and empty notes. Rejected mappings keep release acceptance failed; owner
resolution and fresh frozen artifacts are required before rechecking.
