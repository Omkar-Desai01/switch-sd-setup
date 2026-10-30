import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from sdsetup import backup, format as fmt
from sdsetup.backup import BackupError, backup_card, check_backup
from sdsetup.drives import DriveError, get_drive, list_drives, normalize_letter, removable_drives, safety_problems
from sdsetup.format import FormatError, build_command, confirmation_phrase, format_drive

GB = 10**9


class FakeApi:
    def __init__(self, drives):
        # letter -> (type, label, serial, fs, total, free) ; None volume info = empty slot
        self.drives = drives

    def letters(self):
        return sorted(self.drives)

    def drive_type(self, l):
        return self.drives[l][0]

    def volume_info(self, l):
        d = self.drives[l]
        return None if d[1] is None else (d[1], d[2], d[3])

    def disk_space(self, l):
        return self.drives[l][4], self.drives[l][5]

    def system_drive(self):
        return "C"


def api(**over):
    d = {
        "C": (3, "Windows", 111, "NTFS", 500 * GB, 100 * GB),
        "D": (3, "Data", 222, "NTFS", 1000 * GB, 400 * GB),
        "E": (2, "SWITCH", 333, "exFAT", 64 * GB, 30 * GB),
        "F": (2, None, 0, "", 0, 0),  # empty reader slot
    }
    d.update(over)
    return FakeApi(d)


class DriveTests(unittest.TestCase):
    def test_listing_skips_empty_slot_and_classifies(self):
        drives = {d.letter: d for d in list_drives(api())}
        self.assertEqual(set(drives), {"C", "D", "E"})
        self.assertEqual(drives["E"].kind, "removable")
        self.assertEqual([d.letter for d in removable_drives(api())], ["E"])

    def test_normalize_letter(self):
        self.assertEqual(normalize_letter("e:"), "E")
        self.assertEqual(normalize_letter("E:\\"), "E")
        for bad in ("", "EE", "E:\\x", "1:", "E; rm"):
            with self.assertRaises(DriveError, msg=bad):
                normalize_letter(bad)

    def test_safety(self):
        a = api()
        self.assertEqual(safety_problems(get_drive("E", a), a), [])
        self.assertTrue(any("not removable" in p for p in safety_problems(get_drive("D", a), a)))
        self.assertTrue(any("system drive" in p for p in safety_problems(get_drive("C", a), a)))
        # a removable drive that is somehow the system drive
        sysrem = api(C=(2, "X", 1, "NTFS", 10 * GB, 1 * GB))
        self.assertTrue(any("system drive" in p for p in safety_problems(get_drive("C", sysrem), sysrem)))
        self.assertTrue(any("holds" in p for p in safety_problems(get_drive("E", a), a, ["E:\\tool\\cache"])))
        huge = api(E=(2, "BIG", 5, "NTFS", 4 * 1024**4, 0))
        self.assertTrue(any("TB" in p for p in safety_problems(get_drive("E", huge), huge)))

    def test_missing_drive(self):
        with self.assertRaises(DriveError):
            get_drive("Z", api())


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.card = self.d / "card"
        (self.card / "atmosphere").mkdir(parents=True)
        (self.card / "System Volume Information").mkdir()
        (self.card / "System Volume Information" / "junk").write_bytes(b"x")
        (self.card / "atmosphere" / "package3").write_bytes(b"p3")
        (self.card / "emuMMC").mkdir()
        (self.card / "emuMMC" / "emummc.ini").write_text("[emummc]\nenabled=1\n")
        (self.card / "empty").mkdir()
        self.drive = get_drive("E", api())

    def run_backup(self, **kw):
        return backup_card(self.card, self.d / "out", drive=self.drive, **kw)

    def test_copies_everything_but_system_dirs(self):
        res = self.run_backup(verify_hash=True)
        self.assertTrue(res.ok)
        self.assertEqual(res.files, 2)
        self.assertEqual((res.path / "atmosphere" / "package3").read_bytes(), b"p3")
        self.assertTrue((res.path / "empty").is_dir())
        self.assertFalse((res.path / "System Volume Information").exists())
        self.assertEqual(check_backup(res.path, self.drive), [])

    def test_destination_on_card_rejected(self):
        with self.assertRaises(BackupError):
            backup_card(self.card, self.card / "bk", drive=self.drive)

    def test_missing_source(self):
        with self.assertRaises(BackupError):
            backup_card(self.d / "nope", self.d / "out")

    def test_not_enough_space(self):
        real = backup.shutil.disk_usage
        backup.shutil.disk_usage = lambda p: SimpleNamespace(free=1)
        self.addCleanup(setattr, backup.shutil, "disk_usage", real)
        with self.assertRaisesRegex(BackupError, "not enough space"):
            self.run_backup()

    def test_failed_copy_marks_backup_bad(self):
        real = backup.shutil.copy2

        def flaky(s, t):
            if str(s).endswith("package3"):
                raise OSError("device error")
            return real(s, t)
        backup.shutil.copy2 = flaky
        self.addCleanup(setattr, backup.shutil, "copy2", real)
        res = self.run_backup()
        self.assertFalse(res.ok)
        self.assertIn("backup finished with errors", check_backup(res.path, self.drive))

    def test_check_backup_gates(self):
        res = self.run_backup()
        other = get_drive("E", api(E=(2, "OTHER", 999, "exFAT", 64 * GB, 30 * GB)))
        self.assertTrue(any("different card" in p for p in check_backup(res.path, other)))
        later = time.time() + 25 * 3600
        self.assertTrue(any("old" in p for p in check_backup(res.path, self.drive, now=later)))
        self.assertTrue(any("no backup marker" in p for p in check_backup(self.d, self.drive)))
        (res.path / backup.MARKER).write_text("{not json")
        self.assertTrue(any("unreadable" in p for p in check_backup(res.path, self.drive)))


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.api = api()
        (self.d / "card").mkdir()
        self.drive = get_drive("E", self.api)
        self.bk = backup_card(self.d / "card", self.d / "out", drive=self.drive).path
        self.calls = []

    def runner(self, cmd, **kw):
        self.calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fmt(self, letter="E", fs="exfat", label="SWITCH", **kw):
        kw.setdefault("confirmation", confirmation_phrase(get_drive(letter, self.api)))
        kw.setdefault("backup_dir", self.bk)
        kw.setdefault("admin", True)
        return format_drive(letter, fs, label, api=self.api, runner=self.runner, **kw)

    def test_happy_path_runs_once(self):
        cmd = self.fmt()
        self.assertEqual(self.calls, [cmd])
        self.assertIn("Format-Volume -DriveLetter E -FileSystem exFAT", cmd[-1])

    def test_dry_run_never_runs(self):
        self.fmt(dry_run=True, admin=False)
        self.assertEqual(self.calls, [])

    def test_refuses_fixed_and_system_drives(self):
        for letter in ("D", "C"):
            with self.assertRaisesRegex(FormatError, "refusing"):
                self.fmt(letter=letter, confirmation="ERASE %s:" % letter, skip_backup=True)
        self.assertEqual(self.calls, [])

    def test_wrong_confirmation(self):
        for bad in ("", "erase e:", "ERASE E", "ERASE D:", "yes"):
            with self.assertRaisesRegex(FormatError, "confirmation"):
                self.fmt(confirmation=bad)
        self.assertEqual(self.calls, [])

    def test_backup_required(self):
        with self.assertRaisesRegex(FormatError, "backup-dir"):
            self.fmt(backup_dir=None)
        with self.assertRaisesRegex(FormatError, "refusing"):
            self.fmt(backup_dir=self.d)  # no marker
        self.fmt(backup_dir=None, skip_backup=True)
        self.assertEqual(len(self.calls), 1)

    def test_backup_from_other_card_rejected(self):
        self.api = api(E=(2, "SWITCH", 444, "exFAT", 64 * GB, 30 * GB))  # new serial
        with self.assertRaisesRegex(FormatError, "different card"):
            self.fmt()
        self.assertEqual(self.calls, [])

    def test_fat32_limit(self):
        with self.assertRaisesRegex(FormatError, "32 GB"):
            self.fmt(fs="fat32")
        self.api = api(E=(2, "SD", 333, "exFAT", 16 * GB, 1 * GB))
        self.bk = backup_card(self.d / "card", self.d / "out2", drive=get_drive("E", self.api)).path
        self.fmt(fs="fat32")

    def test_needs_admin_only_for_live_run(self):
        with self.assertRaisesRegex(FormatError, "administrator"):
            self.fmt(admin=False)
        self.assertEqual(self.calls, [])

    def test_failure_reported(self):
        self.runner = lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="access denied")
        with self.assertRaisesRegex(FormatError, "access denied"):
            self.fmt()

    def test_swap_between_check_and_format_aborts(self):
        real_get, seen = fmt.get_drive, []

        def swapping(letter, a=None):
            seen.append(1)
            if len(seen) == 2:  # the pre-format re-read sees a different card
                self.api = api(E=(2, "SWITCH", 777, "exFAT", 64 * GB, 30 * GB))
            return real_get(letter, self.api)
        fmt.get_drive = swapping
        self.addCleanup(setattr, fmt, "get_drive", real_get)
        with self.assertRaisesRegex(FormatError, "changed"):
            self.fmt()
        self.assertEqual(self.calls, [])

    def test_command_injection_blocked(self):
        for label in ("A'; Remove-Item C:\\ -Recurse #", "TOO LONG LABEL!", "a`b"):
            with self.assertRaises(FormatError, msg=label):
                build_command("E", "exfat", label)
        with self.assertRaises(DriveError):
            build_command("E; calc", "exfat", "X")
        with self.assertRaises(FormatError):
            build_command("E", "ntfs", "X")


if __name__ == "__main__":
    unittest.main()
