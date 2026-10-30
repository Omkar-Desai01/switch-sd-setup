import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parent.parent
# Packaged exe: bundled data lives in PyInstaller's temp dir, user-visible data (the vault) next to the exe.
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", _SOURCE_ROOT))
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else _SOURCE_ROOT
DEFAULT_MANIFEST = RESOURCE_DIR / "manifest.json"


class ManifestError(Exception):
    pass


@dataclass(frozen=True)
class Redirect:
    pattern: str
    to: tuple


@dataclass(frozen=True)
class Component:
    id: str
    name: str
    repo: str
    asset: str
    kind: str
    default: bool
    dest: str = ""
    redirect: Redirect = None


@dataclass(frozen=True)
class Manifest:
    components: tuple
    manual: tuple
    dirs: tuple
    files: tuple
    volatile: frozenset = field(default_factory=frozenset)

    def component(self, cid):
        for c in self.components:
            if c.id == cid:
                return c
        raise ManifestError(f"unknown component: {cid}")

    def select(self, with_ids=(), without_ids=()):
        for cid in (*with_ids, *without_ids):
            self.component(cid)
        chosen = []
        for c in self.components:
            if c.id in without_ids:
                continue
            if c.default or c.id in with_ids:
                chosen.append(c)
        return chosen


def _component(raw):
    kind = raw["kind"]
    if kind not in ("zip", "file"):
        raise ManifestError(f"{raw['id']}: bad kind {kind!r}")
    if kind == "file" and not raw.get("dest"):
        raise ManifestError(f"{raw['id']}: file component needs dest")
    re.compile(raw["asset"])
    redirect = None
    if "redirect" in raw:
        r = raw["redirect"]
        re.compile(r["from"])
        redirect = Redirect(r["from"], tuple(r["to"]))
    return Component(
        id=raw["id"],
        name=raw["name"],
        repo=raw["repo"],
        asset=raw["asset"],
        kind=kind,
        default=bool(raw.get("default", False)),
        dest=raw.get("dest", ""),
        redirect=redirect,
    )


def load_manifest(path=DEFAULT_MANIFEST):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    comps = tuple(_component(c) for c in raw["components"])
    ids = [c.id for c in comps]
    if len(ids) != len(set(ids)):
        raise ManifestError("duplicate component id")
    layout = raw.get("layout", {})
    return Manifest(
        components=comps,
        manual=tuple(raw.get("manual", ())),
        dirs=tuple(layout.get("dirs", ())),
        files=tuple(layout.get("files", ())),
        volatile=frozenset(raw.get("volatile", ())),
    )
