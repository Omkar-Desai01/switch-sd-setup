import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sdsetup import nand
from sdsetup.compat import check_firmware, load_compat
from sdsetup.doctor import run_doctor, summarize
from sdsetup.emummc import MIN_PARTITION_BYTES, check_emummc, parse_emummc_ini, windows_partitions
from sdsetup.install import install_components
from sdsetup.manifest import load_manifest
from sdsetup.nand import check_nand_backups, find_backup_sets
from sdsetup.ticket import ticket_findings

from test_core import StubProvider, fake_release_files


def codes(findings, level=None):
    return {f.code for f in findings if level is None or f.level == level}


def make_ticket(sig_byte=0x41, issuer=b"Root-CA00000003-XS00000020", sig_type=0x10004):
    sig = bytes([sig_byte]) * 0x100 if sig_byte is not None else bytes(range(256))
    return (struct.pack("<I", sig_type) + sig + b"\0" * 0x3C + issuer.ljust(0x40, b"\0") + b"\0" * (0x2C0 - 0x180))


def make_nsp(path, files):
    names, offs, data = b"", [], b""
    for name, content in files.items():
        offs.append((len(data), len(content), len(names)))
        names += name.encode() + b"\0"
        data += content
    names = names.ljust((len(names) + 0x1F) // 0x20 * 0x20, b"\0")
    head = b"PFS0" + struct.pack("<III", len(files), len(names), 0)
    entries = b"".join(struct.pack("<QQII", o, s, n, 0) for o, s, n in offs)
    Path(path).write_bytes(head + entries + names + data)


class SmallNand(unittest.TestCase):
    """Shrinks the NAND size constants so tests do not allocate 30 GB."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        for name, small in (("BOOT_SIZE", 16), ("GPP_SIZE", 100)):
            real = getattr(nand, name)
            setattr(nand, name, small)
            self.addCleanup(setattr, nand, name, real)

    def make_set(self, root, boot0=16, boot1=16, parts=()):
        root.mkdir(parents=True, exist_ok=True)
        if boot0 is not None:
            (root / "BOOT0").write_bytes(b"0" * boot0)
        if boot1 is not None:
            (root / "BOOT1").write_bytes(b"1" * boot1)
        for name, size in parts:
            (root / name).write_bytes(b"x" * size)
        return root


class NandTests(SmallNand):
    def test_boot_only_is_flagged(self):
        s = self.make_set(self.d / "backup" / "AB12CD34")
        f = check_nand_backups(self.d / "backup", "card")
        self.assertIn("nand-no-rawgpp", codes(f, "error"))

    def test_complete_split_backup_ok(self):
        self.make_set(self.d / "b" / "id", parts=[("rawnand.bin.00", 60), ("rawnand.bin.01", 50)])
        f = check_nand_backups(self.d / "b", "pc")
        self.assertEqual(codes(f, "error"), set())
        self.assertIn("nand-ok", codes(f))

    def test_short_raw_gpp(self):
        self.make_set(self.d / "b" / "id", parts=[("rawnand.bin.00", 60)])
        self.assertIn("nand-rawgpp-short", codes(check_nand_backups(self.d / "b", "pc"), "error"))

    def test_partial_backup_warns(self):
        s = self.make_set(self.d / "b" / "id", parts=[("rawnand.bin.00", 60)])
        (s / "partial.idx").write_bytes(b"\1")
        f = check_nand_backups(self.d / "b", "pc")
        self.assertIn("nand-partial", codes(f, "warn"))
        self.assertNotIn("nand-rawgpp-short", codes(f))

    def test_missing_and_truncated_boot(self):
        self.make_set(self.d / "b" / "id", boot0=None, boot1=5, parts=[("rawnand.bin", 100)])
        f = check_nand_backups(self.d / "b", "pc")
        self.assertTrue({"nand-boot-missing", "nand-boot-size"} <= codes(f, "error"))

    def test_single_file_and_root_is_the_set(self):
        self.make_set(self.d / "set", parts=[("rawnand.bin", 100)])
        self.assertEqual(find_backup_sets(self.d / "set"), [self.d / "set"])
        self.assertEqual(codes(check_nand_backups(self.d / "set", "x"), "error"), set())

    def test_no_backup_found(self):
        (self.d / "empty").mkdir()
        self.assertIn("nand-none", codes(check_nand_backups(self.d / "empty", "x"), "warn"))
        self.assertIn("nand-none", codes(check_nand_backups(self.d / "missing", "x"), "warn"))


class EmummcTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "emummc").mkdir()
        self.parts = lambda letter: [
            {"offset": 0x1000 * 512, "size": 4 * 1024**3, "mbr_type": 11},
            {"offset": 0xB3F8000 * 512, "size": MIN_PARTITION_BYTES + 10**9, "mbr_type": 224},
        ]

    def ini(self, text):
        (self.root / "emummc" / "emummc.ini").write_text(text)

    def test_parse(self):
        self.assertEqual(parse_emummc_ini("[emummc]\nenabled=1\nsector = 0xB400000\n;c\n[x]\nenabled=0\n"),
                         {"enabled": "1", "sector": "0xB400000"})

    def test_no_ini_and_disabled(self):
        self.assertIn("emummc-none", codes(check_emummc(self.root)))
        self.ini("[emummc]\nenabled=0\n")
        self.assertIn("emummc-disabled", codes(check_emummc(self.root)))

    def test_sector_matches_partition_start_plus_offset(self):
        self.ini("[emummc]\nenabled=1\nsector=0xB400000\n")
        f = check_emummc(self.root, letter="E", partitions=self.parts)
        self.assertIn("emummc-sector-ok", codes(f))
        self.assertEqual(codes(f, "error"), set())

    def test_sector_mismatch(self):
        self.ini("[emummc]\nenabled=1\nsector=0xB3F8000\n")  # forgot the 0x8000
        f = check_emummc(self.root, letter="E", partitions=self.parts)
        self.assertIn("emummc-sector-mismatch", codes(f, "error"))

    def test_small_partition_warns(self):
        self.ini("[emummc]\nenabled=1\nsector=0x9000\n")
        f = check_emummc(self.root, letter="E", partitions=self.parts)
        self.assertIn("emummc-partition-small", codes(f, "warn"))

    def test_raw_based_must_match(self):
        self.ini("[emummc]\nenabled=1\nsector=0xB400000\npath=emuMMC/RAW1\n")
        raw = self.root / "emuMMC" / "RAW1"
        raw.mkdir(parents=True)
        (raw / "raw_based").write_bytes(struct.pack("<I", 0xB400000))
        self.assertEqual(codes(check_emummc(self.root, letter="E", partitions=self.parts), "error"), set())
        (raw / "raw_based").write_bytes(struct.pack("<I", 0x1234))
        self.assertIn("emummc-raw-based", codes(check_emummc(self.root, letter="E", partitions=self.parts), "error"))

    def test_partition_table_unreadable_is_not_an_error(self):
        self.ini("[emummc]\nenabled=1\nsector=0xB400000\n")

        def boom(letter):
            raise OSError("access denied")
        f = check_emummc(self.root, letter="E", partitions=boom)
        self.assertIn("emummc-partition-unreadable", codes(f, "info"))
        self.assertEqual(codes(f, "error"), set())

    def test_no_letter_skips_partition_check(self):
        self.ini("[emummc]\nenabled=1\nsector=0xB400000\n")
        self.assertIn("emummc-partition-skipped", codes(check_emummc(self.root, partitions=self.parts)))

    def test_file_based_needs_emmc_folder(self):
        self.ini("[emummc]\nenabled=1\nsector=0x0\npath=emuMMC/SD00\n")
        self.assertIn("emummc-file-missing", codes(check_emummc(self.root), "error"))
        (self.root / "emuMMC" / "SD00" / "eMMC").mkdir(parents=True)
        self.assertEqual(codes(check_emummc(self.root), "error"), set())

    def test_windows_partitions_parses_powershell_json(self):
        out = '[{"PartitionNumber":1,"Offset":1048576,"Size":100,"MbrType":11},{"PartitionNumber":2,"Offset":2097152,"Size":200,"MbrType":224}]'
        runner = lambda cmd, **kw: SimpleNamespace(returncode=0, stdout=out, stderr="")
        parts = windows_partitions("e", runner)
        self.assertEqual([p["offset"] for p in parts], [1048576, 2097152])
        single = lambda cmd, **kw: SimpleNamespace(returncode=0, stdout='{"Offset":5,"Size":6,"MbrType":null}', stderr="")
        self.assertEqual(len(windows_partitions("E", single)), 1)
        bad = lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="no such drive")
        with self.assertRaises(OSError):
            windows_partitions("E", bad)


class FirmwareTests(unittest.TestCase):
    def setUp(self):
        self.compat = {"verified": "2026-10-27", "source": "notes", "atmosphere": {"1.12.1": "23.0.0"}}

    def test_levels(self):
        cases = [("22.1.0", "info"), ("23.0.0", "info"), ("23.0.1", "warn"), ("23.1.0", "error"), ("24.0.0", "error")]
        for fw, level in cases:
            f = check_firmware(fw, "1.12.1", self.compat)
            self.assertEqual(f[0].level, level, fw)

    def test_unknown_inputs(self):
        self.assertEqual(check_firmware("abc", "1.12.1", self.compat)[0].code, "fw-bad-version")
        self.assertEqual(check_firmware("23.0.0", None, self.compat)[0].code, "fw-no-ams")
        self.assertEqual(check_firmware("23.0.0", "9.9.9", self.compat)[0].code, "fw-unknown-ams")

    def test_shipped_table_loads(self):
        self.assertIn("1.12.1", load_compat()["atmosphere"])


class TicketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def nsp(self, name, tik):
        p = self.d / name
        files = {"game.cnmt.nca": b"nca", "00.nca": b"data"}
        if tik is not None:
            files["rights.tik"] = tik
        make_nsp(p, files)
        return p

    def test_blank_signature_flagged(self):
        f = ticket_findings(self.nsp("a.nsp", make_ticket(sig_byte=0x00)))
        self.assertIn("ticket-blank-signature", codes(f, "error"))
        self.assertIn("0x291", f[0].hint)
        self.assertIn("does not patch", f[0].hint)

    def test_ff_signature_flagged(self):
        self.assertIn("ticket-blank-signature", codes(ticket_findings(self.nsp("a.nsp", make_ticket(sig_byte=0xFF)))))

    def test_bad_issuer_flagged(self):
        f = ticket_findings(self.nsp("b.nsp", make_ticket(sig_byte=None, issuer=b"Root")))
        self.assertIn("ticket-bad-issuer", codes(f, "error"))

    def test_plausible_ticket(self):
        f = ticket_findings(self.nsp("c.nsp", make_ticket(sig_byte=None)))
        self.assertEqual(codes(f), {"ticket-plausible"})
        self.assertIn("not cryptographically verified", f[0].message)

    def test_unknown_signature_type(self):
        f = ticket_findings(self.nsp("d.nsp", make_ticket(sig_type=0x99999)))
        self.assertIn("ticket-odd-sig", codes(f, "warn"))

    def test_no_ticket(self):
        self.assertIn("nsp-no-ticket", codes(ticket_findings(self.nsp("e.nsp", None))))

    def test_not_an_nsp_or_truncated(self):
        junk = self.d / "f.nsp"
        junk.write_bytes(b"MZ" + b"\0" * 64)
        self.assertIn("nsp-unreadable", codes(ticket_findings(junk), "error"))
        good = self.nsp("g.nsp", make_ticket(sig_byte=None))
        cut = self.d / "h.nsp"
        cut.write_bytes(good.read_bytes()[:20])
        self.assertIn("nsp-unreadable", codes(ticket_findings(cut), "error"))
        self.assertIn("nsp-unreadable", codes(ticket_findings(self.d / "missing.nsp"), "error"))


class DoctorTests(SmallNand):
    def setUp(self):
        super().setUp()
        self.card = self.d / "card"
        self.card.mkdir()
        self.manifest = load_manifest()
        install_components(self.manifest.select(), self.card, StubProvider(fake_release_files(self.d)),
                           log=lambda *_: None)
        (self.card / "emummc").mkdir()
        (self.card / "emummc" / "emummc.ini").write_text("[emummc]\nenabled=1\nsector=0x9000\n")
        self.parts = lambda letter: [{"offset": 0x1000 * 512, "size": MIN_PARTITION_BYTES, "mbr_type": 224}]

    def test_combines_install_and_catalogue_checks(self):
        self.make_set(self.card / "backup" / "id")
        pc = self.make_set(self.d / "pc" / "id", parts=[("rawnand.bin", 100)])
        nsp = self.d / "x.nsp"
        make_nsp(nsp, {"r.tik": make_ticket(sig_byte=0)})
        f = run_doctor(self.card, self.manifest, firmware="23.1.0", ams_version="1.12.1",
                       nand_dirs=[self.d / "pc"], nsp_paths=[nsp], partitions=self.parts)
        self.assertTrue({"backup-on-card", "nand-no-rawgpp", "fw-too-new", "ticket-blank-signature"} <= codes(f))
        self.assertIn("nand-ok", codes(f))  # the PC copy is complete
        errors, warns = summarize(f)
        self.assertGreaterEqual(errors, 3)

    def test_clean_card(self):
        f = run_doctor(self.card, self.manifest, firmware="23.0.0", ams_version="1.12.1", partitions=self.parts)
        self.assertEqual(summarize(f), (0, 0), [x for x in f if x.level != "info"])

    def test_ams_version_comes_from_receipt(self):
        f = run_doctor(self.card, self.manifest, firmware="23.0.0", partitions=self.parts)
        self.assertIn("fw-unknown-ams", codes(f))  # stub receipt tag is "v1"

    def test_missing_target(self):
        self.assertIn("no-target", codes(run_doctor(self.d / "nope", self.manifest)))

    def test_nsp_folder_scan_and_empty(self):
        folder = self.d / "games"
        folder.mkdir()
        self.assertIn("nsp-none", codes(run_doctor(self.card, self.manifest, nsp_paths=[folder], partitions=self.parts)))
        make_nsp(folder / "a.nsp", {"r.tik": make_ticket(sig_byte=None)})
        self.assertIn("ticket-plausible", codes(run_doctor(self.card, self.manifest, nsp_paths=[folder], partitions=self.parts)))


if __name__ == "__main__":
    unittest.main()
