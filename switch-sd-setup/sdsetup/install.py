import json
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from .config import write_default_config
from .fetch import sha256_file

RECEIPT = "config/sdsetup/receipt.json"


class InstallError(Exception):
    pass


def safe_relpath(name):
    """Normalise a zip member / destination name; reject anything escaping the card root."""
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or (p.parts and p.parts[0].endswith(":")) or ".." in p.parts:
        raise InstallError(f"unsafe path: {name!r}")
    return str(p)


def _write(target, rel, data, dry_run):
    dest = Path(target) / rel
    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def install_zip(zip_path, target, *, redirect=None, dry_run=False):
    """Extract zip onto target. Returns {relpath: sha256} of files written."""
    import hashlib

    written = {}
    rx = re.compile(redirect.pattern) if redirect else None
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            rel = safe_relpath(info.filename)
            if info.is_dir():
                if not dry_run:
                    (Path(target) / rel).mkdir(parents=True, exist_ok=True)
                continue
            data = zf.read(info)
            digest = hashlib.sha256(data).hexdigest()
            dests = list(redirect.to) if rx and "/" not in rel and rx.search(rel) else [rel]
            for d in dests:
                d = safe_relpath(d)
                _write(target, d, data, dry_run)
                written[d] = digest
    return written


def install_file(src, target, dest, *, dry_run=False):
    rel = safe_relpath(dest)
    data = Path(src).read_bytes()
    _write(target, rel, data, dry_run)
    return {rel: sha256_file(src)}


def load_receipt(target):
    path = Path(target) / RECEIPT
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _existing_receipt(target):
    try:
        receipt = load_receipt(target)
    except (OSError, ValueError):
        receipt = None
    if not isinstance(receipt, dict) or not isinstance(receipt.get("components"), dict):
        return {"components": {}}
    return receipt


def install_components(components, target, provider, *, dry_run=False, reset_config=False, log=print):
    """Install each component, write config and receipt. Returns the receipt dict."""
    target = Path(target)
    if not target.is_dir():
        raise InstallError(f"target is not a directory: {target}")
    receipt = _existing_receipt(target)  # keep entries of components this run does not touch
    for comp in components:
        log(f"[{comp.id}] resolving {comp.repo}")
        res = provider.get(comp)
        if not res.digest_verified:
            log(f"[{comp.id}] warning: no upstream sha256 available for this file (hash recorded locally only)")
        if comp.kind == "zip":
            files = install_zip(res.path, target, redirect=comp.redirect, dry_run=dry_run)
        else:
            files = install_file(res.path, target, comp.dest, dry_run=dry_run)
        log(f"[{comp.id}] {res.tag}: {len(files)} file(s){' (dry run)' if dry_run else ''}")
        receipt["components"][comp.id] = {
            "tag": res.tag,
            "asset": res.asset_name,
            "digest_verified": res.digest_verified,
            "files": files,
        }
    for line in write_default_config(target, reset=reset_config, dry_run=dry_run):
        log(line)
    if not dry_run:
        rp = target / RECEIPT
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    return receipt
