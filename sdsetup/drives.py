import os
import re
import sys
from dataclasses import dataclass

DRIVE_REMOVABLE = 2
DRIVE_KINDS = {0: "unknown", 1: "no-root", 2: "removable", 3: "fixed", 4: "network", 5: "cdrom", 6: "ramdisk"}
_LETTER = re.compile(r"^[A-Za-z]:?[\\/]?$")


class DriveError(Exception):
    pass


@dataclass(frozen=True)
class Drive:
    letter: str  # upper-case, no colon
    kind: str
    label: str
    fs: str
    serial: int
    total: int
    free: int
    mount: str = ""  # overrides the drive root; lets tests point a fake drive at a folder

    @property
    def root(self):
        return self.mount or f"{self.letter}:\\"

    @property
    def used(self):
        return self.total - self.free

    def describe(self):
        gb = self.total / 1e9
        return f"{self.letter}: {self.label or '(no label)'} {self.fs or '?'} {gb:.1f} GB, {self.used / 1e9:.1f} GB used"


def normalize_letter(text):
    if not _LETTER.match(text or ""):
        raise DriveError(f"not a drive letter: {text!r}")
    return text[0].upper()


class WinApi:
    """Thin ctypes wrapper over the Win32 calls used for drive discovery."""

    def __init__(self):
        if sys.platform != "win32":
            raise DriveError("drive detection is Windows only")
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def letters(self):
        mask = self.k32.GetLogicalDrives()
        return [chr(ord("A") + i) for i in range(26) if mask & (1 << i)]

    def drive_type(self, letter):
        return self.k32.GetDriveTypeW(f"{letter}:\\")

    def volume_info(self, letter):
        ct, wt = self.ct, self.wt
        label, fs = ct.create_unicode_buffer(261), ct.create_unicode_buffer(261)
        serial = wt.DWORD()
        ok = self.k32.GetVolumeInformationW(f"{letter}:\\", label, 261, ct.byref(serial), None, None, fs, 261)
        return (label.value, serial.value, fs.value) if ok else None

    def disk_space(self, letter):
        ct = self.ct
        free, total, total_free = ct.c_ulonglong(), ct.c_ulonglong(), ct.c_ulonglong()
        ok = self.k32.GetDiskFreeSpaceExW(f"{letter}:\\", ct.byref(free), ct.byref(total), ct.byref(total_free))
        return (total.value, total_free.value) if ok else (0, 0)

    def system_drive(self):
        return (os.environ.get("SystemDrive") or "C:")[0].upper()


def list_drives(api=None):
    api = api or WinApi()
    out = []
    for letter in api.letters():
        kind = DRIVE_KINDS.get(api.drive_type(letter), "unknown")
        info = api.volume_info(letter)
        if info is None:  # empty card reader slot, unformatted media
            continue
        total, free = api.disk_space(letter)
        mount = api.mount(letter) if hasattr(api, "mount") else ""
        out.append(Drive(letter, kind, info[0], info[2], info[1], total, free, mount))
    return out


def get_drive(letter, api=None):
    letter = normalize_letter(letter)
    for d in list_drives(api):
        if d.letter == letter:
            return d
    raise DriveError(f"drive {letter}: not found or has no readable volume")


def removable_drives(api=None):
    return [d for d in list_drives(api) if d.kind == "removable"]


def safety_problems(drive, api=None, protected_paths=()):
    """Reasons a drive must not be erased or used as a card target. Empty list = acceptable."""
    api = api or WinApi()
    problems = []
    if drive.kind != "removable":
        problems.append(f"{drive.letter}: is a {drive.kind} drive, not removable")
    if drive.letter == api.system_drive():
        problems.append(f"{drive.letter}: is the Windows system drive")
    for p in protected_paths:
        if os.path.splitdrive(os.path.abspath(p))[0][:1].upper() == drive.letter:
            problems.append(f"{drive.letter}: holds {p}, which this tool needs")
    if drive.total > 2 * 1024**4:
        problems.append(f"{drive.letter}: is {drive.total / 1e12:.1f} TB, larger than any SD card")
    return problems
