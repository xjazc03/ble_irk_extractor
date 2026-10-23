"""Read Identity Resolving Keys from BlueZ bond records.

BlueZ writes one INI-style ``info`` file per bonded device.  The relevant
section looks like this::

    [General]
    Name=My Phone
    Address=AA:BB:CC:DD:EE:FF

    [IdentityResolvingKey]
    Key=0123456789ABCDEF0123456789ABCDEF

The parser is deliberately forgiving: it accepts flattened copies of the
files, ``*.info`` files, direct file paths, and the original
``/var/lib/bluetooth/<adapter>/<device>/info`` layout.
"""

from __future__ import annotations

import configparser
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

_LOGGER = logging.getLogger(__name__)

_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")
_IRK_RE = re.compile(r"^[0-9A-Fa-f]{32}$")
_PLACEHOLDER_NAMES = {"", "unknown", "unknown device"}


@dataclass(frozen=True)
class IrkRecord:
    """A single IRK found in a BlueZ bond record."""

    name: str | None
    address: str | None
    irk: str
    source: str
    path: str

    @property
    def display_name(self) -> str:
        """Return a human-friendly label for this device."""
        if self.name and self.name.strip().lower() not in _PLACEHOLDER_NAMES:
            return self.name.strip()
        if self.address:
            return self.address.upper()
        return "Unknown device"


@dataclass(frozen=True)
class ScanResult:
    """Result of scanning one or more BlueZ paths."""

    records: tuple[IrkRecord, ...]
    scanned_paths: tuple[str, ...]
    missing_paths: tuple[str, ...]
    errors: tuple[str, ...]
    scanned_at: datetime


def scan_paths(paths: Iterable[str]) -> ScanResult:
    """Scan ``paths`` for BlueZ IRKs.

    This function is synchronous and intended to be called from an executor.
    Missing paths and unreadable files are reported in ``errors`` rather than
    raised, so a temporarily unavailable host path does not break the
    integration.
    """
    requested_paths = [str(path).strip() for path in paths if str(path).strip()]
    records: list[IrkRecord] = []
    scanned_paths: list[str] = []
    missing_paths: list[str] = []
    errors: list[str] = []
    seen_files: set[Path] = set()

    if not requested_paths:
        errors.append("No BlueZ source paths are configured.")

    for raw_path in requested_paths:
        path = Path(raw_path).expanduser()
        if not path.exists():
            missing_paths.append(str(path))
            continue

        scanned_paths.append(str(path))

        try:
            files = _candidate_files(path)
        except OSError as err:
            message = f"Cannot read {path}: {err}"
            _LOGGER.debug(message)
            errors.append(message)
            continue

        for info_file in files:
            try:
                resolved = info_file.resolve()
            except OSError:
                resolved = info_file
            if resolved in seen_files:
                continue
            seen_files.add(resolved)

            try:
                record = _parse_info_file(info_file, source=str(path))
            except Exception as err:  # noqa: BLE001 - one bad file must not stop the scan
                message = f"Cannot parse {info_file}: {err}"
                _LOGGER.debug(message)
                errors.append(message)
                continue

            if record is not None:
                records.append(record)

        if not files:
            _LOGGER.debug("No BlueZ info files found under %s", path)

    return ScanResult(
        records=_deduplicate(records),
        scanned_paths=tuple(scanned_paths),
        missing_paths=tuple(missing_paths),
        errors=tuple(errors),
        scanned_at=datetime.now(timezone.utc),
    )


def _candidate_files(path: Path) -> list[Path]:
    """Return files that may contain BlueZ bond records."""
    if path.is_file():
        return [path]

    files: list[Path] = []
    for root, dirs, filenames in os.walk(path, followlinks=False):
        # Skip hidden directories such as .git or cache directories.
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        for filename in filenames:
            if filename == "info" or filename.endswith(".info"):
                files.append(Path(root) / filename)

    # Friendly fallback for users who renamed files or copied a flat set of
    # records without the "info" filename.  The parser returns None for
    # anything that is not a BlueZ bond file, so unrelated files are harmless.
    if not files:
        files = [
            entry
            for entry in path.iterdir()
            if entry.is_file()
            and not entry.name.startswith(".")
            and _is_small_file(entry)
        ]

    return sorted(files)


def _is_small_file(path: Path, limit: int = 256 * 1024) -> bool:
    """Return whether a fallback candidate is small enough to inspect."""
    try:
        return path.stat().st_size <= limit
    except OSError:
        return False


def _parse_info_file(path: Path, source: str) -> IrkRecord | None:
    """Parse one BlueZ ``info`` file."""
    parser = configparser.RawConfigParser(strict=False)
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        parser.read_file(handle)

    if not parser.has_section("IdentityResolvingKey"):
        return None

    irk = _normalise_irk(
        parser.get("IdentityResolvingKey", "Key", fallback="")
    )
    if irk is None:
        return None

    name: str | None = None
    address: str | None = None
    if parser.has_section("General"):
        name = parser.get("General", "Name", fallback="").strip() or None
        address = _normalise_address(
            parser.get("General", "Address", fallback="")
        )

    derived_address = _address_from_path(path)
    address = address or derived_address
    name = name or address

    return IrkRecord(
        name=name,
        address=address,
        irk=irk,
        source=source,
        path=str(path),
    )


def _normalise_irk(value: str | None) -> str | None:
    """Return a canonical 32-character IRK, or None if invalid."""
    if not value:
        return None

    cleaned = re.sub(r"[\s:\-]", "", value.strip().strip("\"'"))
    if not _IRK_RE.fullmatch(cleaned):
        return None
    if set(cleaned.lower()) == {"0"}:
        return None
    return cleaned.upper()


def _normalise_address(value: str | None) -> str | None:
    """Return a canonical Bluetooth address, or None if invalid."""
    if not value:
        return None

    cleaned = value.strip().strip("\"'").upper()
    return cleaned if _MAC_RE.fullmatch(cleaned) else None


def _address_from_path(path: Path) -> str | None:
    """Find a Bluetooth address in the path, as used by BlueZ directories."""
    for part in reversed(path.parts):
        if _MAC_RE.fullmatch(part):
            return part.upper()
    return None


def _deduplicate(records: list[IrkRecord]) -> tuple[IrkRecord, ...]:
    """Deduplicate records by IRK, preferring records with more metadata."""
    by_irk: dict[str, IrkRecord] = {}

    for record in records:
        existing = by_irk.get(record.irk)
        if existing is None:
            by_irk[record.irk] = record
            continue

        sources = [source.strip() for source in existing.source.split(",")]
        if record.source not in sources:
            sources.append(record.source)

        by_irk[record.irk] = IrkRecord(
            name=existing.name or record.name,
            address=existing.address or record.address,
            irk=record.irk,
            source=", ".join(sources),
            path=existing.path,
        )

    return tuple(
        sorted(by_irk.values(), key=lambda record: record.display_name.lower())
    )
