"""Local copies of known-good releases, used when GitHub or a repo is unavailable (or when pinned versions are wanted)."""
import json
import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .fetch import FetchError, Resolved, sha256_file
from .manifest import APP_DIR

DEFAULT_VAULT = APP_DIR / "vault"
INDEX = "index.json"
KEEP_TAGS = 2  # newest tag plus one previous known-good


def _safe(name):
    """Tags such as '12/02/2025' are not safe path parts."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip(".") or "_"
    return cleaned


@dataclass(frozen=True)
class VaultRow:
    component_id: str
    tag: str
    asset: str
    size: int
    saved: str
    upstream_digest: bool


class Vault:
    def __init__(self, root=DEFAULT_VAULT):
        self.root = Path(root)

    # -- index ---------------------------------------------------------
    def _load(self):
        path = self.root / INDEX
        if not path.is_file():
            return {"components": {}}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise FetchError(f"vault index is corrupt: {e}") from e

    def _save(self, data):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / INDEX).write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    # -- write ---------------------------------------------------------
    def add(self, component, resolved):
        """Copy a verified download into the vault and make it the current version of that component."""
        asset = Path(resolved.asset_name).name
        rel = f"{_safe(component.id)}/{_safe(resolved.tag)}/{asset}"
        dest = self.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resolved.path, dest)
        sha = sha256_file(dest)
        if sha != resolved.sha256:
            dest.unlink()
            raise FetchError(f"{component.id}: copy into vault changed the file")
        data = self._load()
        entry = data["components"].setdefault(component.id, {"current": None, "releases": {}})
        seq = 1 + max((r.get("seq", 0) for r in entry["releases"].values()), default=0)
        entry["releases"][resolved.tag] = {
            "seq": seq, "path": rel, "asset": asset, "sha256": sha, "size": dest.stat().st_size,
            "repo": component.repo, "saved": date.today().isoformat(),
            "upstream_digest": bool(resolved.digest_verified),
        }
        entry["current"] = resolved.tag
        self._prune(entry)
        self._save(data)

    def _prune(self, entry):
        order = sorted(entry["releases"].items(), key=lambda kv: kv[1].get("seq", 0), reverse=True)
        keep = {entry["current"]}
        for tag, _ in order:
            if len(keep) >= KEEP_TAGS:
                break
            keep.add(tag)
        for tag in list(entry["releases"]):
            if tag not in keep:
                gone = self.root / entry["releases"].pop(tag)["path"]
                gone.unlink(missing_ok=True)
                try:
                    gone.parent.rmdir()
                except OSError:
                    pass

    # -- read ----------------------------------------------------------
    def get(self, component, tag=None):
        """Same interface as GithubProvider.get: returns a Resolved for the saved copy, checked against its hash."""
        entry = self._load()["components"].get(component.id)
        if not entry:
            raise FetchError(f"{component.id}: no saved copy in the vault ({self.root})")
        tag = tag or entry["current"]
        rel = entry["releases"].get(tag)
        if not rel:
            raise FetchError(f"{component.id}: vault has no copy of {tag}")
        path = self.root / rel["path"]
        if not path.is_file():
            raise FetchError(f"{component.id}: vault file missing: {rel['path']}")
        if sha256_file(path) != rel["sha256"]:
            raise FetchError(f"{component.id}: vault copy {rel['path']} is corrupt (sha256 differs); run `vault update`")
        return Resolved(component.id, tag, rel["asset"], path, rel["sha256"], rel.get("upstream_digest", False))

    def tags(self, component_id):
        """Saved tags of a component, current first, then newest to oldest."""
        entry = self._load()["components"].get(component_id)
        if not entry:
            return []
        current = entry["current"]
        rest = sorted((t for t in entry["releases"] if t != current),
                      key=lambda t: entry["releases"][t].get("seq", 0), reverse=True)
        return ([current] if current in entry["releases"] else []) + rest

    def rows(self):
        out = []
        for cid, entry in sorted(self._load()["components"].items()):
            for tag, r in entry["releases"].items():
                out.append(VaultRow(cid, tag + (" (current)" if tag == entry["current"] else ""),
                                    r["asset"], r["size"], r["saved"], r.get("upstream_digest", False)))
        return out

    def verify(self):
        """Problems with the stored files (missing, wrong size or hash). Empty list = healthy."""
        problems = []
        for cid, entry in self._load()["components"].items():
            for tag, r in entry["releases"].items():
                p = self.root / r["path"]
                if not p.is_file():
                    problems.append(f"{cid} {tag}: file missing ({r['path']})")
                elif sha256_file(p) != r["sha256"]:
                    problems.append(f"{cid} {tag}: sha256 differs ({r['path']})")
        return problems


class FallbackProvider:
    """Try the primary source (GitHub); if it fails for any reason use the vault copy."""

    def __init__(self, primary, vault, log=print):
        self.primary, self.vault, self.log = primary, vault, log

    def get(self, component):
        try:
            return self.primary.get(component)
        except FetchError as e:
            try:
                res = self.vault.get(component)
            except FetchError as v:
                raise FetchError(f"{e}; and no usable saved copy: {v}") from e
            self.log(f"[{component.id}] online source failed ({e}); using saved copy {res.tag} from the vault")
            return res


def update_vault(vault, provider, components, log=print):
    """Download the current release of each component and save it. Returns the list of failures."""
    failures = []
    for c in components:
        try:
            res = provider.get(c)
            vault.add(c, res)
            log(f"{c.id}: saved {res.tag} ({res.asset_name})")
        except FetchError as e:
            failures.append(f"{c.id}: {e}")
            log(f"{c.id}: FAILED, keeping the old saved copy: {e}")
    return failures
