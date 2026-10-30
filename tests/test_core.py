import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path

from sdsetup.fetch import (Asset, FetchError, RateLimitError, Resolved, download_asset,
                           latest_release, parse_release, pick_asset, sha256_file)
from sdsetup.install import InstallError, install_components, install_zip, safe_relpath
from sdsetup.manifest import ManifestError, load_manifest
from sdsetup.verify import ERROR, WARN, verify_card


def make_zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class StubProvider:
    """Serves prebuilt local files in place of GitHub downloads."""

    def __init__(self, files):
        self.files = files

    def get(self, comp):
        path = self.files[comp.id]
        return Resolved(comp.id, "v1", Path(path).name, Path(path), sha256_file(path), True)


class ManifestTests(unittest.TestCase):
    def test_default_manifest_loads(self):
        m = load_manifest()
        ids = [c.id for c in m.select()]
        self.assertIn("atmosphere", ids)
        self.assertNotIn("goldleaf", ids)
        self.assertIn("goldleaf", [c.id for c in m.select(with_ids=["goldleaf"])])
        self.assertNotIn("sphaira", [c.id for c in m.select(without_ids=["sphaira"])])

    def test_unknown_component(self):
        with self.assertRaises(ManifestError):
            load_manifest().select(with_ids=["nope"])

    def test_no_bypass_components(self):
        text = Path(load_manifest.__globals__["DEFAULT_MANIFEST"]).read_text().lower()
        self.assertNotIn("sys-patch", text)
        self.assertNotIn("syspatch", text)


class FetchTests(unittest.TestCase):
    def release_json(self, digest=None):
        return {"tag_name": "1.0", "assets": [
            {"name": "a-1.0.zip", "browser_download_url": "https://x.test/1", "size": 3, "digest": digest},
            {"name": "b.nro", "browser_download_url": "https://x.test/2", "size": 1},
        ]}

    def test_parse_and_pick(self):
        rel = parse_release("o/r", self.release_json("sha256:ABC"))
        self.assertEqual(rel.assets[0].digest, "abc")
        self.assertIsNone(rel.assets[1].digest)
        self.assertEqual(pick_asset(rel, r"^a-.*\.zip$").name, "a-1.0.zip")
        with self.assertRaises(FetchError):
            pick_asset(rel, r"^zzz$")
        with self.assertRaises(FetchError):
            pick_asset(rel, r".")  # ambiguous

    def test_rate_limit_message(self):
        def opener(req, timeout=0):
            raise urllib.error.HTTPError(
                "u", 403, "x", {}, io.BytesIO(b'{"message":"API rate limit exceeded"}'))
        with self.assertRaises(RateLimitError):
            latest_release("o/r", opener=opener)

    def test_dmca_block_is_clear(self):
        def opener(req, timeout=0):
            raise urllib.error.HTTPError(
                "u", 403, "x", {}, io.BytesIO(b'{"message":"Repository access blocked"}'))
        with self.assertRaisesRegex(FetchError, "blocked"):
            latest_release("o/r", opener=opener)

    def test_cache_avoids_second_request(self):
        calls = []

        def opener(req, timeout=0):
            calls.append(1)
            return FakeResp(json.dumps(self.release_json()).encode())
        with tempfile.TemporaryDirectory() as d:
            latest_release("o/r", opener=opener, cache_dir=d)
            latest_release("o/r", opener=opener, cache_dir=d)
        self.assertEqual(len(calls), 1)

    def test_download_verifies_digest(self):
        data = b"hello"
        good = Asset("f.bin", "https://x.test/f.bin", 5, hashlib.sha256(data).hexdigest())
        bad = Asset("g.bin", "https://x.test/g.bin", 5, "0" * 64)
        opener = lambda req, timeout=0: FakeResp(data)
        with tempfile.TemporaryDirectory() as d:
            path, sha, verified = download_asset(good, d, opener=opener)
            self.assertTrue(verified)
            self.assertEqual(path.read_bytes(), data)
            with self.assertRaisesRegex(FetchError, "sha256 mismatch"):
                download_asset(bad, d, opener=opener)
            self.assertFalse((Path(d) / "g.bin").exists())
            self.assertFalse((Path(d) / "g.bin.part").exists())

    def test_download_size_mismatch(self):
        asset = Asset("f.bin", "https://x.test/f.bin", 99, None)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(FetchError, "size mismatch"):
                download_asset(asset, d, opener=lambda r, timeout=0: FakeResp(b"abc"))

    def test_download_without_digest_is_unverified(self):
        asset = Asset("f.bin", "https://x.test/f.bin", 3, None)
        with tempfile.TemporaryDirectory() as d:
            _, _, verified = download_asset(asset, d, opener=lambda r, timeout=0: FakeResp(b"abc"))
        self.assertFalse(verified)


class InstallTests(unittest.TestCase):
    def test_safe_relpath(self):
        for bad in ("../x", "a/../../x", "/abs", "C:/x", "C:\\x"):
            with self.assertRaises(InstallError, msg=bad):
                safe_relpath(bad)
        self.assertEqual(safe_relpath("a\\b/c"), "a/b/c")

    def test_zip_slip_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            z = make_zip(Path(d) / "e.zip", {"../evil.txt": b"x"})
            card = Path(d) / "card"
            card.mkdir()
            with self.assertRaises(InstallError):
                install_zip(z, card)
            self.assertFalse((Path(d) / "evil.txt").exists())

    def test_redirect_and_dry_run(self):
        m = load_manifest()
        redirect = m.component("hekate").redirect
        with tempfile.TemporaryDirectory() as d:
            z = make_zip(Path(d) / "h.zip", {
                "hekate_ctcaer_6.5.4.bin": b"PAYLOAD",
                "bootloader/update.bin": b"U",
            })
            card = Path(d) / "card"
            card.mkdir()
            files = install_zip(z, card, redirect=redirect, dry_run=True)
            self.assertEqual(list(card.iterdir()), [])
            self.assertEqual(set(files), {"payload.bin", "atmosphere/reboot_payload.bin", "bootloader/update.bin"})
            install_zip(z, card, redirect=redirect)
            self.assertEqual((card / "payload.bin").read_bytes(), b"PAYLOAD")
            self.assertEqual((card / "atmosphere/reboot_payload.bin").read_bytes(), b"PAYLOAD")
            self.assertFalse((card / "hekate_ctcaer_6.5.4.bin").exists())


def fake_release_files(d, ver=b"", legacy=False):
    """Fake release files. ver is mixed into every payload so two versions differ; legacy adds a file
    that older releases shipped."""
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    ams_files = {
        "atmosphere/package3": b"p3" + ver, "atmosphere/stratosphere.romfs": b"romfs" + ver,
        "atmosphere/reboot_payload.bin": b"amsreboot", "atmosphere/config_templates/exosphere.ini": b"",
        "atmosphere/config/": b"", "atmosphere/fatal_errors/": b"", "hbmenu.nro": b"hb" + ver, "switch/x.nro": b"x" + ver,
    }
    if legacy:
        ams_files["atmosphere/legacy_module.bin"] = b"legacy"
    ams = make_zip(d / "ams.zip", ams_files)
    hk = make_zip(d / "hk.zip", {
        "hekate_ctcaer_6.5.4.bin": b"HK" + ver, "bootloader/update.bin": b"upd" + ver, "bootloader/ini/": b"",
        "bootloader/payloads/": b"", "bootloader/res/icon.bmp": b"i" + ver,
    })
    sp = make_zip(d / "sp.zip", {"switch/sphaira/sphaira.nro": b"sph" + ver})
    other = {}
    for cid in ("checkpoint", "nxdumptool", "dns_tester"):
        p = d / f"{cid}.nro"
        p.write_bytes(cid.encode() + ver)
        other[cid] = p
    return {"atmosphere": ams, "hekate": hk, "sphaira": sp, **other}


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.card = self.d / "card"
        self.card.mkdir()
        self.manifest = load_manifest()
        self.provider = StubProvider(fake_release_files(self.d))

    def install(self, **kw):
        return install_components(self.manifest.select(), self.card, self.provider, log=lambda *_: None, **kw)

    def codes(self, level=None):
        return {f.code for f in verify_card(self.card, self.manifest) if level is None or f.level == level}

    def test_install_then_verify_clean(self):
        self.install()
        self.assertTrue((self.card / "switch/Checkpoint/Checkpoint.nro").is_file())
        self.assertTrue((self.card / "bootloader/hekate_ipl.ini").is_file())
        self.assertEqual(self.codes(ERROR), set())

    def test_dry_run_writes_nothing(self):
        self.install(dry_run=True)
        self.assertEqual(list(self.card.iterdir()), [])

    def test_existing_config_kept_unless_reset(self):
        ipl = self.card / "bootloader/hekate_ipl.ini"
        ipl.parent.mkdir(parents=True)
        ipl.write_text("[config]\n[Mine]\nemummcforce=1\n")
        self.install()
        self.assertIn("[Mine]", ipl.read_text())
        self.install(reset_config=True)
        self.assertIn("Atmosphere emuMMC", ipl.read_text())

    def test_detects_missing_and_modified(self):
        self.install()
        (self.card / "atmosphere/package3").write_bytes(b"corrupt")
        (self.card / "switch/sphaira/sphaira.nro").unlink()
        (self.card / "atmosphere/reboot_payload.bin").write_bytes(b"hekate rewrote me")
        errors = {f.message for f in verify_card(self.card, self.manifest) if f.level == ERROR}
        self.assertTrue(any("package3" in m for m in errors))
        self.assertTrue(any("sphaira.nro" in m for m in errors))
        self.assertFalse(any("reboot_payload" in m for m in errors))  # volatile -> info only

    def test_warns_backup_and_debug_log(self):
        self.install()
        (self.card / "backup").mkdir()
        ss = self.card / "atmosphere/config/system_settings.ini"
        ss.write_text("[atmosphere]\nenable_dns_mitm_debug_log = u8!0x1\n")
        self.assertTrue({"backup-on-card", "dns-debug-on"} <= self.codes(WARN))

    def test_bad_ipl_entries(self):
        self.install()
        (self.card / "bootloader/hekate_ipl.ini").write_text("[config]\n[Only]\npkg3=atmosphere/package3\n")
        self.assertTrue({"ipl-no-emummc", "ipl-no-stock"} <= self.codes(WARN))

    def test_hosts_file_blocks_nintendo(self):
        self.install()
        text = (self.card / "atmosphere/hosts/emummc.txt").read_text()
        self.assertIn("127.0.0.1 *nintendo.com", text)
        self.assertIn("conntest.nintendowifi.net", text)

    def test_missing_target(self):
        with self.assertRaises(InstallError):
            install_components([], self.d / "nope", self.provider, log=lambda *_: None)
        self.assertIn("no-target", {f.code for f in verify_card(self.d / "nope", self.manifest)})


if __name__ == "__main__":
    unittest.main()
