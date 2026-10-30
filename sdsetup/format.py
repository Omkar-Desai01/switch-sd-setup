import ctypes
import re
import subprocess

from .backup import check_backup
from .drives import get_drive, normalize_letter, safety_problems

FAT32_MAX_BYTES = 32 * 1024**3
FILESYSTEMS = {"fat32": "FAT32", "exfat": "exFAT"}
_LABEL = re.compile(r"^[A-Za-z0-9_ -]{0,11}$")


class FormatError(Exception):
    pass


def confirmation_phrase(drive):
    return f"ERASE {drive.letter}:"


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def build_command(letter, fs, label):
    """PowerShell argv. Letter, fs and label are validated, so nothing user-supplied reaches the shell raw."""
    letter = normalize_letter(letter)
    if fs not in FILESYSTEMS:
        raise FormatError(f"unsupported filesystem {fs!r}; choose fat32 or exfat")
    if not _LABEL.match(label):
        raise FormatError("label must be 0-11 characters: letters, digits, space, - or _")
    script = (
        f"Format-Volume -DriveLetter {letter} -FileSystem {FILESYSTEMS[fs]} "
        f"-NewFileSystemLabel '{label}' -Confirm:$false"
    )
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]


def format_drive(letter, fs, label, *, confirmation, backup_dir=None, skip_backup=False,
                 dry_run=False, protected_paths=(), api=None, runner=subprocess.run, admin=None):
    """Quick-format a removable drive after every safety gate passes. Returns the command used.

    Gates, in order: drive re-read just now; removable and not the system drive; filesystem fits;
    a fresh verified backup of this exact card (matched by volume serial) or an explicit skip;
    the exact typed confirmation phrase; administrator rights (live runs only).
    """
    drive = get_drive(letter, api)
    problems = safety_problems(drive, api, protected_paths)
    if problems:
        raise FormatError("refusing: " + "; ".join(problems))
    if fs == "fat32" and drive.total > FAT32_MAX_BYTES:
        raise FormatError(
            f"Windows will not format {drive.total / 1e9:.0f} GB as FAT32 (32 GB limit). "
            "Use exfat, or format with a third-party FAT32 tool and skip this step."
        )
    cmd = build_command(drive.letter, fs, label)

    if skip_backup:
        pass
    elif backup_dir:
        bad = check_backup(backup_dir, drive)
        if bad:
            raise FormatError("refusing: " + "; ".join(bad))
    else:
        raise FormatError("refusing: give --backup-dir with a verified backup of this card, or --skip-backup")

    if confirmation != confirmation_phrase(drive):
        raise FormatError(f"confirmation did not match; type exactly: {confirmation_phrase(drive)}")
    if dry_run:
        return cmd
    if not (is_admin() if admin is None else admin):
        raise FormatError("formatting needs an elevated (Run as administrator) terminal")

    # Last look: same volume as at the start (serial unchanged) right before the destructive call.
    again = get_drive(drive.letter, api)
    if again.serial != drive.serial or again.total != drive.total:
        raise FormatError("drive changed while preparing; aborting")
    res = runner(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise FormatError(f"format failed: {(res.stderr or res.stdout).strip()}")
    return cmd
