import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .fetch import sha256_file

MARKER = ".sdsetup-backup.json"
SKIP_DIRS = {"system volume information", "$recycle.bin"}
MAX_BACKUP_AGE_HOURS = 24
ESSENTIAL_MAX_BYTES = 1_000_000_000  # essential mode skips top-level folders and files bigger than this


class BackupError(Exception):
    pass


@dataclass
class BackupResult:
    path: Path
    files: int = 0
    bytes: int = 0
    errors: list = field(default_factory=list)
    mode: str = "full"
    excluded: list = field(default_factory=list)  # [(name, size)] left out in essential mode

    @property
    def ok(self):
        return not self.errors


def _is_inside(child, parent):
    child, parent = Path(os.path.abspath(child)), Path(os.path.abspath(parent))
    return parent == child or parent in child.parents


def _dir_size(path):
    total = 0
    for dirpath, _, names in os.walk(path):
        for n in names:
            try:
                total += (Path(dirpath) / n).stat().st_size
            except OSError:
                pass
    return total


def _plan(src, essential, max_bytes):
    """Folders and files to copy, plus what essential mode leaves out."""
    dirs, files, excluded = [], [], []
    for dirpath, dirnames, filenames in os.walk(src):
        d = Path(dirpath)
        keep = []
        for name in dirnames:
            if name.lower() in SKIP_DIRS:
                continue
            if essential and d == src:
                size = _dir_size(d / name)
                if size > max_bytes:
                    excluded.append((name + "/", size))
                    continue
            keep.append(name)
        dirnames[:] = keep
        dirs.append(d)
        for n in filenames:
            p = d / n
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if essential and size > max_bytes:
                excluded.append((str(p.relative_to(src)), size))
                continue
            files.append((p, size))
    return dirs, files, excluded


def backup_card(src, dest_root, *, drive=None, verify_hash=False, now=None, progress=None,
                essential=False, max_bytes=ESSENTIAL_MAX_BYTES):
    """Copy the card to dest_root/<name>_<timestamp> and verify the copy.

    full: every file. essential: skips top-level folders and files over max_bytes (games, emuMMC images,
    NAND dumps), which is enough to undo an update but NOT enough to justify a format.
    Writes a marker with the card's volume serial and the mode, so a later format can check it.
    """
    src = Path(src)
    if not src.is_dir():
        raise BackupError(f"source is not a directory: {src}")
    dest_root = Path(dest_root)
    if _is_inside(dest_root, src):
        raise BackupError("backup destination is on the card being backed up")

    dirs, files, excluded = _plan(src, essential, max_bytes)
    total = sum(size for _, size in files)
    dest_root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(dest_root).free
    if free < total * 1.05:
        raise BackupError(f"not enough space at {dest_root}: need {total / 1e9:.1f} GB, have {free / 1e9:.1f} GB")

    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    name = (drive.label if drive and drive.label else "SDCARD").replace(" ", "_")
    dest = dest_root / f"{name}_{stamp}"
    dest.mkdir(parents=True)
    result = BackupResult(dest, mode="essential" if essential else "full", excluded=excluded)

    for d in dirs:
        (dest / d.relative_to(src)).mkdir(parents=True, exist_ok=True)
    for s, size in files:
        t = dest / s.relative_to(src)
        try:
            shutil.copy2(s, t)
            if t.stat().st_size != size:
                raise OSError(f"size differs after copy ({t.stat().st_size} != {size})")
            if verify_hash and sha256_file(s) != sha256_file(t):
                raise OSError("sha256 differs after copy")
        except OSError as e:
            result.errors.append(f"{s}: {e}")
            continue
        result.files += 1
        result.bytes += size
        if progress:
            progress(result.files, result.bytes, total)

    marker = {
        "source_letter": drive.letter if drive else None,
        "source_serial": drive.serial if drive else None,
        "source_total": drive.total if drive else None,
        "finished": time.time(),
        "files": result.files,
        "bytes": result.bytes,
        "hash_verified": bool(verify_hash),
        "mode": result.mode,
        "excluded": [[n, s] for n, s in excluded],
        "ok": result.ok,
        "errors": result.errors[:50],
    }
    (dest / MARKER).write_text(json.dumps(marker, indent=2), encoding="utf-8")
    return result


def check_backup(backup_dir, drive, *, now=None, max_age_hours=MAX_BACKUP_AGE_HOURS):
    """Return a list of reasons this backup does not justify erasing `drive` (empty = good)."""
    path = Path(backup_dir) / MARKER
    if not path.is_file():
        return [f"no backup marker in {backup_dir} (run the backup command first)"]
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"backup marker unreadable: {e}"]
    problems = []
    if not m.get("ok"):
        problems.append("backup finished with errors")
    if m.get("mode") == "essential":
        skipped = ", ".join(n for n, _ in m.get("excluded", [])[:5]) or "large folders"
        problems.append(f"backup is partial (skipped {skipped}); a format needs a full backup")
    if m.get("source_serial") != drive.serial:
        problems.append("backup was taken from a different card (volume serial differs)")
    age = ((now if now is not None else time.time()) - m.get("finished", 0)) / 3600
    if age > max_age_hours:
        problems.append(f"backup is {age:.0f} h old, limit is {max_age_hours} h; take a fresh one")
    return problems
