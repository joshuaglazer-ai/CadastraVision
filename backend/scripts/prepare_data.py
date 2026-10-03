"""Put the required files in place and build the layer index ahead of time.

    python -m backend.scripts.prepare_data
    python -m backend.scripts.prepare_data --from ~/Downloads

The API builds the same index on first start; running this beforehand
simply moves the half-minute wait out of the first page load.

``--from <folder>`` first looks in that folder for the checkpoint and the two
existing layers, including browser download names such as
``candidate_parcels (2).geojson``, and copies them to the locations the
backend expects under their clean names. A file is never chosen by guesswork:
when the folder holds several differing copies and none under the clean
name, nothing is copied for that file and the copies are listed.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from backend.config import (
    LANDCOVER_FILE_NAME,
    MODEL_FILE_NAME,
    PARCELS_FILE_NAME,
    Settings,
    display_path,
    settings,
)
from backend.core import runtime


@dataclass
class ImportResult:
    label: str
    destination: Path
    status: str  # copied | unchanged | not_found | ambiguous
    source: Path | None = None
    candidates: tuple[Path, ...] = ()

    def line(self) -> str:
        target = display_path(self.destination)
        if self.status == "copied":
            return f"copied     {self.source.name}  ->  {target}"
        if self.status == "unchanged":
            return f"unchanged  {target} is already identical to {self.source.name}"
        if self.status == "ambiguous":
            names = ", ".join(path.name for path in self.candidates)
            return (
                f"NOT COPIED {self.label}: several different files match ({names}). "
                f"Rename the right one to {self.destination.name} and run again."
            )
        return f"NOT FOUND  {self.label}: no file like {self.destination.name}"


def _pattern(clean_name: str) -> re.Pattern[str]:
    """``candidate_parcels.geojson`` also matches ``candidate_parcels (2).geojson``,
    ``candidate_parcels(2).geojson`` and ``candidate_parcels-1.geojson``."""

    stem, suffix = clean_name.rsplit(".", 1)
    return re.compile(
        rf"^{re.escape(stem)}\s*(?:\(\d+\)|[-_ ]\d+)?\.{re.escape(suffix)}$", re.IGNORECASE
    )


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def _same_file(a: Path, b: Path) -> bool:
    return a.stat().st_size == b.stat().st_size and _digest(a) == _digest(b)


def targets(config: Settings) -> list[tuple[str, Path]]:
    return [
        ("model checkpoint", config.configured_model_path.parent / MODEL_FILE_NAME),
        ("candidate parcels", config.data_dir / "parcels" / PARCELS_FILE_NAME),
        ("land-cover layer", config.data_dir / "landcover" / LANDCOVER_FILE_NAME),
    ]


def pick(folder: Path, clean_name: str) -> tuple[Path | None, tuple[Path, ...]]:
    """The file to import for ``clean_name``, and every file that matched.

    The clean name wins. Otherwise a single match is used, or several matches
    that are byte-for-byte identical (repeated downloads of the same file).
    Several differing matches return ``None``.
    """

    pattern = _pattern(clean_name)
    matches = tuple(
        sorted(
            (p for p in folder.iterdir() if p.is_file() and pattern.match(p.name)),
            key=lambda p: p.name.lower(),
        )
    )
    for match in matches:
        if match.name.lower() == clean_name.lower():
            return match, matches
    if len(matches) == 1:
        return matches[0], matches
    if matches and all(_same_file(matches[0], other) for other in matches[1:]):
        return matches[0], matches
    return None, matches


def import_files(folder: Path, config: Settings = settings) -> list[ImportResult]:
    """Copy the required files from ``folder`` to where the backend expects them."""

    folder = Path(folder).expanduser()
    if not folder.is_dir():
        raise NotADirectoryError(f"{folder} is not a folder")

    results: list[ImportResult] = []
    for label, destination in targets(config):
        source, matches = pick(folder, destination.name)
        if source is None:
            status = "ambiguous" if matches else "not_found"
            results.append(ImportResult(label, destination, status, candidates=matches))
            continue
        if destination.exists() and _same_file(source, destination):
            results.append(ImportResult(label, destination, "unchanged", source, matches))
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".part")
        shutil.copy2(source, partial)
        partial.replace(destination)  # a half-copied file is never read as the layer
        results.append(ImportResult(label, destination, "copied", source, matches))
    return results


def build_index() -> None:
    settings.ensure_dirs()
    started = time.time()
    indexed = runtime.ensure_source(runtime.EXISTING_SOURCE)
    layers = runtime.get_layers()
    for kind, record in indexed.items():
        if record is None:
            print(f"{kind:10s} unavailable: {runtime.unavailable_reason(kind)}")
            continue
        stats = layers.stats(runtime.EXISTING_SOURCE, kind)
        print(
            f"{kind:10s} {record['feature_count']:>7,} features  "
            f"{stats['totals']['area_m2'] / 10000:>8.2f} ha  CRS {record['crs']}  "
            f"skipped {record['skipped_count']}  ({Path(record['path']).name})"
        )
        for entry in stats["classes"].values():
            print(
                f"    {entry['class_name']:10s} {entry['count']:>6,}  {entry['area_m2']:>12,.1f} m²  "
                f"fragments {entry['fragments']:,}"
            )
    model = runtime.data_files()["model"]
    print(f"model      {model['message'] or 'present: ' + model['used']}")
    for key, message in runtime.bootstrap_errors().items():
        print(f"ERROR {key}: {message}")
    print(f"Done in {time.time() - started:.1f} s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--from",
        dest="source",
        metavar="FOLDER",
        help="copy the checkpoint and the two existing layers from this folder first",
    )
    args = parser.parse_args(argv)

    missing = False
    if args.source:
        try:
            results = import_files(Path(args.source), settings)
        except NotADirectoryError as exc:
            print(f"ERROR {exc}")
            return 2
        for result in results:
            print(result.line())
        missing = any(r.status in ("not_found", "ambiguous") for r in results)
        print()

    build_index()
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
