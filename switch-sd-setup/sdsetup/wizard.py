import shutil
import subprocess
from pathlib import Path

from .backup import BackupError, backup_card
from .checklist import render_checklist
from .drives import DriveError, get_drive, removable_drives, safety_problems
from .fetch import FetchError
from .format import FILESYSTEMS, FAT32_MAX_BYTES, FormatError, confirmation_phrase, format_drive
from .install import InstallError, install_components
from .verify import ERROR, WARN, verify_card


class Console:
    """Terminal I/O. Tests replace this with a scripted version."""

    def say(self, text=""):
        print(text)

    def ask(self, prompt, default=""):
        suffix = f" [{default}]" if default else ""
        return input(f"{prompt}{suffix}: ").strip() or default

    def confirm(self, prompt, default=False):
        answer = self.ask(f"{prompt} ({'Y/n' if default else 'y/N'})", "").lower()
        return default if not answer else answer.startswith("y")


def _choose_drive(io, candidates):
    if len(candidates) == 1:
        d = candidates[0]
        return d if io.confirm(f"Use {d.describe()}?", True) else None
    io.say("Removable drives:")
    for n, d in enumerate(candidates, 1):
        io.say(f"  {n}. {d.describe()}")
    for _ in range(3):
        pick = io.ask("Number of the card to use (q to quit)", "")
        if pick.lower() == "q":
            return None
        if pick.isdigit() and 1 <= int(pick) <= len(candidates):
            return candidates[int(pick) - 1]
        io.say("Not a valid number.")
    return None


def _backup_step(io, drive, backup_root, dry_run, essential):
    """Returns (backup_dir or None, empty_card). Raises BackupError on failure.

    essential=True (the card is being kept): copy settings and apps, skip folders over 1 GB (games, emuMMC).
    essential=False (the card will be erased): copy everything.
    """
    if drive.used == 0:
        io.say("\nThe card is empty; nothing to back up.")
        return None, True
    if essential:
        io.say(f"\nStep 3: back up the card's settings and apps to {backup_root}")
        io.say("  (folders over 1 GB such as games and the emuMMC stay on the card and are skipped)")
    else:
        io.say(f"\nStep 3: back up the whole card ({drive.used / 1e9:.1f} GB) to {backup_root}")
    if dry_run:
        io.say("  dry run: would copy the card here.")
        return None, False
    verify_hash = io.confirm("Compare SHA-256 of every file too? Slow, but the strongest check", False)
    res = backup_card(drive.root, backup_root, drive=drive, verify_hash=verify_hash, essential=essential,
                      progress=lambda n, b, t: print(f"\r  {n} files, {b / 1e6:.0f} MB", end="", flush=True))
    print()
    if not res.ok:
        for e in res.errors[:5]:
            io.say(f"  ERROR {e}")
        raise BackupError(f"backup finished with {len(res.errors)} error(s); stopping so nothing is changed")
    io.say(f"  backup OK: {res.files} files, {res.bytes / 1e6:.0f} MB -> {res.path}")
    for name, size in res.excluded:
        io.say(f"  not copied: {name} ({size / 1e9:.1f} GB), it stays on the card")
    return res.path, False


def _format_step(io, drive, backup_dir, empty, dry_run, runner, admin, protected, api):
    """Returns False if the user should stop here."""
    io.say("\nStep 4: erase and format.")
    default_fs = "fat32" if drive.total <= FAT32_MAX_BYTES else "exfat"
    fs = io.ask(f"  filesystem ({'/'.join(sorted(FILESYSTEMS))})", default_fs).lower()
    label = io.ask("  volume label (up to 11 letters/digits)", "SWITCH SD")
    phrase = confirmation_phrase(drive)
    if dry_run:
        typed = phrase
    else:
        io.say(f"  This ERASES everything on {drive.describe()}.")
        typed = io.ask(f"  type {phrase} to continue")
    try:
        format_drive(drive.letter, fs, label, confirmation=typed, backup_dir=backup_dir,
                     skip_backup=empty, dry_run=dry_run, protected_paths=protected,
                     api=api, runner=runner, admin=admin)
    except FormatError as e:
        io.say(f"  {e}")
        return io.confirm("  Continue without formatting?", False)
    io.say("  dry run: format checks passed, nothing erased." if dry_run else "  formatted.")
    return True


def _select_components(io, manifest):
    chosen = list(manifest.select())
    io.say("\nStep 5: choose what to install.")
    for c in chosen:
        io.say(f"  included: {c.name}")
    extras = [c for c in manifest.components if c not in chosen]
    for c in extras:
        if io.confirm(f"  add {c.name}?", False):
            chosen.append(c)
    return chosen


def run_wizard(manifest, io, provider, *, api=None, backup_root=None, protected_paths=(),
               dry_run=False, runner=subprocess.run, admin=None, preselect=None):
    """Guided card setup. Returns a process exit code. preselect: drive letter chosen by a UI."""
    backup_root = Path(backup_root or Path.home() / "SwitchSD-backups")
    protected = [*protected_paths, str(backup_root)]
    io.say("Switch SD card setup" + ("  (DRY RUN: nothing will be written)" if dry_run else ""))
    io.say("\nStep 1: pick the card.")
    candidates = [d for d in removable_drives(api) if not safety_problems(d, api, protected)]
    if not candidates:
        io.say("No usable removable drive found. Insert the SD card (in a reader) and run again.")
        return 1
    if preselect:
        letter = preselect.strip(":\\/ ").upper()
        drive = next((d for d in candidates if d.letter == letter), None)
        if drive is None:
            io.say(f"{letter}: is not a usable removable drive (it must be removable, not the system drive).")
            return 1
        io.say(f"Using {drive.describe()}")
    else:
        drive = _choose_drive(io, candidates)
    if drive is None:
        io.say("Cancelled.")
        return 1

    try:
        want_format = io.confirm(
            "\nStep 2: erase and format the card first? "
            "(good for a new or unreliable card; leave it unformatted to add to or update the card)", False)
        backup_dir, empty = _backup_step(io, drive, backup_root, dry_run, essential=not want_format)
        if want_format and not _format_step(io, drive, backup_dir, empty, dry_run, runner, admin, protected, api):
            io.say("Stopped.")
            return 1
        drive = get_drive(drive.letter, api)  # a format changes the volume serial
        problems = safety_problems(drive, api, protected)
        if problems:
            raise InstallError("refusing: " + "; ".join(problems))

        comps = _select_components(io, manifest)
        io.say("\nStep 6: download and install.")
        receipt = install_components(comps, drive.root, provider, dry_run=dry_run, log=lambda m: io.say("  " + m))
        for m in manifest.manual:
            io.say(f"  manual step: {m['name']}: {m['note']}")

        payload_copy = None
        if dry_run:
            io.say("\nStep 7: verify skipped in a dry run.")
        else:
            io.say("\nStep 7: verify the card.")
            findings = verify_card(drive.root, manifest)
            for f in findings:
                io.say(f"  {f.level.upper():5} {f.code}: {f.message}")
            if any(f.level == ERROR for f in findings):
                io.say("Verification found errors; fix them before using the card.")
                return 1
            if not any(f.level == WARN for f in findings):
                io.say("  all checks passed.")
            backup_root.mkdir(parents=True, exist_ok=True)
            src = Path(drive.root) / "payload.bin"
            if src.is_file():
                payload_copy = backup_root / "hekate_payload.bin"
                shutil.copy2(src, payload_copy)
    except (BackupError, DriveError, FetchError, InstallError) as e:
        io.say(f"\nStopped: {e}")
        return 1

    text = render_checklist(None if dry_run else receipt, drive, manifest,
                            payload_path=payload_copy, backup_path=backup_dir)
    io.say("\n" + text)
    if not dry_run:
        out = backup_root / "SETUP-CHECKLIST.txt"
        out.write_text(text, encoding="utf-8")
        io.say(f"(checklist saved to {out})")
    io.say("Eject the card safely before removing it.")
    return 0
