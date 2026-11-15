import csv
import hashlib

import pytest

from .pipeline_artifacts import read_category_review


def write_csv(path, rows):
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def review_files(tmp_path):
    source = tmp_path / "sample.csv"
    review = tmp_path / "review.csv"
    rows = [{"audit_reason": "candidate:grocery", "place_id": "one", "primary_category": "grocery", "reviewer_verdict": ""},
            {"audit_reason": "lookalike:grocery", "place_id": "two", "primary_category": "other", "reviewer_verdict": ""}]
    write_csv(source, rows)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    reviewed = [{**row, "reviewer_verdict": verdict, "reviewer_note": note, "sample_sha256": digest}
                for row, verdict, note in zip(rows, ("accept", "needs_review"), ("Listing label matches.", "Retail service scope unknown."))]
    write_csv(review, reviewed)
    return source, review, reviewed


def test_review_keeps_data_owned_sample_unchanged(review_files):
    source, review, rows = review_files
    before = source.read_bytes()
    assert read_category_review(source, review) == rows
    assert source.read_bytes() == before


@pytest.mark.parametrize("corruption,message", [
    ("source", "different source sample"), ("mapping", "changes source evidence"),
    ("duplicate", "exactly once"), ("missing", "exactly once"),
    ("verdict", "Invalid review verdict"), ("note", "evidence note"),
])
def test_review_cannot_hide_missing_rows_or_change_its_evidence(review_files, corruption, message):
    source, review, rows = review_files
    if corruption == "source":
        source.write_text(source.read_text() + "\n")
    elif corruption == "mapping":
        rows[0]["primary_category"] = "other"
    elif corruption == "duplicate":
        rows[1] = rows[0].copy()
    elif corruption == "missing":
        rows.pop()
    elif corruption == "verdict":
        rows[0]["reviewer_verdict"] = "approved"
    elif corruption == "note":
        rows[0]["reviewer_note"] = ""
    write_csv(review, rows)
    with pytest.raises(ValueError, match=message):
        read_category_review(source, review)
