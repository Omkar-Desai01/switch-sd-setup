import json
import tempfile
import time
import unittest
from pathlib import Path

from sdsetup.backup import MARKER, backup_card, check_backup
from sdsetup.drives import get_drive
from sdsetup.fetch import FetchError
from sdsetup.install import RECEIPT, install_components, load_receipt
from sdsetup.manifest import load_manifest
from sdsetup.update import (UpdateError, latest_snapshot, read_meta, rollback, run_rollback, run_update, scan_card,
                            status_lines)
from sdsetup.vault import Vault, update_vault
from sdsetup.verify import verify_card

from test_core import fake_release_files
from test_drives import api
from test_vault import TaggedProvider
from test_wizard import Scripted


class CardCase(unittest.TestCase):
    """A card installed from fake v1 releases, with v2 available as 'newest'."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.manifest = load_manifest()
        self.v1 = fake_release_files(self.d / "v1", ver=b"-1", legacy=True)
        self.v2 = fake_release_files(self.d / "v2", ver=b"-2")
        self.card = self.d / "card"
        self.card.mkdir()
        install_components(self.manifest.select(), self.card, TaggedProvider(self.v1, "1.0"), log=lambda *_: None)
        self.vault = Vault(self.d / "vault")
        self.snaps = self.d / "snaps"
        self.newest = TaggedProvider(self.v2, "2.0")

    def scan(self, provider=None, vault=None, comps=None):
        return scan_card(self.card, comps or self.manifest.components, provider or self.newest,
                         vault or self.vault, self.manifest.volatile, log=lambda *_: None)

    def update(self, answers=(), **kw):
        io = Scripted(answers)
        kw.setdefault("snapshot_root", self.snaps)
        code = run_update(self.manifest, io, kw.pop("provider", self.newest), self.card, self.vault, **kw)
        return code, io

    def by_id(self, statuses):
        return {s.component.id: s for s in statuses}


class ScanTests(CardCase):
    def test_old_install_is_recognised_from_the_receipt(self):
        s = self.by_id(self.scan())
        for cid in ("atmosphere", "hekate", "sphaira", "checkpoint", "nxdumptool", "dns_tester"):
            self.assertEqual((s[cid].state, s[cid].installed, s[cid].latest), ("behind", "1.0", "2.0"), cid)

    def test_hand_built_card_recognised_from_the_vault(self):
        (self.card / RECEIPT).unlink()
        update_vault(self.vault, TaggedProvider(self.v1, "1.0"), self.manifest.select(), lambda *_: None)
        s = self.by_id(self.scan())
        self.assertEqual((s["atmosphere"].state, s["atmosphere"].installed), ("behind", "1.0"))

    def test_hand_built_card_with_no_reference_is_modified_but_updatable(self):
        (self.card / RECEIPT).unlink()
        a = self.by_id(self.scan())["atmosphere"]
        self.assertEqual(a.state, "modified")
        self.assertTrue(a.needs_update and a.files)

    def test_up_to_date_after_update_and_tie_prefers_newest(self):
        self.update(["", "n"])
        self.assertEqual({s.state for s in self.scan() if s.component.default}, {"up-to-date"})

    def test_modified_files_are_flagged_partial(self):
        (self.card / "atmosphere" / "package3").write_bytes(b"hacked")
        a = self.by_id(self.scan())["atmosphere"]
        self.assertEqual((a.state, a.installed), ("modified", "1.0"))  # 5 of 6 files still match release 1.0
        self.assertIn("mostly 1.0", "".join(status_lines([a])))
        self.assertTrue(a.needs_update)

    def test_offline_gives_unchecked_and_no_update(self):
        down = TaggedProvider(self.v2, "2.0", fail=FetchError("offline"))
        s = self.by_id(self.scan(provider=down))
        self.assertEqual((s["atmosphere"].state, s["atmosphere"].installed), ("unchecked", "1.0"))
        code, io = self.update(provider=down)
        self.assertEqual(code, 0)
        self.assertIn("Nothing to update", io.text)

    def test_optional_component_not_installed_is_not_even_fetched(self):
        s = self.by_id(self.scan())  # TaggedProvider has no goldleaf/jksv files: fetching them would raise KeyError
        self.assertEqual(s["goldleaf"].state, "optional-missing")
        self.assertFalse(s["goldleaf"].needs_update)

    def test_missing_default_component_is_offered(self):
        (self.card / "switch" / "sphaira" / "sphaira.nro").unlink()
        s = self.by_id(self.scan())["sphaira"]
        self.assertEqual(s.state, "missing")
        self.assertTrue(s.needs_update)

    def test_vault_as_the_provider(self):
        update_vault(self.vault, TaggedProvider(self.v2, "2.0"), self.manifest.select(), lambda *_: None)
        s = self.by_id(scan_card(self.card, self.manifest.select(), self.vault, self.vault,
                                 self.manifest.volatile, log=lambda *_: None))
        self.assertEqual((s["atmosphere"].state, s["atmosphere"].latest), ("behind", "2.0"))


class UpdateTests(CardCase):
    def test_dry_run_changes_nothing(self):
        before = (self.card / "atmosphere" / "package3").read_bytes()
        code, io = self.update(dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("Dry run", io.text)
        self.assertEqual((self.card / "atmosphere" / "package3").read_bytes(), before)
        self.assertFalse(self.snaps.exists())

    def test_declining_changes_nothing(self):
        code, _ = self.update(["n"])
        self.assertEqual(code, 1)
        self.assertEqual((self.card / "atmosphere" / "package3").read_bytes(), b"p3-1")
        self.assertFalse(self.snaps.exists())

    def test_update_applies_snapshots_and_keeps_user_settings(self):
        ipl = self.card / "bootloader" / "hekate_ipl.ini"
        ipl.write_text("[config]\n[Mine]\nemummcforce=1\n[Stock]\nstock=1\n")
        code, io = self.update(["", "n"])
        self.assertEqual(code, 0, io.text)
        self.assertEqual((self.card / "atmosphere" / "package3").read_bytes(), b"p3-2")
        self.assertIn("[Mine]", ipl.read_text())
        receipt = load_receipt(self.card)
        self.assertEqual({e["tag"] for e in receipt["components"].values()}, {"2.0"})
        snap = latest_snapshot(self.snaps)
        meta = read_meta(snap)
        self.assertEqual(meta["components"]["atmosphere"], {"before": "1.0", "after": "2.0"})
        self.assertEqual((snap / "files" / "atmosphere" / "package3").read_bytes(), b"p3-1")
        self.assertIn("[Mine]", (snap / "files" / "bootloader" / "hekate_ipl.ini").read_text())
        self.assertEqual([f for f in verify_card(self.card, self.manifest) if f.level == "error"], [])

    def test_leftover_files_listed_kept_by_default_and_removed_on_request(self):
        legacy = self.card / "atmosphere" / "legacy_module.bin"
        self.assertTrue(legacy.exists())
        _, io = self.update(["", "n"])  # update yes, remove leftovers no
        self.assertIn("legacy_module.bin", io.text)
        self.assertTrue(legacy.exists())

    def test_leftover_removal_is_reversible_and_skips_edited_files(self):
        legacy = self.card / "atmosphere" / "legacy_module.bin"
        self.update(["", "y"])
        self.assertFalse(legacy.exists())
        snap = latest_snapshot(self.snaps)
        self.assertEqual((snap / "removed" / "atmosphere" / "legacy_module.bin").read_bytes(), b"legacy")
        self.assertEqual(rollback(self.card, snap, log=lambda *_: None), [])
        self.assertEqual(legacy.read_bytes(), b"legacy")

        # an edited leftover is never removed
        legacy.write_bytes(b"my own edit")
        _, io = self.update(["", "y"], snapshot_root=self.d / "snaps2", only=["atmosphere"])
        self.assertTrue(legacy.exists())

    def test_only_updates_the_chosen_component_and_keeps_the_others_in_the_receipt(self):
        code, io = self.update(["", "n"], only=["atmosphere"])
        self.assertEqual(code, 0, io.text)
        tags = {cid: e["tag"] for cid, e in load_receipt(self.card)["components"].items()}
        self.assertEqual(tags["atmosphere"], "2.0")
        self.assertEqual(tags["hekate"], "1.0")
        self.assertEqual(len(tags), 6)
        self.assertEqual([f for f in verify_card(self.card, self.manifest) if f.level == "error"], [])

    def test_assume_yes_needs_no_questions(self):
        code, io = self.update([], assume_yes=True)
        self.assertEqual(code, 0)
        self.assertEqual(io.prompts, [])

    def test_nothing_to_do_when_current(self):
        self.update([], assume_yes=True)
        code, io = self.update([])
        self.assertEqual(code, 0)
        self.assertIn("Nothing to update", io.text)

    def test_not_a_folder(self):
        with self.assertRaises(UpdateError):
            run_update(self.manifest, Scripted([]), self.newest, self.d / "nope", self.vault, snapshot_root=self.snaps)


class RollbackTests(CardCase):
    def test_rollback_restores_the_old_state(self):
        self.update([], assume_yes=True, remove_leftovers=True)
        self.assertEqual((self.card / "atmosphere" / "package3").read_bytes(), b"p3-2")
        io = Scripted([""])  # confirm default is No, so answer explicitly below
        io = Scripted(["y"])
        self.assertEqual(run_rollback(io, self.card, self.snaps), 0, io.text)
        self.assertEqual((self.card / "atmosphere" / "package3").read_bytes(), b"p3-1")
        self.assertTrue((self.card / "atmosphere" / "legacy_module.bin").exists())
        self.assertEqual({e["tag"] for e in load_receipt(self.card)["components"].values()}, {"1.0"})

    def test_second_rollback_has_nothing_to_roll_back(self):
        self.update([], assume_yes=True)
        run_rollback(Scripted([]), self.card, self.snaps, assume_yes=True)
        io = Scripted([])
        self.assertEqual(run_rollback(io, self.card, self.snaps, assume_yes=True), 1)
        self.assertIn("No snapshot", io.text)

    def test_declined_rollback_changes_nothing(self):
        self.update([], assume_yes=True)
        self.assertEqual(run_rollback(Scripted(["n"]), self.card, self.snaps), 1)
        self.assertEqual((self.card / "atmosphere" / "package3").read_bytes(), b"p3-2")

    def test_files_added_by_the_update_are_removed_unless_changed_since(self):
        sphaira = self.card / "switch" / "sphaira" / "sphaira.nro"
        sphaira.unlink()
        self.update([], assume_yes=True)
        self.assertTrue(sphaira.exists())
        sphaira.write_bytes(b"user modified after update")
        problems = rollback(self.card, latest_snapshot(self.snaps), log=lambda *_: None)
        self.assertTrue(any("sphaira.nro" in p for p in problems))
        self.assertTrue(sphaira.exists())

    def test_added_file_without_changes_is_removed_and_empty_folders_pruned(self):
        sphaira = self.card / "switch" / "sphaira" / "sphaira.nro"
        sphaira.unlink()
        sphaira.parent.rmdir()
        self.update([], assume_yes=True)
        self.assertTrue(sphaira.exists())
        self.assertEqual(rollback(self.card, latest_snapshot(self.snaps), log=lambda *_: None), [])
        self.assertFalse(sphaira.parent.exists())

    def test_damaged_snapshot_file_is_reported_not_trusted(self):
        self.update([], assume_yes=True)
        snap = latest_snapshot(self.snaps)
        (snap / "files" / "atmosphere" / "package3").write_bytes(b"damaged")
        problems = rollback(self.card, snap, log=lambda *_: None)
        self.assertTrue(any("package3" in p for p in problems))
        self.assertEqual((self.card / "atmosphere" / "package3").read_bytes(), b"p3-2")  # not overwritten with junk

    def test_no_snapshot_folder(self):
        self.assertIsNone(latest_snapshot(self.d / "none"))
        with self.assertRaises(UpdateError):
            read_meta(self.d)


class EssentialBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.card = self.d / "card"
        (self.card / "atmosphere").mkdir(parents=True)
        (self.card / "atmosphere" / "package3").write_bytes(b"p3")
        (self.card / "Nintendo" / "Contents").mkdir(parents=True)
        (self.card / "Nintendo" / "Contents" / "game").write_bytes(b"x" * 5000)
        (self.card / "switch").mkdir()
        (self.card / "switch" / "app.nro").write_bytes(b"nro")
        (self.card / "dump.bin").write_bytes(b"y" * 4000)
        self.drive = get_drive("E", api())

    def test_essential_skips_large_folders_and_files(self):
        res = backup_card(self.card, self.d / "out", drive=self.drive, essential=True, max_bytes=1000)
        self.assertTrue(res.ok)
        self.assertEqual(res.mode, "essential")
        self.assertEqual({n for n, _ in res.excluded}, {"Nintendo/", "dump.bin"})
        self.assertTrue((res.path / "atmosphere" / "package3").exists())
        self.assertTrue((res.path / "switch" / "app.nro").exists())
        self.assertFalse((res.path / "Nintendo").exists())
        self.assertFalse((res.path / "dump.bin").exists())

    def test_essential_backup_does_not_justify_a_format(self):
        res = backup_card(self.card, self.d / "out", drive=self.drive, essential=True, max_bytes=1000)
        problems = check_backup(res.path, self.drive)
        self.assertTrue(any("partial" in p and "Nintendo/" in p for p in problems))

    def test_full_backup_still_justifies_a_format(self):
        res = backup_card(self.card, self.d / "out", drive=self.drive)
        self.assertEqual(res.mode, "full")
        self.assertEqual(res.excluded, [])
        self.assertEqual(check_backup(res.path, self.drive), [])
        self.assertTrue((res.path / "Nintendo" / "Contents" / "game").exists())

    def test_old_marker_without_mode_counts_as_full(self):
        res = backup_card(self.card, self.d / "out", drive=self.drive)
        marker = json.loads((res.path / MARKER).read_text())
        del marker["mode"]
        (res.path / MARKER).write_text(json.dumps(marker))
        self.assertEqual(check_backup(res.path, self.drive), [])


if __name__ == "__main__":
    unittest.main()


class DamagedFileTests(unittest.TestCase):
    """One bad file in a recognised release must not be reported as 'up to date' (found on a real card)."""

    def setUp(self):
        from test_core import make_zip
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.manifest = load_manifest()
        files = {f"atmosphere/part{i}.bin": f"data{i}".encode() for i in range(20)}
        files["atmosphere/package3"] = b"p3"
        self.provider = TaggedProvider({"atmosphere": make_zip(self.d / "ams.zip", files)}, "1.0")
        self.card = self.d / "card"
        self.card.mkdir()
        self.ams = self.manifest.component("atmosphere")
        install_components([self.ams], self.card, self.provider, log=lambda *_: None)
        self.vault = Vault(self.d / "vault")

    def status(self):
        return scan_card(self.card, [self.ams], self.provider, self.vault, self.manifest.volatile,
                         log=lambda *_: None)[0]

    def test_intact_card_is_up_to_date(self):
        self.assertEqual(self.status().state, "up-to-date")

    def test_one_corrupt_file_among_many_is_flagged(self):
        (self.card / "atmosphere" / "package3").write_bytes(b"junk")
        s = self.status()
        self.assertEqual((s.state, s.installed), ("modified", "1.0"))
        self.assertIn("package3", s.detail)
        self.assertTrue(s.needs_update)

    def test_one_missing_file_is_flagged_and_repaired_by_update(self):
        (self.card / "atmosphere" / "part3.bin").unlink()
        self.assertEqual(self.status().state, "modified")
        io = Scripted([])
        run_update(self.manifest, io, self.provider, self.card, self.vault,
                   snapshot_root=self.d / "snaps", assume_yes=True, only=["atmosphere"])
        # (exit code is 1 here: this stub card has no hekate files, so the final layout check complains)
        self.assertTrue((self.card / "atmosphere" / "part3.bin").exists())
        self.assertIn("1 file(s) differ", io.text)
        self.assertEqual(self.status().state, "up-to-date")
