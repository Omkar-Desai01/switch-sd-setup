"""Clean-boot test: switch off every add-on so the console can be tried with a minimal setup, then switch them back on.

Nothing is deleted. Folders are renamed in place (instant, same drive) and the moves are recorded on the card,
so restore can put each one back even after a reboot or a new session.
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

# Third-party sysmodules and mods live in contents/; patch folders are the usual cause of boot problems
# after a firmware update because they target specific firmware builds.
ADDON_FOLDERS = ("atmosphere/contents", "atmosphere/exefs_patches", "atmosphere/kip_patches")
STATE = "config/sdsetup/quarantine.json"


class RepairError(Exception):
    pass


def _load(card):
    p = Path(card) / STATE
    if not p.is_file():
        return {"moves": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data.get("moves"), list) else {"moves": []}
    except (OSError, ValueError):
        return {"moves": []}


def _save(card, state):
    p = Path(card) / STATE
    if not state["moves"]:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def active_addons(card):
    """{folder: [entry names]} for add-on folders that currently have content."""
    out = {}
    for rel in ADDON_FOLDERS:
        d = Path(card) / rel
        if d.is_dir():
            names = sorted(p.name for p in d.iterdir())
            if names:
                out[rel] = names
    return out


def disabled_moves(card):
    return _load(card)["moves"]


def clean_boot(card, *, now=None, dry_run=False):
    """Rename each non-empty add-on folder to <name>.disabled-<stamp>. Returns [(from, to)]."""
    card = Path(card)
    if not card.is_dir():
        raise RepairError(f"not a folder: {card}")
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    state, done = _load(card), []
    for rel in active_addons(card):
        new = f"{rel}.disabled-{stamp}"
        done.append((rel, new))
        if dry_run:
            continue
        (card / rel).rename(card / new)
        state["moves"].append({"from": rel, "to": new})
    if not dry_run:
        _save(card, state)
    return done


def restore_addons(card):
    """Undo clean_boot. Returns (restored [(from, to)], problems [str])."""
    card = Path(card)
    state = _load(card)
    restored, problems, remaining = [], [], []
    for move in reversed(state["moves"]):
        src, dst = card / move["to"], card / move["from"]
        if not src.is_dir():
            problems.append(f"{move['to']} is gone; nothing to restore for {move['from']}")
            continue
        if not dst.exists():
            src.rename(dst)
            restored.append((move["to"], move["from"]))
            continue
        # The folder was recreated since (Atmosphere or the user): merge the old content back in.
        clash = []
        for child in list(src.iterdir()):
            target = dst / child.name
            if target.exists():
                clash.append(child.name)
            else:
                shutil.move(str(child), str(target))
        if clash:
            problems.append(f"{move['from']}: kept {', '.join(clash)} in {move['to']} (name already in use)")
            remaining.append(move)
        else:
            src.rmdir()
            restored.append((move["to"], move["from"]))
    state["moves"] = list(reversed(remaining))
    _save(card, state)
    return restored, problems


def run_clean_boot(io, card, *, dry_run=False, assume_yes=False):
    active = active_addons(card)
    if not active:
        io.say("No add-ons are active (contents, exefs_patches and kip_patches are empty or missing).")
        return 0
    io.say("Add-ons that will be switched off for the test:")
    for rel, names in active.items():
        shown = ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
        io.say(f"  {rel}: {shown}")
    io.say("This also switches off game cheats and mods stored there. Nothing is deleted; you can switch them back on.")
    if dry_run:
        io.say("Dry run: nothing was changed.")
        return 0
    if not assume_yes and not io.confirm("Switch them off now?", True):
        io.say("Cancelled; nothing was changed.")
        return 1
    for src, dst in clean_boot(card):
        io.say(f"  {src} -> {dst}")
    io.say("\nNow start the console from hekate. If it boots, one of the add-ons is the problem:")
    io.say("update them, then switch them back on (sdsetup repair --restore) and re-test.")
    return 0


def run_restore(io, card):
    if not disabled_moves(card):
        io.say("Nothing was switched off by the clean-boot test.")
        return 0
    restored, problems = restore_addons(card)
    for src, dst in restored:
        io.say(f"  {src} -> {dst}")
    for p in problems:
        io.say(f"  WARN {p}")
    io.say("Add-ons are back on." if not problems else "Add-ons restored, with the warnings above.")
    return 0 if not problems else 1
