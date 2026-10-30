"""Repository hygiene: versions agree, links resolve, nothing personal is published."""
import re
import unittest
from pathlib import Path

import sdsetup

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "release", "build", "dist", "vault", "__pycache__"}
TEXT = {".md", ".py", ".yml", ".yaml", ".json", ".html", ".toml", ".txt"}


def text_files():
    for p in ROOT.rglob("*"):
        if p.is_file() and p.suffix in TEXT and not SKIP_DIRS & set(p.relative_to(ROOT).parts):
            yield p


class VersionTests(unittest.TestCase):
    def test_package_and_pyproject_versions_agree(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1), sdsetup.__version__)

    def test_changelog_has_an_entry_for_this_version(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertRegex(changelog, rf"(?m)^## \[{re.escape(sdsetup.__version__)}\]")
        self.assertRegex(changelog, r"(?m)^## \[Unreleased\]")


class SupportLinkTests(unittest.TestCase):
    def test_coffee_link_is_consistent_everywhere(self):
        self.assertEqual(sdsetup.SUPPORT_URL, "https://buymeacoffee.com/ok_27")
        self.assertIn("buy_me_a_coffee: ok_27", (ROOT / ".github" / "FUNDING.yml").read_text())
        for name in ("README.md", "site/index.html"):
            self.assertIn(sdsetup.SUPPORT_URL, (ROOT / name).read_text(encoding="utf-8"), name)


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    def test_publishes_only_for_version_tags_and_checks_the_version(self):
        self.assertIn('tags: ["v*"]', self.text)
        self.assertIn("does not match package version", self.text)
        self.assertIn("CHANGELOG.md", self.text)

    def test_beta_versions_are_prereleases_and_third_party_files_are_not_shipped(self):
        self.assertIn("startsWith(github.ref_name, 'v0.')", self.text)
        self.assertNotIn("vault update", self.text)


class LinkTests(unittest.TestCase):
    def test_relative_markdown_links_resolve(self):
        broken = []
        for md in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")), ROOT / "CONTRIBUTING.md"]:
            for target in re.findall(r"\]\(([^)\s]+)\)", md.read_text(encoding="utf-8")):
                if re.match(r"(https?:|mailto:|#)", target):
                    continue
                path = (md.parent / target.split("#")[0]).resolve()
                if not path.exists():
                    broken.append(f"{md.name}: {target}")
        self.assertEqual(broken, [])

    def test_readme_images_exist(self):
        for src in re.findall(r'src="([^"]+)"', (ROOT / "README.md").read_text(encoding="utf-8")):
            if not src.startswith("http"):
                self.assertTrue((ROOT / src).exists(), src)


class PrivacyTests(unittest.TestCase):
    def test_no_personal_data_in_published_files(self):
        patterns = {
            "windows user path": re.compile(r"C:\\Users\\", re.I),
            "work domain": re.compile(r"openspaceservices", re.I),
            "email address": re.compile(r"[\w.+-]+@(?!users\.noreply\.github\.com)[\w-]+\.[a-z]{2,}", re.I),
        }
        found = []
        for p in text_files():
            if p.name == "test_project.py":
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            found += [f"{p.relative_to(ROOT).as_posix()}: {label}" for label, rx in patterns.items() if rx.search(text)]
        self.assertEqual(found, [])

    def test_no_third_party_binaries_are_tracked(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("vault/*", ignore)
        self.assertIn("!vault/README.md", ignore)
        self.assertIn("release/", ignore)


if __name__ == "__main__":
    unittest.main()
