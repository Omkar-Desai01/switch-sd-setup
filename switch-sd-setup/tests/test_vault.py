import tempfile
import unittest
from pathlib import Path

from sdsetup.cli import build_parser, make_provider
from sdsetup.fetch import FetchError, RateLimitError, Resolved, sha256_file
from sdsetup.install import install_components
from sdsetup.manifest import load_manifest
from sdsetup.vault import FallbackProvider, Vault, update_vault
from sdsetup.verify import verify_card

from test_core import fake_release_files


class TaggedProvider:
    """Serves the fake release files under a chosen tag, or fails on demand."""

    def __init__(self, files, tag="1.0", fail=None, digest=True):
        self.files, self.tag, self.fail, self.digest = files, tag, fail, digest

    def get(self, comp):
        if self.fail:
            raise self.fail
        p = Path(self.files[comp.id])
        return Resolved(comp.id, self.tag, p.name, p, sha256_file(p), self.digest)


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.files = fake_release_files(self.d)
        self.manifest = load_manifest()
        self.vault = Vault(self.d / "vault")
        self.logs = []

    def save_all(self, tag="1.0"):
        return update_vault(self.vault, TaggedProvider(self.files, tag), self.manifest.select(), self.logs.append)

    def test_add_get_roundtrip(self):
        self.assertEqual(self.save_all(), [])
        comp = self.manifest.component("sphaira")
        r = self.vault.get(comp)
        self.assertEqual((r.tag, r.asset_name, r.digest_verified), ("1.0", "sp.zip", True))
        self.assertEqual(sha256_file(r.path), sha256_file(self.files["sphaira"]))
        self.assertTrue(r.path.is_relative_to(self.d / "vault"))

    def test_missing_component_and_tag(self):
        comp = self.manifest.component("sphaira")
        with self.assertRaisesRegex(FetchError, "no saved copy"):
            self.vault.get(comp)
        self.save_all()
        with self.assertRaisesRegex(FetchError, "no copy of"):
            self.vault.get(comp, "9.9")

    def test_keeps_current_plus_one_previous(self):
        for tag in ("1.0", "1.1", "1.2"):
            self.save_all(tag)
        comp = self.manifest.component("sphaira")
        self.assertEqual(self.vault.get(comp).tag, "1.2")
        self.assertEqual(self.vault.get(comp, "1.1").tag, "1.1")
        with self.assertRaises(FetchError):
            self.vault.get(comp, "1.0")
        self.assertFalse((self.d / "vault" / "sphaira" / "1.0").exists())
        self.assertEqual(self.vault.verify(), [])

    def test_unsafe_tag_is_stored_safely(self):
        # JKSV's real tag is '12/02/2025'
        comp = self.manifest.component("jksv")
        self.vault.add(comp, TaggedProvider(self.files | {"jksv": self.files["checkpoint"]}, "12/02/2025").get(comp))
        r = self.vault.get(comp)
        self.assertEqual(r.tag, "12/02/2025")
        self.assertEqual(r.path.relative_to(self.d / "vault").parts[:2], ("jksv", "12_02_2025"))

    def test_corruption_detected_on_get_and_verify(self):
        self.save_all()
        comp = self.manifest.component("sphaira")
        path = self.vault.get(comp).path
        path.write_bytes(b"tampered")
        with self.assertRaisesRegex(FetchError, "corrupt"):
            self.vault.get(comp)
        self.assertTrue(any("sphaira" in p and "sha256" in p for p in self.vault.verify()))
        path.unlink()
        self.assertTrue(any("missing" in p for p in self.vault.verify()))

    def test_update_failure_keeps_old_copy(self):
        self.save_all("1.0")
        failures = update_vault(self.vault, TaggedProvider(self.files, "2.0", fail=FetchError("rate limit")),
                                self.manifest.select(), self.logs.append)
        self.assertEqual(len(failures), len(self.manifest.select()))
        self.assertEqual(self.vault.get(self.manifest.component("sphaira")).tag, "1.0")

    def test_upstream_digest_flag_survives(self):
        comp = self.manifest.component("nxdumptool")
        self.vault.add(comp, TaggedProvider(self.files, digest=False).get(comp))
        self.assertFalse(self.vault.get(comp).digest_verified)
        self.assertFalse(self.vault.rows()[0].upstream_digest)

    def test_corrupt_index(self):
        (self.d / "vault").mkdir()
        (self.d / "vault" / "index.json").write_text("{nope")
        with self.assertRaisesRegex(FetchError, "corrupt"):
            self.vault.rows()


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.files = fake_release_files(self.d)
        self.manifest = load_manifest()
        self.vault = Vault(self.d / "vault")
        update_vault(self.vault, TaggedProvider(self.files, "1.0"), self.manifest.select(), lambda m: None)
        self.logs = []

    def test_online_wins_when_available(self):
        fb = FallbackProvider(TaggedProvider(self.files, "2.0"), self.vault, self.logs.append)
        self.assertEqual(fb.get(self.manifest.component("sphaira")).tag, "2.0")
        self.assertEqual(self.logs, [])

    def test_vault_used_when_online_fails_and_user_is_told(self):
        for err in (FetchError("repo missing"), RateLimitError("rate limit hit")):
            self.logs.clear()
            fb = FallbackProvider(TaggedProvider(self.files, fail=err), self.vault, self.logs.append)
            self.assertEqual(fb.get(self.manifest.component("sphaira")).tag, "1.0")
            self.assertIn("using saved copy 1.0", self.logs[0])
            self.assertIn(str(err), self.logs[0])

    def test_both_fail_reports_both(self):
        fb = FallbackProvider(TaggedProvider(self.files, fail=FetchError("offline")), self.vault, self.logs.append)
        with self.assertRaises(FetchError) as cm:
            fb.get(self.manifest.component("goldleaf"))  # optional, never saved
        self.assertIn("offline", str(cm.exception))
        self.assertIn("no usable saved copy", str(cm.exception))

    def test_install_works_with_github_down(self):
        card = self.d / "card"
        card.mkdir()
        fb = FallbackProvider(TaggedProvider(self.files, fail=FetchError("GitHub down")), self.vault, self.logs.append)
        install_components(self.manifest.select(), card, fb, log=lambda *_: None)
        self.assertEqual([f for f in verify_card(card, self.manifest) if f.level == "error"], [])
        self.assertEqual(len(self.logs), len(self.manifest.select()))


class NoApiFallbackTests(unittest.TestCase):
    """GitHub API rate-limited: fixed-name assets are still fetched through plain github.com URLs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.requested = []

    def opener(self, req, timeout=0):
        import io
        import urllib.error
        url = req.full_url
        self.requested.append(url)
        if url.startswith("https://api.github.com"):
            raise urllib.error.HTTPError(url, 403, "x", {}, io.BytesIO(b'{"message":"API rate limit exceeded"}'))
        if url.endswith("/releases/latest"):
            return Redirected(b"", "https://github.com/o/r/releases/tag/12%2F02%2F2025")
        return Redirected(b"NRO-DATA", url)

    def test_fixed_name_asset_downloads_without_api(self):
        from sdsetup.fetch import GithubProvider
        comp = load_manifest().component("goldleaf")
        res = GithubProvider(self.tmp.name, opener=self.opener).get(comp)
        self.assertEqual((res.tag, res.asset_name, res.digest_verified), ("12/02/2025", "Goldleaf.nro", False))
        self.assertEqual(res.path.read_bytes(), b"NRO-DATA")
        self.assertIn("https://github.com/XorTroll/Goldleaf/releases/download/12%2F02%2F2025/Goldleaf.nro",
                      self.requested)

    def test_versioned_asset_still_reports_rate_limit(self):
        from sdsetup.fetch import GithubProvider
        comp = load_manifest().component("atmosphere")
        with self.assertRaises(RateLimitError):
            GithubProvider(self.tmp.name, opener=self.opener).get(comp)


class Redirected:
    def __init__(self, data, url):
        import io
        self._b, self._url = io.BytesIO(data), url

    def read(self, n=-1):
        return self._b.read(n)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class CliSourceTests(unittest.TestCase):
    def test_source_flag_selects_provider(self):
        parser = build_parser()
        args = parser.parse_args(["--vault", "V", "install", "X:\\", "--source", "vault"])
        self.assertIsInstance(make_provider(args), Vault)
        args = parser.parse_args(["--vault", "V", "install", "X:\\"])
        self.assertIsInstance(make_provider(args), FallbackProvider)
        args = parser.parse_args(["wizard", "--source", "vault"])
        self.assertEqual(args.source, "vault")


if __name__ == "__main__":
    unittest.main()
