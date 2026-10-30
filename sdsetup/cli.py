import argparse
import os
import re
import sys
from pathlib import Path

from . import SUPPORT_URL, __version__
from .backup import BackupError, backup_card
from .doctor import run_doctor, summarize
from .drives import DriveError, get_drive, list_drives, safety_problems
from .fetch import FetchError, GithubProvider
from .format import FILESYSTEMS, FormatError, confirmation_phrase, format_drive
from .install import InstallError, install_components
from .manifest import APP_DIR, DEFAULT_MANIFEST, ManifestError, load_manifest
from .repair import RepairError, run_clean_boot, run_restore
from .update import UpdateError, run_rollback, run_update
from .vault import DEFAULT_VAULT, FallbackProvider, Vault, update_vault
from .verify import ERROR, WARN, verify_card
from .wizard import Console, run_wizard

DEFAULT_SNAPSHOT_ROOT = Path.home() / "SwitchSD-backups" / "updates"

_DRIVE_ROOT = re.compile(r"^[A-Za-z]:[\\/]?$")


def _default_cache():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "sdsetup" / "cache"


def _progress(name, done, total):
    if total:
        print(f"\r  {name}: {done * 100 // total}%", end="", flush=True)
        if done >= total:
            print()


def build_parser():
    p = argparse.ArgumentParser(prog="sdsetup", description="Switch SD card setup assistant")
    p.add_argument("--version", action="version", version=f"sdsetup {__version__}")
    p.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    p.add_argument("--cache", default=str(_default_cache()))
    p.add_argument("--vault", default=str(DEFAULT_VAULT), help="folder of saved known-good releases")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("components", help="list components and manual steps")
    sub.add_parser("drives", help="list drives and whether each is safe to use as a card")
    sub.add_parser("gui", help="open the desktop window")
    sub.add_parser("about", help="version, license and how to support the project")

    def source(sp):
        sp.add_argument("--source", choices=["latest", "vault"], default="latest",
                        help="latest: newest from GitHub, saved copy if GitHub fails (default); "
                             "vault: only the saved known-good versions, no network")

    def selectors(sp):
        sp.add_argument("--with", dest="with_ids", action="append", default=[], metavar="ID",
                        help="also include an optional component")
        sp.add_argument("--without", dest="without_ids", action="append", default=[], metavar="ID",
                        help="skip a default component")

    f = sub.add_parser("fetch", help="download and verify releases into the cache")
    selectors(f)
    source(f)

    i = sub.add_parser("install", help="install onto a card folder or drive root")
    i.add_argument("target")
    selectors(i)
    i.add_argument("--dry-run", action="store_true")
    i.add_argument("--reset-config", action="store_true", help="overwrite hekate_ipl.ini and hosts file")
    source(i)

    v = sub.add_parser("verify", help="check a card against the manifest")
    v.add_argument("target")

    d = sub.add_parser("doctor", help="full health check of a card: install checks plus the issue catalogue")
    d.add_argument("target", help="drive root (E:\\) or card folder")
    d.add_argument("--firmware", help="console firmware, e.g. 23.0.0 (System Settings or hekate Console info)")
    d.add_argument("--ams-version", help="Atmosphere release tag; defaults to the install receipt")
    d.add_argument("--nand-backup", action="append", default=[], metavar="DIR",
                   help="also check a NAND backup folder on the PC (repeatable)")
    d.add_argument("--nsp", action="append", default=[], metavar="PATH",
                   help="inspect tickets in an .nsp file or folder (repeatable)")

    def snapshot_root(sp):
        sp.add_argument("--snapshot-root", default=str(DEFAULT_SNAPSHOT_ROOT),
                        help="PC folder where update snapshots are kept")

    u = sub.add_parser("update", help="update an existing card: show what is behind, snapshot, update")
    u.add_argument("target", help="drive root (E:\\) or card folder")
    u.add_argument("--only", action="append", default=[], metavar="ID", help="update just this component")
    u.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    u.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    u.add_argument("--remove-leftovers", action="store_true",
                   help="also remove files that old versions installed and the new release dropped")
    snapshot_root(u)
    source(u)

    rb = sub.add_parser("rollback", help="undo the last update using its snapshot")
    rb.add_argument("target", help="drive root (E:\\) or card folder")
    rb.add_argument("--snapshot", help="a specific snapshot folder (default: the newest not yet rolled back)")
    rb.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    snapshot_root(rb)

    rp = sub.add_parser("repair", help="clean-boot test: switch add-ons off, or back on")
    rp.add_argument("target", help="drive root (E:\\) or card folder")
    mode = rp.add_mutually_exclusive_group(required=True)
    mode.add_argument("--clean-boot", action="store_true", help="switch off third-party add-ons and patches")
    mode.add_argument("--restore", action="store_true", help="switch them back on")
    rp.add_argument("--dry-run", action="store_true")
    rp.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    b = sub.add_parser("backup", help="copy the whole card to a folder on the PC")
    b.add_argument("drive", help="drive letter, e.g. E:")
    b.add_argument("--to", required=True, help="folder on the PC to hold the backup")
    b.add_argument("--verify-hash", action="store_true", help="also compare SHA-256 of every file (slow)")
    b.add_argument("--essential", action="store_true",
                   help="skip folders and files over 1 GB (games, emuMMC, NAND dumps); fine before an update, not before a format")

    fm = sub.add_parser("format", help="quick-format a removable drive (erases everything)")
    fm.add_argument("drive", help="drive letter, e.g. E:")
    fm.add_argument("--fs", choices=sorted(FILESYSTEMS), default="exfat")
    fm.add_argument("--label", default="SWITCH SD")
    fm.add_argument("--backup-dir", help="folder printed by the backup command for this card")
    fm.add_argument("--skip-backup", action="store_true", help="erase without a backup (cannot be undone)")
    fm.add_argument("--confirm", help='exact phrase, e.g. "ERASE E:"; prompted when omitted')
    fm.add_argument("--dry-run", action="store_true", help="run every check, print the command, erase nothing")

    w = sub.add_parser("wizard", help="guided setup: pick card, back up, format, install, verify, checklist")
    w.add_argument("--backup-root", help="PC folder for backups and the checklist (default: ~/SwitchSD-backups)")
    w.add_argument("--dry-run", action="store_true", help="walk through every step but write nothing to the card")
    source(w)

    vp = sub.add_parser("vault", help="manage the saved known-good releases")
    vp.add_argument("action", choices=["update", "status", "verify"],
                    help="update: save the current releases; status: list saved copies; verify: check their hashes")
    vp.add_argument("--only", action="append", default=[], metavar="ID", help="limit update to a component")
    return p


def make_provider(args, log=print):
    """Provider for fetch/install/wizard: GitHub with vault fallback, or the vault alone."""
    vault = Vault(args.vault)
    if args.source == "vault":
        return vault
    return FallbackProvider(GithubProvider(args.cache, progress=_progress), vault, log)


def _protected(args):
    return [args.cache, args.manifest, os.getcwd(), str(APP_DIR)]


def _target_path(target):
    """'E:' alone means 'current folder on E:' to Windows; make it the drive root."""
    return f"{target[0].upper()}:\\" if _DRIVE_ROOT.match(target) else target


def _guard_install_target(target, args):
    """A bare drive letter must be a removable, non-system drive; plain folders are allowed."""
    if not _DRIVE_ROOT.match(target):
        return
    drive = get_drive(target)
    problems = safety_problems(drive, protected_paths=_protected(args))
    if problems:
        raise InstallError("refusing: " + "; ".join(problems))


MENU = {
    "1": ("Guided setup, dry run (writes nothing)", ["wizard", "--dry-run"]),
    "2": ("Guided setup (real)", ["wizard"]),
    "3": ("List drives", ["drives"]),
    "4": ("Check a card (doctor)", None),
    "5": ("Saved known-good versions (vault)", ["vault", "status"]),
    "6": ("Update an existing card", None),
    "7": ("Roll back the last update", None),
    "8": ("Clean-boot test (switch add-ons off)", None),
    "9": ("Switch add-ons back on", None),
}
_TARGET_COMMANDS = {"6": ["update"], "7": ["rollback"], "8": ["repair", "--clean-boot"], "9": ["repair", "--restore"]}


def interactive_menu(io=None, run=None):
    """Shown when the exe is double-clicked or started without arguments."""
    io = io or Console()
    run = run or main
    while True:
        io.say("\nSwitch SD Setup")
        for key, (label, _) in MENU.items():
            io.say(f"  {key}. {label}")
        io.say("  q. Quit")
        choice = io.ask("Choose").strip().lower()
        if choice in ("q", "quit", "exit"):
            return 0
        if choice not in MENU:
            io.say("Not a valid choice.")
            continue
        argv = MENU[choice][1]
        if argv is None:
            target = io.ask("Card drive or folder, e.g. E:\\")
            if not target:
                continue
            if choice in _TARGET_COMMANDS:
                argv = [*_TARGET_COMMANDS[choice], target]
            else:
                argv = ["doctor", target]
                firmware = io.ask("Console firmware, e.g. 23.0.0 (Enter to skip)")
                if firmware:
                    argv += ["--firmware", firmware]
        try:
            code = run(argv)
        except KeyboardInterrupt:
            code = 130
        io.say(f"(finished, exit code {code})")


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
        if not argv:
            return interactive_menu()
    args = build_parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        return _run(args, manifest)
    except (ManifestError, FetchError, InstallError, DriveError, BackupError, FormatError,
            UpdateError, RepairError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def _run(args, manifest):
    if args.cmd == "components":
        for c in manifest.components:
            print(f"{c.id:12} {'default ' if c.default else 'optional'}  {c.name}  ({c.repo})")
        for m in manifest.manual:
            print(f"{m['id']:12} manual    {m['name']}\n             {m['note']}")
        return 0

    if args.cmd == "about":
        print(f"Switch SD Setup {__version__}")
        print("Free and open source (MIT). Not affiliated with Nintendo.")
        print(f"If it saved you time, you can buy me a coffee: {SUPPORT_URL}")
        return 0

    if args.cmd == "gui":
        from .gui import main as gui_main
        return gui_main()

    if args.cmd == "wizard":
        io = Console()
        return run_wizard(manifest, io, make_provider(args, lambda m: io.say("  " + m)),
                          backup_root=args.backup_root, protected_paths=_protected(args), dry_run=args.dry_run)

    if args.cmd == "vault":
        vault = Vault(args.vault)
        if args.action == "update":
            comps = [manifest.component(c) for c in args.only] if args.only else list(manifest.components)
            failures = update_vault(vault, GithubProvider(args.cache, progress=_progress), comps)
            print(f"{len(comps) - len(failures)} saved, {len(failures)} failed")
            return 1 if failures else 0
        if args.action == "status":
            rows = vault.rows()
            for r in rows:
                note = "" if r.upstream_digest else "  (no upstream sha256: hash recorded at save time)"
                print(f"{r.component_id:12} {r.tag:24} {r.asset}  {r.size / 1e6:.1f} MB  saved {r.saved}{note}")
            if not rows:
                print(f"vault is empty ({args.vault}); run: sdsetup vault update")
            return 0
        problems = vault.verify()
        for p in problems:
            print(f"ERROR {p}")
        print(f"{len(problems)} problem(s)" if problems else "vault OK")
        return 1 if problems else 0

    if args.cmd in ("update", "rollback", "repair"):
        _guard_install_target(args.target, args)
        target = _target_path(args.target)
        io = Console()
        if args.cmd == "update":
            return run_update(manifest, io, make_provider(args, lambda m: io.say("  " + m)), target,
                              Vault(args.vault), snapshot_root=args.snapshot_root, only=args.only,
                              dry_run=args.dry_run, assume_yes=args.yes,
                              remove_leftovers=True if args.remove_leftovers else None)
        if args.cmd == "rollback":
            return run_rollback(io, target, args.snapshot_root, snapshot=args.snapshot, assume_yes=args.yes)
        if args.clean_boot:
            return run_clean_boot(io, target, dry_run=args.dry_run, assume_yes=args.yes)
        return run_restore(io, target)

    if args.cmd == "doctor":
        findings = run_doctor(args.target, manifest, firmware=args.firmware, ams_version=args.ams_version,
                              nand_dirs=args.nand_backup, nsp_paths=args.nsp)
        for fd in findings:
            print(f"{fd.level.upper():5} {fd.code}: {fd.message}")
            if fd.hint:
                print(f"      fix: {fd.hint}")
        errors, warns = summarize(findings)
        print(f"{errors} error(s), {warns} warning(s)")
        return 1 if errors else 0

    if args.cmd == "drives":
        for d in list_drives():
            problems = safety_problems(d, protected_paths=_protected(args))
            status = "OK for card use" if not problems else "NOT SAFE: " + "; ".join(problems)
            print(f"{d.describe()} [{d.kind}]  {status}")
        return 0

    if args.cmd == "backup":
        drive = get_drive(args.drive)
        problems = safety_problems(drive, protected_paths=[args.cache, args.to])
        if problems:
            raise BackupError("refusing: " + "; ".join(problems))
        print(f"backing up {drive.describe()}")
        res = backup_card(drive.root, args.to, drive=drive, verify_hash=args.verify_hash, essential=args.essential,
                          progress=lambda n, b, t: print(f"\r  {n} files, {b / 1e6:.0f} MB", end="", flush=True))
        print()
        for e in res.errors[:10]:
            print(f"ERROR {e}")
        print(f"{'backup OK' if res.ok else 'backup FAILED'}: {res.files} files, {res.bytes / 1e6:.0f} MB -> {res.path}")
        for name, size in res.excluded:
            print(f"not copied: {name} ({size / 1e9:.1f} GB)")
        if res.ok and res.mode == "full":
            print(f'next: sdsetup format {drive.letter}: --backup-dir "{res.path}"')
        elif res.ok:
            print("this is a partial backup: fine before an update, but a format needs a full backup")
        return 0 if res.ok else 1

    if args.cmd == "format":
        drive = get_drive(args.drive)
        phrase = confirmation_phrase(drive)
        confirm = args.confirm
        if confirm is None and not args.dry_run:
            print(f"This ERASES everything on {drive.describe()}.")
            confirm = input(f"Type {phrase} to continue: ").strip()
        elif confirm is None:
            confirm = phrase  # dry run: nothing is erased, so exercise the remaining gates
        cmd = format_drive(args.drive, args.fs, args.label, confirmation=confirm,
                           backup_dir=args.backup_dir, skip_backup=args.skip_backup,
                           dry_run=args.dry_run, protected_paths=_protected(args))
        print(("dry run, would run: " if args.dry_run else "formatted with: ") + " ".join(cmd[:3]) + " ...")
        return 0

    if args.cmd in ("fetch", "install"):
        comps = manifest.select(args.with_ids, args.without_ids)
        provider = make_provider(args)
        if args.cmd == "fetch":
            for c in comps:
                r = provider.get(c)
                print(f"{c.id}: {r.tag} {r.asset_name} sha256={r.sha256[:12]}… "
                      f"{'verified' if r.digest_verified else 'NO PUBLISHED DIGEST'}")
            return 0
        _guard_install_target(args.target, args)
        install_components(comps, args.target, provider, dry_run=args.dry_run, reset_config=args.reset_config)
        for m in manifest.manual:
            print(f"manual step: {m['name']}: {m['note']}")
        return 0

    findings = verify_card(args.target, manifest)
    for fd in findings:
        print(f"{fd.level.upper():5} {fd.code}: {fd.message}")
    errors = sum(f.level == ERROR for f in findings)
    warns = sum(f.level == WARN for f in findings)
    print(f"{errors} error(s), {warns} warning(s)")
    return 1 if errors else 0
