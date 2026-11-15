from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Check:
    id: str
    status: str
    owner: str
    detail: str
    reproduction: str
    expected: str
    impact: str = "release blocker"
    categories: list[str] = field(default_factory=list)


class Report:
    def __init__(self, mode="release"):
        self.mode = mode
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.checks: list[Check] = []

    def add(self, id, status, owner, detail, reproduction, expected, impact="release blocker", categories=()):
        if status not in {"PASS", "FAIL", "BLOCKED", "WARN"}:
            raise ValueError(f"Unknown check status: {status}")
        self.checks.append(Check(id, status, owner, str(detail), reproduction, expected, impact, list(categories)))

    @property
    def failed(self):
        return any(c.status in {"FAIL", "BLOCKED"} for c in self.checks)

    def write(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": self.created_at, "mode": self.mode,
            "release_accepted": self.mode == "release" and bool(self.checks) and not self.failed,
            "checks": [asdict(c) for c in self.checks],
        }
        (directory / "results.json").write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
        counts = {s: sum(c.status == s for c in self.checks) for s in ("PASS", "FAIL", "BLOCKED", "WARN")}
        lines = ["# Parcel Planner verification", "", f"Generated: {self.created_at}", "",
                 f"Mode: **{self.mode}**. Release accepted: **{payload['release_accepted']}**.", "",
                 ", ".join(f"{count} {status}" for status, count in counts.items()) + ".", ""]
        if self.mode == "fixture":
            lines += ["Synthetic checker fixtures only; this does not validate an application or real data release.", ""]
        for c in self.checks:
            lines += [f"## {c.status}: {c.id}", "", f"Owner: {c.owner}. Impact: {c.impact}.", "",
                      c.detail, "", f"Reproduce: `{c.reproduction}`", "", f"Expected: {c.expected}", ""]
            if c.categories:
                lines += ["Affected categories: " + ", ".join(c.categories) + ".", ""]
        (directory / "acceptance.md").write_text("\n".join(lines))
        return payload
