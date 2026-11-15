"""Load the team's checked-in ZIP and matching tract index without setup commands."""

import gzip
import hashlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile, gettempdir

BUNDLE = Path(__file__).resolve().parents[1] / "demo"


def available():
    return (BUNDLE / "manifest.json").is_file() and (BUNDLE / "serving-release.json.gz").is_file()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_verified(source, target, expected):
    """Publish complete, verified files atomically, including concurrent cold starts."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
            checksum = hashlib.sha256()
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
                checksum.update(chunk)
        if checksum.hexdigest() != expected:
            raise ValueError("Bundled data checksum does not match its manifest")
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def paths():
    manifest_bytes = (BUNDLE / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    version = hashlib.sha256(manifest_bytes).hexdigest()[:20]
    # Vercel's deployed filesystem is read-only; this also works on local machines.
    cache = Path(gettempdir()) / "tract-planner" / version
    release, index = cache / "release.json", cache / "planning.json"
    if not release.is_file() or digest(release) != manifest["release_sha256"]:
        compressed = BUNDLE / "serving-release.json.gz"
        if digest(compressed) != manifest["release_gzip_sha256"]:
            raise ValueError("Bundled serving release checksum does not match its manifest")
        with gzip.open(compressed, "rb") as source:
            copy_verified(source, release, manifest["release_sha256"])
    if not index.is_file() or digest(index) != manifest["index_sha256"]:
        compressed = BUNDLE / "planning-index.json.gz"
        if digest(compressed) != manifest["index_gzip_sha256"]:
            raise ValueError("Bundled tract index checksum does not match its manifest")
        with gzip.open(compressed, "rb") as source:
            copy_verified(source, index, manifest["index_sha256"])
    return release, index
