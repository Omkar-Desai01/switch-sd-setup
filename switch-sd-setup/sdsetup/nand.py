import re
from pathlib import Path

from .verify import ERROR, INFO, WARN, Finding

BOOT_SIZE = 0x400000  # BOOT0 and BOOT1 are 4 MiB each
GPP_SIZE = 31_268_536_320  # RAW GPP of a standard 32 GB eMMC (29.1 GiB)
_PART = re.compile(r"^rawnand\.bin(\.\d+)?$")


def _gib(n):
    return f"{n / 1024**3:.1f} GiB"


def find_backup_sets(root):
    """Backup sets under root: root itself if it holds BOOT0/rawnand, else each such subfolder."""
    root = Path(root)
    if not root.is_dir():
        return []

    def looks_like_set(p):
        return (p / "BOOT0").exists() or any(_PART.match(f.name) for f in p.iterdir() if f.is_file())

    if looks_like_set(root):
        return [root]
    return sorted(p for p in root.iterdir() if p.is_dir() and looks_like_set(p))


def check_backup_set(path, label):
    path = Path(path)
    out = []
    for name in ("BOOT0", "BOOT1"):
        f = path / name
        if not f.is_file():
            out.append(Finding(ERROR, "nand-boot-missing", f"{label}: {name} missing",
                               "Redo hekate > Tools > Backup eMMC > eMMC BOOT0 & BOOT1."))
        elif f.stat().st_size != BOOT_SIZE:
            out.append(Finding(ERROR, "nand-boot-size", f"{label}: {name} is {f.stat().st_size} bytes, expected {BOOT_SIZE}",
                               "The file is truncated; take the backup again."))
    parts = [f for f in path.iterdir() if f.is_file() and _PART.match(f.name)]
    total = sum(f.stat().st_size for f in parts)
    if (path / "partial.idx").exists():
        out.append(Finding(WARN, "nand-partial", f"{label}: partial RAW GPP backup in progress ({_gib(total)} so far)",
                           "Copy these parts to the PC, delete them from the card, and run eMMC RAW GPP again until it finishes."))
    elif not parts:
        out.append(Finding(ERROR, "nand-no-rawgpp", f"{label}: only BOOT0/BOOT1, no RAW GPP",
                           "Run hekate > Tools > Backup eMMC > eMMC RAW GPP. It needs about 30 GB free (exFAT) on the card."))
    elif total < GPP_SIZE:
        out.append(Finding(ERROR, "nand-rawgpp-short", f"{label}: RAW GPP is {_gib(total)}, a full one is at least {_gib(GPP_SIZE)}",
                           "The backup is incomplete (card ran out of space?). Free space or use a bigger card and redo it."))
    if not any(f.level == ERROR for f in out) and not (path / "partial.idx").exists():
        out.append(Finding(INFO, "nand-ok", f"{label}: BOOT0, BOOT1 and RAW GPP ({_gib(total)}) present"))
    return out


def check_nand_backups(root, label):
    sets = find_backup_sets(root)
    if not sets:
        return [Finding(WARN, "nand-none", f"{label}: no NAND backup found in {root}",
                        "Back up the console NAND from hekate before changing anything else.")]
    out = []
    for s in sets:
        out += check_backup_set(s, f"{label}/{s.name}" if s != Path(root) else label)
    return out
