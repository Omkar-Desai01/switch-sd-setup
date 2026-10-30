import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from sdsetup.doctor import run_doctor
from sdsetup.install import install_components
from sdsetup.manifest import load_manifest
from sdsetup.repair import (STATE, active_addons, clean_boot, disabled_moves, restore_addons, run_clean_boot,
                            run_restore)
from sdsetup.reports import check_addons, check_reports, classify, read_reports

from test_core import StubProvider, fake_release_files
from test_wizard import Scripted

SYSTEM_ID = "0100000000000032"
ADDON_ID = "420000000000000B"
GAME_ID = "0100ABCD12345000"


def crash_text(pid, name="proc", desc="Data Abort"):
    return f"Atmosphere Crash Report (v1.5):\nReport Identifier: abc\nProcess Name: {name}\nProgram ID: {pid}\nError Desc: {desc}\n"


class AddonCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.card = Path(self.tmp.name) / "card"
        (self.card / "atmosphere" / "contents" / ADDON_ID / "flags").mkdir(parents=True)
        (self.card / "atmosphere" / "contents" / ADDON_ID / "exefs.nsp").write_bytes(b"sys")
        (self.card / "atmosphere" / "contents" / ADDON_ID / "flags" / "boot2.flag").write_bytes(b"")
        (self.card / "atmosphere" / "exefs_patches" / "fix").mkdir(parents=True)
        (self.card / "atmosphere" / "exefs_patches" / "fix" / "a.ips").write_bytes(b"ips")
        (self.card / "atmosphere" / "kip_patches").mkdir()  # empty: nothing to disable
        (self.card / "atmosphere" / "config").mkdir()
        (self.card / "atmosphere" / "config" / "system_settings.ini").write_text("[a]\n")


class CleanBootTests(AddonCase):
    def test_lists_only_non_empty_folders(self):
        self.assertEqual(set(active_addons(self.card)), {"atmosphere/contents", "atmosphere/exefs_patches"})

    def test_dry_run_changes_nothing(self):
        moves = clean_boot(self.card, dry_run=True)
        self.assertEqual(len(moves), 2)
        self.assertTrue((self.card / "atmosphere" / "contents").is_dir())
        self.assertFalse((self.card / STATE).exists())

    def test_clean_boot_renames_and_records_and_restore_undoes_it(self):
        moves = clean_boot(self.card, now=datetime(2026, 10, 29, 12, 0, 0))
        self.assertEqual(moves[0][1], "atmosphere/contents.disabled-20261029-120000")
        self.assertFalse((self.card / "atmosphere" / "contents").exists())
        self.assertTrue((self.card / "atmosphere" / "contents.disabled-20261029-120000" / ADDON_ID / "exefs.nsp").exists())
        self.assertEqual(len(disabled_moves(self.card)), 2)
        self.assertTrue((self.card / "atmosphere" / "config" / "system_settings.ini").exists())  # settings untouched

        restored, problems = restore_addons(self.card)
        self.assertEqual((len(restored), problems), (2, []))
        self.assertTrue((self.card / "atmosphere" / "contents" / ADDON_ID / "exefs.nsp").exists())
        self.assertTrue((self.card / "atmosphere" / "exefs_patches" / "fix" / "a.ips").exists())
        self.assertFalse((self.card / STATE).exists())

    def test_clean_boot_twice_does_nothing_the_second_time(self):
        clean_boot(self.card)
        self.assertEqual(clean_boot(self.card), [])
        self.assertEqual(len(disabled_moves(self.card)), 2)

    def test_restore_merges_into_a_folder_atmosphere_recreated(self):
        clean_boot(self.card)
        (self.card / "atmosphere" / "contents" / "0100000000000099").mkdir(parents=True)
        restored, problems = restore_addons(self.card)
        self.assertEqual(problems, [])
        self.assertTrue((self.card / "atmosphere" / "contents" / ADDON_ID).is_dir())
        self.assertTrue((self.card / "atmosphere" / "contents" / "0100000000000099").is_dir())
        self.assertEqual(len(restored), 2)

    def test_restore_keeps_clashing_names_and_reports_them(self):
        clean_boot(self.card)
        (self.card / "atmosphere" / "contents" / ADDON_ID).mkdir(parents=True)
        restored, problems = restore_addons(self.card)
        self.assertTrue(any(ADDON_ID in p for p in problems))
        self.assertEqual(len(restored), 1)  # exefs_patches restored; contents still has the clash
        self.assertEqual(len(disabled_moves(self.card)), 1)

    def test_restore_when_the_disabled_folder_was_deleted(self):
        clean_boot(self.card)
        for p in (self.card / "atmosphere").glob("exefs_patches.disabled-*"):
            for f in sorted(p.rglob("*"), reverse=True):
                f.rmdir() if f.is_dir() else f.unlink()
            p.rmdir()
        restored, problems = restore_addons(self.card)
        self.assertTrue(any("is gone" in p for p in problems))
        self.assertEqual(len(restored), 1)

    def test_restore_with_nothing_recorded(self):
        self.assertEqual(restore_addons(self.card), ([], []))
        io = Scripted([])
        self.assertEqual(run_restore(io, self.card), 0)
        self.assertIn("Nothing was switched off", io.text)

    def test_run_clean_boot_asks_and_explains(self):
        io = Scripted(["n"])
        self.assertEqual(run_clean_boot(io, self.card), 1)
        self.assertTrue((self.card / "atmosphere" / "contents").exists())
        self.assertIn("cheats", io.text)
        io = Scripted(["y"])
        self.assertEqual(run_clean_boot(io, self.card), 0)
        self.assertFalse((self.card / "atmosphere" / "contents").exists())
        self.assertIn("repair --restore", io.text)

    def test_run_clean_boot_with_no_addons(self):
        empty = self.card.parent / "empty"
        empty.mkdir()
        io = Scripted([])
        self.assertEqual(run_clean_boot(io, empty), 0)
        self.assertIn("No add-ons are active", io.text)

    def test_corrupt_state_file_is_ignored(self):
        (self.card / STATE).parent.mkdir(parents=True)
        (self.card / STATE).write_text("{broken")
        self.assertEqual(disabled_moves(self.card), [])


class ReportTests(AddonCase):
    def write_report(self, name, text, folder="crash_reports", age=0):
        d = self.card / "atmosphere" / folder
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_text(text)
        if age:
            t = time.time() - age
            os.utime(p, (t, t))
        return p

    def codes(self):
        return {f.code: f for f in check_reports(self.card)}

    def test_system_module_crash_points_at_atmosphere_and_firmware(self):
        self.write_report("a.log", crash_text(SYSTEM_ID, "ro", "Data Abort"))
        f = self.codes()["report-system-crash"]
        self.assertEqual(f.level, "warn")
        self.assertIn("ro", f.message)
        self.assertIn("update", f.hint)
        self.assertIn("firmware", f.hint)

    def test_addon_crash_identified_by_contents_folder(self):
        self.write_report("b.log", crash_text(ADDON_ID, "ovl", "Undefined Instruction"))
        f = self.codes()["report-addon-crash"]
        self.assertIn(f"atmosphere/contents/{ADDON_ID}", f.message)
        self.assertIn("clean-boot", f.hint)

    def test_game_crash_is_informational(self):
        self.write_report("c.log", crash_text(GAME_ID, "game"))
        f = self.codes()["report-app-crash"]
        self.assertEqual(f.level, "info")

    def test_lowercase_program_id_and_folder_match(self):
        (self.card / "atmosphere" / "contents" / ADDON_ID).rename(self.card / "atmosphere" / "contents" / ADDON_ID.lower())
        self.write_report("d.log", crash_text(ADDON_ID.lower()))
        self.assertIn("report-addon-crash", self.codes())

    def test_unreadable_and_binary_reports(self):
        self.write_report("e.log", "garbage without ids")
        (self.card / "atmosphere" / "crash_reports" / "f.bin").write_bytes(b"\x00\x01")
        self.write_report("erpt.log", crash_text(SYSTEM_ID), folder="erpt_reports")  # error reports are not crashes
        codes = self.codes()
        self.assertIn("report-unknown", codes)
        self.assertNotIn("report-system-crash", codes)

    def test_same_crash_is_listed_once_newest_first(self):
        self.write_report("old.log", crash_text(SYSTEM_ID, "ro"), age=3600)
        self.write_report("new.log", crash_text(SYSTEM_ID, "ro"))
        reports = read_reports(self.card)
        self.assertEqual(reports[0].path, "atmosphere/crash_reports/new.log")
        self.assertEqual(len([f for f in check_reports(self.card) if f.code == "report-system-crash"]), 1)

    def test_classify_boundaries(self):
        from sdsetup.reports import Report
        for pid, kind in (("0100000000000000", "system"), ("01000000000007FF", "system"),
                          ("0100000000000800", "app"), (None, "unknown")):
            self.assertEqual(classify(Report("x", 0, pid), self.card), kind, pid)

    def test_addon_findings(self):
        codes = {f.code for f in check_addons(self.card)}
        self.assertEqual(codes, {"addons-active"})
        clean_boot(self.card)
        findings = {f.code: f for f in check_addons(self.card)}
        self.assertEqual(findings["addons-disabled"].level, "warn")
        self.assertIn("--restore", findings["addons-disabled"].hint)

    def test_no_report_folders_is_fine(self):
        self.assertEqual(check_reports(self.card), [])


class DoctorIntegrationTests(unittest.TestCase):
    def test_doctor_includes_reports_and_addons(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        manifest = load_manifest()
        card = d / "card"
        card.mkdir()
        install_components(manifest.select(), card, StubProvider(fake_release_files(d / "rel")), log=lambda *_: None)
        (card / "atmosphere" / "crash_reports").mkdir(parents=True, exist_ok=True)
        (card / "atmosphere" / "crash_reports" / "a.log").write_text(crash_text(SYSTEM_ID, "ro"))
        (card / "atmosphere" / "contents" / ADDON_ID).mkdir(parents=True)
        (card / "atmosphere" / "contents" / ADDON_ID / "exefs.nsp").write_bytes(b"x")
        codes = {f.code for f in run_doctor(card, manifest, partitions=lambda l: [])}
        self.assertTrue({"report-system-crash", "addons-active"} <= codes)


if __name__ == "__main__":
    unittest.main()
