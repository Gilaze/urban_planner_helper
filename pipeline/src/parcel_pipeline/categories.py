"""Category mapping from Google Places labels to the 15 Parcel Planner groups."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from .settings import CONFIG_DIR

OTHER = "other"


def _norm(label: str) -> str:
    return re.sub(r"\s+", " ", str(label)).strip().casefold()


@dataclass(frozen=True)
class CategoryConfig:
    version: str
    groups: tuple[str, ...]
    candidates: tuple[str, ...]
    labels: dict[str, str]  # group id -> display label
    label_to_group: dict[str, str]  # normalized source label -> group id
    exclusions: dict[str, tuple[re.Pattern, ...]] = field(default_factory=dict)

    def group_of(self, label: object) -> str:
        if not isinstance(label, str) or not label.strip():
            return OTHER
        return self.label_to_group.get(_norm(label), OTHER)

    def excluded(self, group: str, name: object) -> bool:
        if not isinstance(name, str):
            return False
        return any(p.search(name) for p in self.exclusions.get(group, ()))

    def classify(self, category: object, all_categories: object, name: object) -> tuple[str, list[str]]:
        """Return (primary_group, sorted deduplicated service tags excluding `other`)."""
        primary = self.group_of(category)
        if primary != OTHER and self.excluded(primary, name):
            primary = OTHER
        tags = set()
        if primary != OTHER:
            tags.add(primary)
        for lab in parse_all_categories(all_categories):
            g = self.group_of(lab)
            if g != OTHER and not self.excluded(g, name):
                tags.add(g)
        return primary, sorted(tags)


def parse_all_categories(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [v.strip() for v in value.strip("[]").split(",") if v.strip()]
    return [str(v) for v in parsed] if isinstance(parsed, list) else []


def load_categories(path: Path | None = None) -> CategoryConfig:
    path = path or CONFIG_DIR / "categories.yaml"
    return _load(str(path))


@lru_cache(maxsize=4)
def _load(path: str) -> CategoryConfig:
    raw = yaml.safe_load(Path(path).read_text())
    groups = tuple(raw["groups"].keys())
    label_to_group: dict[str, str] = {}
    exclusions: dict[str, tuple[re.Pattern, ...]] = {}
    for gid, spec in raw["groups"].items():
        for lab in spec.get("labels") or []:
            key = _norm(lab)
            if key in label_to_group and label_to_group[key] != gid:
                raise ValueError(f"label {lab!r} assigned to both {label_to_group[key]} and {gid}")
            label_to_group[key] = gid
        exclusions[gid] = tuple(re.compile(p, re.I) for p in spec.get("exclude_name_patterns") or [])
    candidates = tuple(raw["candidates"])
    if len(groups) != 15 or OTHER not in groups or not set(candidates) <= set(groups):
        raise ValueError("categories.yaml must define 15 groups including 'other' and all candidates")
    return CategoryConfig(
        version=str(raw["version"]),
        groups=groups,
        candidates=candidates,
        labels={g: s.get("label", g) for g, s in raw["groups"].items()},
        label_to_group=label_to_group,
        exclusions=exclusions,
    )
