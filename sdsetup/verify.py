import configparser
import re
from dataclasses import dataclass
from pathlib import Path

from .fetch import sha256_file
from .install import load_receipt

ERROR, WARN, INFO = "error", "warn", "info"


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    message: str
    hint: str = ""


def _check_layout(root, manifest):
    out = []
    for d in manifest.dirs:
        if not (root / d).is_dir():
            out.append(Finding(ERROR, "missing-dir", f"missing folder: {d}"))
    for f in manifest.files:
        if not (root / f).is_file():
            out.append(Finding(ERROR, "missing-file", f"missing file: {f}"))
    return out


def _check_receipt(root, manifest):
    receipt = load_receipt(root)
    if receipt is None:
        return [Finding(WARN, "no-receipt", "no install receipt; file hashes not checked")]
    out = []
    for cid, entry in receipt["components"].items():
        for rel, expected in entry["files"].items():
            path = root / rel
            if not path.is_file():
                out.append(Finding(ERROR, "missing-file", f"[{cid}] missing: {rel}"))
            elif sha256_file(path) != expected:
                level = INFO if rel in manifest.volatile else ERROR
                note = " (expected: rewritten by hekate)" if level == INFO else ""
                out.append(Finding(level, "hash-mismatch", f"[{cid}] differs from release {entry['tag']}: {rel}{note}"))
    return out


def _check_hekate_ini(root):
    path = root / "bootloader" / "hekate_ipl.ini"
    if not path.is_file():
        return []
    cp = configparser.ConfigParser(interpolation=None, strict=False, allow_no_value=True)
    try:
        cp.read_string(path.read_text(encoding="utf-8", errors="replace"))
    except configparser.Error as e:
        return [Finding(ERROR, "bad-ipl", f"hekate_ipl.ini unreadable: {e}")]
    out = []
    if "config" not in cp:
        out.append(Finding(ERROR, "ipl-no-config", "hekate_ipl.ini has no [config] section"))
    entries = [s for s in cp.sections() if s != "config"]
    if not entries:
        out.append(Finding(ERROR, "ipl-no-entries", "hekate_ipl.ini has no boot entries"))
    has_emu = any(cp[s].get("emummcforce") == "1" for s in entries)
    has_stock = any(cp[s].get("stock") == "1" for s in entries)
    if not has_emu:
        out.append(Finding(WARN, "ipl-no-emummc", "no boot entry with emummcforce=1"))
    if not has_stock:
        out.append(Finding(WARN, "ipl-no-stock", "no stock sysNAND boot entry (stock=1)"))
    return out


def _check_extras(root):
    out = []
    if (root / "backup").is_dir():
        out.append(Finding(WARN, "backup-on-card",
                           "backup/ is still on the card: copy it to the PC, confirm RAW GPP is included, then remove it"))
    ss = root / "atmosphere" / "config" / "system_settings.ini"
    if ss.is_file():
        text = ss.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^\s*enable_dns_mitm_debug_log\s*=\s*u8!0x1\b", text, re.M):
            out.append(Finding(WARN, "dns-debug-on", "enable_dns_mitm_debug_log is on; turn it off after testing"))
    emu = root / "emummc" / "emummc.ini"
    if emu.is_file():
        text = emu.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^enabled\s*=\s*1", text, re.M) and not re.search(r"^(sector|path)\s*=\s*\S+", text, re.M):
            out.append(Finding(ERROR, "emummc-no-location", "emummc.ini is enabled but has no sector or path"))
    return out


def verify_card(target, manifest):
    root = Path(target)
    if not root.is_dir():
        return [Finding(ERROR, "no-target", f"not a directory: {root}")]
    findings = _check_layout(root, manifest)
    findings += _check_receipt(root, manifest)
    findings += _check_hekate_ini(root)
    findings += _check_extras(root)
    return findings
