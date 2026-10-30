"""Read Atmosphere crash reports and explain which kind of thing crashed, then flag active add-ons."""
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .repair import active_addons, disabled_moves
from .verify import INFO, WARN, Finding

REPORT_DIRS = ("atmosphere/crash_reports", "atmosphere/fatal_errors", "atmosphere/fatal_reports")
_PID = re.compile(r"Program ID:\s*(?:0x)?([0-9A-Fa-f]{16})")
_NAME = re.compile(r"Process Name:\s*(.+)")
_ERROR = re.compile(r"Error (?:Desc(?:ription)?|Code):\s*(.+)")
SYSTEM_RANGE = (0x0100000000000000, 0x01000000000007FF)  # system modules by Switch title-id convention


@dataclass(frozen=True)
class Report:
    path: str
    mtime: float
    program_id: str = None
    name: str = None
    error: str = None


def read_reports(root, limit=5):
    """Newest text reports from the crash and fatal-error folders (binary files are ignored)."""
    root = Path(root)
    found = []
    for rel in REPORT_DIRS:
        d = root / rel
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if p.is_file() and p.suffix.lower() in (".log", ".txt"):
                found.append(p)
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in found[:limit]:
        text = p.read_bytes()[:8192].decode("utf-8", "replace")
        pid, name, err = _PID.search(text), _NAME.search(text), _ERROR.search(text)
        out.append(Report(p.relative_to(root).as_posix(), p.stat().st_mtime,
                          pid.group(1).upper() if pid else None,
                          name.group(1).strip() if name else None,
                          err.group(1).strip() if err else None))
    return out


def classify(report, root):
    """system | addon | app | unknown"""
    if not report.program_id:
        return "unknown"
    contents = Path(root) / "atmosphere" / "contents"
    if contents.is_dir() and any(p.name.upper() == report.program_id for p in contents.iterdir()):
        return "addon"
    pid = int(report.program_id, 16)
    if SYSTEM_RANGE[0] <= pid <= SYSTEM_RANGE[1]:
        return "system"
    return "app"


def check_reports(root):
    out, seen = [], set()
    for r in read_reports(root):
        kind = classify(r, root)
        key = (kind, r.program_id)
        if key in seen:
            continue
        seen.add(key)
        when = datetime.fromtimestamp(r.mtime).strftime("%Y-%m-%d %H:%M")
        who = r.name or r.program_id or r.path
        extra = f" ({r.error})" if r.error else ""
        if kind == "system":
            out.append(Finding(WARN, "report-system-crash", f"{when}: system module {who} crashed{extra}",
                               "System modules usually crash when Atmosphere does not match the console firmware. "
                               "Run `sdsetup update` to get the newest Atmosphere and hekate, and check the firmware with `doctor --firmware`."))
        elif kind == "addon":
            out.append(Finding(WARN, "report-addon-crash",
                               f"{when}: add-on {who} (atmosphere/contents/{r.program_id}) crashed{extra}",
                               "Update that add-on, or run `sdsetup repair --clean-boot` to test without add-ons."))
        elif kind == "app":
            out.append(Finding(INFO, "report-app-crash", f"{when}: a game or app ({who}) crashed{extra}",
                               "That is usually the game or its mods, not the card setup."))
        else:
            out.append(Finding(INFO, "report-unknown", f"{when}: report {r.path} could not be read for a program id"))
    return out


def check_addons(root):
    out = []
    moves = disabled_moves(root)
    if moves:
        names = ", ".join(m["from"] for m in moves)
        out.append(Finding(WARN, "addons-disabled", f"add-ons are switched off by a clean-boot test: {names}",
                           "Switch them back on with `sdsetup repair --restore` once the test is done."))
    active = active_addons(root)
    if active:
        parts = "; ".join(f"{rel} ({len(names)})" for rel, names in active.items())
        out.append(Finding(INFO, "addons-active", f"third-party add-ons active: {parts}",
                           "If the console stops booting after an update, `sdsetup repair --clean-boot` tests without them."))
    return out
