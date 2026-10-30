import re
from pathlib import Path

from .compat import check_firmware, load_compat
from .emummc import check_emummc, windows_partitions
from .install import load_receipt
from .nand import check_nand_backups
from .reports import check_addons, check_reports
from .ticket import nsp_files, ticket_findings
from .verify import ERROR, WARN, Finding, verify_card

_DRIVE_ROOT = re.compile(r"^([A-Za-z]):[\\/]?$")


def _dns_log_check(root):
    log = Path(root) / "atmosphere" / "logs" / "dns_mitm_startup.log"
    if log.is_file() and "emummc.txt" not in log.read_text(encoding="utf-8", errors="replace"):
        return [Finding(WARN, "dns-emummc-not-selected", "DNS.MITM startup log does not mention hosts/emummc.txt",
                        "Boot the emuMMC once and check again; make sure atmosphere/hosts/emummc.txt exists.")]
    return []


def run_doctor(target, manifest, *, firmware=None, ams_version=None, nand_dirs=(), nsp_paths=(),
               partitions=windows_partitions, compat=None):
    """Full health check of a card: the install checks plus the issue catalogue. Returns [Finding]."""
    root = Path(target)
    findings = verify_card(root, manifest)
    if not root.is_dir():
        return findings
    m = _DRIVE_ROOT.match(str(target))
    letter = m.group(1).upper() if m else None

    findings += check_emummc(root, letter=letter, partitions=partitions)
    findings += _dns_log_check(root)
    findings += check_reports(root)
    findings += check_addons(root)

    if (root / "backup").is_dir():
        findings += check_nand_backups(root / "backup", "card backup")
    for d in nand_dirs:
        findings += check_nand_backups(d, f"backup {d}")

    if firmware:
        if not ams_version:
            receipt = load_receipt(root)
            ams_version = receipt["components"].get("atmosphere", {}).get("tag") if receipt else None
        findings += check_firmware(firmware, ams_version, compat or load_compat())

    for p in nsp_paths:
        files = nsp_files(p)
        if not files:
            findings.append(Finding(WARN, "nsp-none", f"no .nsp files found at {p}"))
        for f in files:
            findings += ticket_findings(f)
    return findings


def summarize(findings):
    errors = sum(f.level == ERROR for f in findings)
    warns = sum(f.level == WARN for f in findings)
    return errors, warns
