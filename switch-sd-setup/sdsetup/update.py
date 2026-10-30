"""Update an existing card: scan versions, snapshot what will change, update, remove leftovers, roll back."""
import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import CONFIG_FILES
from .fetch import FetchError, sha256_file
from .install import RECEIPT, install_components, load_receipt
from .verify import ERROR, INFO, verify_card
from .versions import detect, fingerprint

SNAPSHOT_META = "snapshot.json"
MAX_SNAPSHOT_FILE = 20_000_000
# Settings the user may have edited; saved with every snapshot so a rollback can bring them back.
CONFIG_PATHS = ("bootloader/hekate_ipl.ini", "bootloader/nyx.ini", "bootloader/ini", "atmosphere/config",
                "atmosphere/hosts", "emummc/emummc.ini", RECEIPT)


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class Status:
    component: object
    state: str  # up-to-date | behind | missing | modified | unchecked | optional-missing
    installed: str = None
    latest: str = None
    files: dict = field(default_factory=dict)  # fingerprint of the newest release: what an update writes
    detail: str = ""

    @property
    def needs_update(self):
        return self.state in ("behind", "missing", "modified")


# ---------------------------------------------------------------- scan
def scan_card(card, components, provider, vault, volatile=(), log=print):
    card = Path(card)
    receipt = _safe_receipt(card) or {"components": {}}
    out = []
    for c in components:
        if not c.default and c.kind == "file" and not (card / c.dest).is_file():
            out.append(Status(c, "optional-missing", detail="optional, not installed"))
            continue
        latest_tag, latest_fp, cands = None, {}, []
        try:
            res = provider.get(c)
            latest_tag, latest_fp = res.tag, fingerprint(c, res.path, volatile)
            cands.append((latest_tag, latest_fp))
        except FetchError as e:
            log(f"[{c.id}] could not get the newest release: {e}")
        known = {latest_tag}
        # What this tool installed earlier is a candidate too, so an older version is recognised without the vault.
        entry = receipt["components"].get(c.id) or {}
        if entry.get("tag") and entry.get("files") and entry["tag"] not in known:
            cands.append((entry["tag"], {r: h for r, h in entry["files"].items() if r not in volatile}))
            known.add(entry["tag"])
        for tag in vault.tags(c.id):
            if tag in known:
                continue
            try:
                cands.append((tag, fingerprint(c, vault.get(c, tag).path, volatile)))
            except FetchError:
                pass
        d = detect(card, cands)
        if d.state == "missing":
            out.append(Status(c, "missing", None, latest_tag, latest_fp, "not installed"))
        elif d.state == "identified":
            if latest_tag is None:
                out.append(Status(c, "unchecked", d.tag, None, {}, "could not check for a newer release"))
            elif d.tag == latest_tag and d.diffs:
                # The version is right but files are damaged, missing or edited: an update repairs them.
                shown = ", ".join(Path(r).name for r in d.diffs[:3]) + (" ..." if len(d.diffs) > 3 else "")
                out.append(Status(c, "modified", d.tag, latest_tag, latest_fp,
                                  f"{len(d.diffs)} file(s) differ from {d.tag}: {shown}"))
            elif d.tag == latest_tag:
                out.append(Status(c, "up-to-date", d.tag, latest_tag, latest_fp))
            else:
                out.append(Status(c, "behind", d.tag, latest_tag, latest_fp))
        else:
            detail = (f"mostly {d.tag}, with changed or missing files" if d.state == "partial"
                      else "differs from every known release")
            out.append(Status(c, "modified", d.tag if d.state == "partial" else None, latest_tag, latest_fp, detail))
    return out


_VERDICT = {
    "up-to-date": "up to date", "behind": "update available", "missing": "not installed (will be added)",
    "modified": "files differ from the release", "unchecked": "newest version unknown (offline)",
    "optional-missing": "not installed (optional)",
}


def status_lines(statuses):
    lines = [f"  {'Component':<16}{'On card':<13}{'Newest':<13}Status"]
    for s in statuses:
        short = s.component.name.split(" (")[0]  # "nxdumptool (dump your own ...)" -> "nxdumptool"
        lines.append(f"  {short:<16}{s.installed or '-':<13}{s.latest or '-':<13}{_VERDICT[s.state]}")
        if s.state == "modified" and s.detail:
            lines.append(f"      -> {s.detail}")
    return lines


# ---------------------------------------------------------------- snapshot / rollback
def _config_files(card):
    out = set()
    for rel in CONFIG_PATHS:
        p = card / rel
        if p.is_file():
            out.add(rel)
        elif p.is_dir():
            out |= {f.relative_to(card).as_posix() for f in p.rglob("*") if f.is_file()}
    return out


def take_snapshot(card, rels, root, *, label="update", now=None):
    """Copy the files an update will overwrite, plus the user's settings, to the PC. Returns (path, meta)."""
    card = Path(card)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    snap, n = Path(root) / stamp, 1
    while snap.exists():
        snap, n = Path(root) / f"{stamp}_{n}", n + 1
    saved = {}
    for rel in sorted(set(rels) | _config_files(card)):
        src = card / rel
        if src.is_file() and src.stat().st_size <= MAX_SNAPSHOT_FILE:
            dst = snap / "files" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            saved[rel] = sha256_file(dst)
    snap.mkdir(parents=True, exist_ok=True)
    meta = {"created": time.time(), "label": label, "card": str(card), "saved": saved,
            "new_files": {}, "removed": {}, "components": {}}
    write_meta(snap, meta)
    return snap, meta


def write_meta(snap, meta):
    (Path(snap) / SNAPSHOT_META).write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")


def read_meta(snap):
    path = Path(snap) / SNAPSHOT_META
    if not path.is_file():
        raise UpdateError(f"not a snapshot folder (no {SNAPSHOT_META}): {snap}")
    return json.loads(path.read_text(encoding="utf-8"))


def latest_snapshot(root):
    """Newest snapshot that has not been rolled back yet, or None."""
    root = Path(root)
    if not root.is_dir():
        return None
    for d in sorted((p for p in root.iterdir() if (p / SNAPSHOT_META).is_file()), reverse=True):
        if not read_meta(d).get("rolled_back"):
            return d
    return None


def _prune_empty(card, rel):
    parent = (card / rel).parent
    while parent != card and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def rollback(card, snap, log=print):
    """Put the card back as it was before the update. Returns a list of problems (empty = clean)."""
    card, snap = Path(card), Path(snap)
    meta = read_meta(snap)
    problems = []
    for rel, h in {**meta["saved"], **meta["removed"]}.items():
        folder = "removed" if rel in meta["removed"] else "files"
        src = snap / folder / rel
        if not src.is_file() or sha256_file(src) != h:
            problems.append(f"snapshot copy of {rel} is missing or damaged; left as it is")
            continue
        dst = card / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    for rel, h in meta["new_files"].items():
        p = card / rel
        if not p.is_file():
            continue
        if sha256_file(p) == h:
            p.unlink()
            _prune_empty(card, rel)
        else:
            problems.append(f"kept {rel}: it changed after the update")
    log(f"restored {len(meta['saved']) + len(meta['removed'])} file(s), removed {len(meta['new_files'])} added by the update")
    meta["rolled_back"] = time.time()
    write_meta(snap, meta)
    return problems


# ---------------------------------------------------------------- stale files
def find_stale(old_receipt, new_receipt, component_ids, card):
    """Files an earlier install of these components wrote that the new release no longer has.

    Only files still identical to what the tool wrote are returned as removable; edited ones are listed apart.
    """
    card = Path(card)
    all_new = {rel for entry in new_receipt["components"].values() for rel in entry["files"]}
    stale, edited = {}, []
    for cid in component_ids:
        old = ((old_receipt or {}).get("components", {}).get(cid) or {}).get("files", {})
        for rel, h in old.items():
            p = card / rel
            if rel in all_new or not p.is_file():
                continue
            if sha256_file(p) == h:
                stale[rel] = h
            else:
                edited.append(rel)
    return stale, edited


def remove_stale(card, stale, snap, meta):
    card = Path(card)
    for rel, h in stale.items():
        dst = Path(snap) / "removed" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(card / rel, dst)
        (card / rel).unlink()
        _prune_empty(card, rel)
        meta["removed"][rel] = h


# ---------------------------------------------------------------- the whole flow
def _safe_receipt(card):
    try:
        return load_receipt(card)
    except (OSError, ValueError):
        return None


def run_update(manifest, io, provider, card, vault, *, snapshot_root, only=(), dry_run=False,
               assume_yes=False, remove_leftovers=None):
    """Scan, show what is behind, snapshot, update, optionally clean up. Returns an exit code."""
    card = Path(card)
    if not card.is_dir():
        raise UpdateError(f"not a folder: {card}")
    io.say(f"Checking what is on {card} ...")
    comps = [c for c in manifest.components if not only or c.id in only]
    statuses = scan_card(card, comps, provider, vault, manifest.volatile, log=lambda m: io.say("  " + m))
    io.say("")
    for line in status_lines(statuses):
        io.say(line)

    todo = [s for s in statuses if s.needs_update and s.files]
    for s in (s for s in statuses if s.needs_update and not s.files):
        io.say(f"  {s.component.name}: no release available (offline and not saved in the vault); skipped")
    if not todo:
        io.say("\nNothing to update.")
        return 0
    io.say("\nWill update: " + ", ".join(s.component.name for s in todo))
    if dry_run:
        io.say("Dry run: nothing was changed.")
        return 0
    if not assume_yes and not io.confirm("Save a snapshot of the affected files, then update now?", True):
        io.say("Cancelled; nothing was changed.")
        return 1

    tracked = {rel for s in todo for rel in s.files} | {RECEIPT, *CONFIG_FILES}
    existed = {rel for rel in tracked if (card / rel).exists()}
    old_receipt = _safe_receipt(card)
    snap, meta = take_snapshot(card, tracked, snapshot_root)
    io.say(f"Snapshot saved: {snap}")

    receipt = install_components([s.component for s in todo], card, provider, log=lambda m: io.say("  " + m))
    meta["components"] = {s.component.id: {"before": s.installed, "after": receipt["components"][s.component.id]["tag"]}
                          for s in todo}
    meta["new_files"] = {rel: sha256_file(card / rel) for rel in sorted(tracked - existed) if (card / rel).is_file()}

    stale, edited = find_stale(old_receipt, receipt, [s.component.id for s in todo], card)
    for rel in edited:
        io.say(f"  kept {rel}: left over from an old version but you changed it")
    if stale:
        io.say(f"\n{len(stale)} file(s) from the old versions are no longer part of the new release:")
        for rel in list(stale)[:10]:
            io.say(f"  {rel}")
        if remove_leftovers is None:
            remove_leftovers = (not assume_yes) and io.confirm(
                "Remove them? They are moved into the snapshot, so a rollback restores them.", False)
        if remove_leftovers:
            remove_stale(card, stale, snap, meta)
            io.say(f"  removed {len(stale)} file(s)")
    elif old_receipt is None:
        io.say("\n(Leftover-file cleanup needs an install record; this update just created one.)")
    write_meta(snap, meta)

    findings = verify_card(card, manifest)
    problems = [f for f in findings if f.level != INFO]
    for f in problems:
        io.say(f"  {f.level.upper():5} {f.code}: {f.message}")
    errors = any(f.level == ERROR for f in problems)
    io.say("\nUpdated." if not errors else "\nUpdated, but the check found errors (see above).")
    io.say(f"To undo this update: sdsetup rollback {card}  (snapshot: {snap})")
    return 1 if errors else 0


def run_rollback(io, card, snapshot_root, *, snapshot=None, assume_yes=False):
    card = Path(card)
    snap = Path(snapshot) if snapshot else latest_snapshot(snapshot_root)
    if snap is None:
        io.say("No snapshot to roll back to.")
        return 1
    meta = read_meta(snap)
    if meta.get("rolled_back"):
        io.say(f"{snap} was already rolled back.")
        return 1
    parts = ", ".join(f"{cid} {v['after']} -> {v['before'] or 'nothing'}" for cid, v in meta["components"].items())
    io.say(f"Snapshot {snap.name}: {parts or 'no components recorded'}")
    if not assume_yes and not io.confirm("Roll the card back to this snapshot?", False):
        io.say("Cancelled; nothing was changed.")
        return 1
    problems = rollback(card, snap, log=lambda m: io.say("  " + m))
    for p in problems:
        io.say(f"  WARN {p}")
    io.say("Rolled back." if not problems else "Rolled back, with the warnings above.")
    return 0 if not problems else 1
