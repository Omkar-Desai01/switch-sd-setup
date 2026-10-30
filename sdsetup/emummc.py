import json
import re
import struct
import subprocess
from pathlib import Path

from .drives import normalize_letter
from .nand import BOOT_SIZE, GPP_SIZE
from .verify import ERROR, INFO, WARN, Finding

SECTOR_OFFSET = 0x8000  # hekate's protective offset: emuMMC sector = partition start + 0x8000
SECTOR_BYTES = 512
MIN_PARTITION_BYTES = GPP_SIZE + 2 * BOOT_SIZE


def parse_emummc_ini(text):
    values, in_section = {}, False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_section = line.lower() == "[emummc]"
        elif in_section and "=" in line and not line.startswith((";", "#")):
            k, v = line.split("=", 1)
            values[k.strip().lower()] = v.strip()
    return values


def windows_partitions(letter, runner=subprocess.run):
    """Partition table of the disk holding drive `letter`, via PowerShell. Returns [{'offset','size','mbr_type'}]."""
    letter = normalize_letter(letter)
    script = (
        f"$n=(Get-Partition -DriveLetter {letter}).DiskNumber; "
        "Get-Partition -DiskNumber $n | Select-Object PartitionNumber,Offset,Size,MbrType | ConvertTo-Json -Compress"
    )
    res = runner(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                 capture_output=True, text=True)
    if res.returncode != 0 or not res.stdout.strip():
        raise OSError((res.stderr or "no output").strip())
    data = json.loads(res.stdout)
    if isinstance(data, dict):
        data = [data]
    return [{"offset": int(p["Offset"]), "size": int(p["Size"]), "mbr_type": p.get("MbrType")} for p in data]


def check_emummc(root, *, letter=None, partitions=None):
    """Checks of emummc/emummc.ini against the card. partitions: callable(letter) -> list, or None to skip."""
    root = Path(root)
    ini = root / "emummc" / "emummc.ini"
    if not ini.is_file():
        return [Finding(INFO, "emummc-none", "no emummc/emummc.ini: emuMMC not created yet")]
    cfg = parse_emummc_ini(ini.read_text(encoding="utf-8", errors="replace"))
    if cfg.get("enabled") != "1":
        return [Finding(INFO, "emummc-disabled", "emummc.ini has enabled=0 (hekate's 'emummcforce' still boots it)")]
    out = []
    raw_sector = cfg.get("sector", "0")
    try:
        sector = int(raw_sector, 0)
    except ValueError:
        return [Finding(ERROR, "emummc-bad-sector", f"emummc.ini sector is not a number: {raw_sector!r}")]
    path = cfg.get("path", "")

    if sector:
        if path:
            rb = root / path / "raw_based"
            if rb.is_file():
                data = rb.read_bytes()
                if len(data) != 4 or struct.unpack("<I", data)[0] != sector:
                    out.append(Finding(ERROR, "emummc-raw-based", f"{path}/raw_based does not match emummc.ini sector 0x{sector:X}",
                                       "Recreate the emuMMC from hekate, or fix emummc.ini to the sector hekate wrote."))
            else:
                out.append(Finding(WARN, "emummc-raw-based-missing", f"{path}/raw_based not found"))
        if partitions is None or not letter:
            out.append(Finding(INFO, "emummc-partition-skipped", "partition check skipped (needs a drive letter)"))
            return out
        try:
            parts = partitions(letter)
        except (OSError, ValueError) as e:
            out.append(Finding(INFO, "emummc-partition-unreadable", f"could not read the partition table: {e}"))
            return out
        match = [p for p in parts if p["offset"] // SECTOR_BYTES + SECTOR_OFFSET == sector]
        if not match:
            starts = ", ".join(f"0x{p['offset'] // SECTOR_BYTES:X}" for p in parts)
            out.append(Finding(
                ERROR, "emummc-sector-mismatch",
                f"emummc.ini sector 0x{sector:X} is not any partition start + 0x{SECTOR_OFFSET:X} (partitions start at {starts})",
                "The partition table changed after the emuMMC was made. Recreate the emuMMC in hekate, or restore the old partition layout."))
        elif match[0]["size"] < MIN_PARTITION_BYTES:
            out.append(Finding(WARN, "emummc-partition-small",
                               f"the emuMMC partition is {match[0]['size'] / 1024**3:.1f} GiB, a full NAND copy needs {MIN_PARTITION_BYTES / 1024**3:.1f} GiB"))
        else:
            out.append(Finding(INFO, "emummc-sector-ok", f"sector 0x{sector:X} = partition start + 0x{SECTOR_OFFSET:X}"))
    elif path:
        if not (root / path / "eMMC").is_dir():
            out.append(Finding(ERROR, "emummc-file-missing", f"file-based emuMMC folder {path}/eMMC is missing",
                               "Recreate the emuMMC in hekate, or restore the folder from your PC backup."))
    return out
