import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sdsetup import backup
from sdsetup.checklist import render_checklist
from sdsetup.drives import get_drive
from sdsetup.manifest import load_manifest
from sdsetup.wizard import Console, run_wizard

from test_core import StubProvider, fake_release_files
from test_drives import GB, FakeApi, api


class Scripted(Console):
    def __init__(self, answers):
        self.answers, self.out, self.prompts = list(answers), [], []

    def say(self, text=""):
        self.out.append(text)

    def ask(self, prompt, default=""):
        self.prompts.append(prompt)
        answer = self.answers.pop(0) if self.answers else ""
        return answer or default

    @property
    def text(self):
        return "\n".join(self.out)


class MountApi(FakeApi):
    def __init__(self, drives, mounts):
        super().__init__(drives)
        self.mounts = mounts

    def mount(self, letter):
        return self.mounts.get(letter, "")


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.card = self.d / "card"
        (self.card / "old").mkdir(parents=True)
        (self.card / "old" / "save.dat").write_bytes(b"precious")
        self.out = self.d / "pc"
        self.manifest = load_manifest()
        self.provider = StubProvider(fake_release_files(self.d))
        self.api = self.make_api()
        self.calls = []

    def make_api(self, **over):
        base = api(**over)
        return MountApi(base.drives, {"E": str(self.card)})

    def runner(self, cmd, **kw):
        self.calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def wizard(self, answers, **kw):
        io = Scripted(answers)
        kw.setdefault("admin", True)
        code = run_wizard(self.manifest, io, self.provider, api=self.api, backup_root=self.out,
                          runner=self.runner, **kw)
        return code, io

    def test_dry_run_writes_nothing(self):
        code, io = self.wizard(["", "n", "", ""], dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual([p.name for p in self.card.iterdir()], ["old"])
        self.assertFalse(self.out.exists())
        self.assertIn("SWITCH SETUP CHECKLIST", io.text)
        self.assertIn("DRY RUN", io.text)

    def test_full_run_with_format(self):
        # use drive, no hash, format yes, default fs, label, phrase, no optional extras
        code, io = self.wizard(["", "y", "", "", "TESTSD", "ERASE E:", "", ""])
        self.assertEqual(code, 0, io.text)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("-NewFileSystemLabel 'TESTSD'", self.calls[0][-1])
        backups = [p for p in self.out.iterdir() if p.is_dir()]
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "old" / "save.dat").read_bytes(), b"precious")
        self.assertTrue((self.card / "bootloader" / "hekate_ipl.ini").is_file())
        self.assertEqual((self.out / "hekate_payload.bin").read_bytes(), b"HK")
        self.assertIn("v1", (self.out / "SETUP-CHECKLIST.txt").read_text())
        self.assertIn("all checks passed", io.text)

    def test_no_format_still_backs_up_and_installs(self):
        code, io = self.wizard(["", "n", "", "", ""])
        self.assertEqual(code, 0, io.text)
        self.assertEqual(self.calls, [])
        self.assertTrue((self.card / "old" / "save.dat").exists())
        self.assertTrue((self.card / "switch" / "sphaira" / "sphaira.nro").is_file())

    def test_optional_component_added(self):
        extra = self.d / "g.nro"
        extra.write_bytes(b"gold")
        self.provider.files["goldleaf"] = extra
        code, _ = self.wizard(["", "n", "", "y", ""])
        self.assertEqual(code, 0)
        self.assertEqual((self.card / "switch/Goldleaf/Goldleaf.nro").read_bytes(), b"gold")

    def test_no_removable_drive(self):
        self.api = self.make_api(E=(3, "Fixed", 5, "NTFS", 64 * GB, 30 * GB))
        code, io = self.wizard([])
        self.assertEqual(code, 1)
        self.assertIn("No usable removable drive", io.text)

    def test_preselected_drive_skips_the_picker(self):
        code, io = self.wizard(["n", "", ""], dry_run=True, preselect="e:")  # format? no, then the two extras
        self.assertEqual(code, 0, io.text)
        self.assertIn("Using E:", io.text)
        self.assertFalse(any("Use E:" in p for p in io.prompts))

    def test_unusable_preselect_is_refused(self):
        code, io = self.wizard([], dry_run=True, preselect="D")
        self.assertEqual(code, 1)
        self.assertIn("not a usable removable drive", io.text)

    def test_declining_the_drive_cancels(self):
        code, _ = self.wizard(["n"])
        self.assertEqual(code, 1)
        self.assertFalse(self.out.exists())

    def test_pick_between_two_cards(self):
        other = self.d / "card2"
        other.mkdir()
        self.api = MountApi(api(G=(2, "OTHER", 888, "exFAT", 32 * GB, 32 * GB)).drives,
                            {"E": str(self.card), "G": str(other)})
        code, io = self.wizard(["2", "n", "", ""])  # G is empty: no backup, no hash prompt
        self.assertEqual(code, 0, io.text)
        self.assertIn("nothing to back up", io.text)
        self.assertTrue((other / "atmosphere").is_dir())
        self.assertFalse((self.card / "atmosphere").exists())

    def test_wrong_phrase_stops_before_formatting(self):
        code, io = self.wizard(["", "y", "", "", "X", "erase e:", "n"])
        self.assertEqual(code, 1)
        self.assertEqual(self.calls, [])
        self.assertIn("confirmation did not match", io.text)
        self.assertFalse((self.card / "atmosphere").exists())

    def test_not_admin_can_continue_without_format(self):
        code, io = self.wizard(["", "y", "", "", "X", "ERASE E:", "y", "", ""], admin=False)
        self.assertEqual(code, 0, io.text)
        self.assertIn("administrator", io.text)
        self.assertEqual(self.calls, [])
        self.assertTrue((self.card / "old" / "save.dat").exists())

    def test_failed_backup_aborts_everything(self):
        real = backup.shutil.copy2
        backup.shutil.copy2 = lambda s, t: (_ for _ in ()).throw(OSError("device error"))
        self.addCleanup(setattr, backup.shutil, "copy2", real)
        code, io = self.wizard(["", "y", "", "", "X", "ERASE E:"])
        self.assertEqual(code, 1)
        self.assertEqual(self.calls, [])
        self.assertIn("nothing is changed", io.text)
        self.assertFalse((self.card / "atmosphere").exists())

    def test_fetch_failure_reported(self):
        class Broken:
            def get(self, comp):
                from sdsetup.fetch import FetchError
                raise FetchError("rate limit")
        io = Scripted(["", "", "n"])
        code = run_wizard(self.manifest, io, Broken(), api=self.api, backup_root=self.out,
                          runner=self.runner, admin=True)
        self.assertEqual(code, 1)
        self.assertIn("rate limit", io.text)


class ChecklistTests(unittest.TestCase):
    def test_contents(self):
        m = load_manifest()
        drive = get_drive("E", api())
        text = render_checklist({"components": {"atmosphere": {"tag": "1.12.1"}}}, drive, m,
                                payload_path="C:/x/hekate_payload.bin", backup_path="C:/b")
        for needle in ("1.12.1", "hekate_payload.bin", "RAW GPP", "Airplane Mode", "0x291", "AMS x.y.z|E", "C:/b"):
            self.assertIn(needle, text)
        self.assertNotIn("sys-patch", text.lower())


if __name__ == "__main__":
    unittest.main()
