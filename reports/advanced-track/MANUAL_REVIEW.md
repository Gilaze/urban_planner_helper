# Manual sample-review evidence

The team confirmed in the project conversation on 2026-11-15 that they personally reviewed the category sample. This records that confirmation; it does not claim independent observation of who reviewed each row or external verification of businesses.

The reproducible audit contains 160 distinct places: 20 for each of five candidate services, 20 ambiguous listings, 20 clinic lookalikes and 20 grocery lookalikes. Annotated row-level evidence and notes are committed at `reports/verification/category-review.csv`; source-sample SHA256 is `430ddabcb72d04c4cdbd39ce64fca346ba870538ccada4148470ceaa80bd124f`.

The checker annotations are 80 accepted, 45 rejected, 35 uncertain. The Data response documents removal of 45 rejected supply mappings; 34 cases await owner evidence and one changed case needs re-review. The original audit is selected, not a random sample, so its reject fraction is not a population error estimate. Do not present uncertain rows as verified facilities or claim all category issues were resolved.

Data decisions checked include primary versus secondary category tags, specialty/credential exclusions, grocery versus convenience stores, K–12 versus college scope, individual providers versus facilities, and duplicate-capacity uncertainty. Source names/categories support consistency checks; they do not prove current operation, public access, distinct facilities or capacity. No new labels or taxonomy changes were made after the final geographic holdout results.

For the handoff, include this record together with the actual annotated CSV and Data responses. A judge can request the team's explanation of their review; this document does not replace that explanation.
