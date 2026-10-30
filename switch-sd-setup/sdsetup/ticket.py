"""Read-only inspection of the tickets inside an NSP.

This explains why a console would reject a ticket ("common ticket is modified", error 0x291).
It does not alter tickets and does not verify RSA signatures (that needs Nintendo's certificate chain).
"""
import struct
from dataclasses import dataclass
from pathlib import Path

from .verify import ERROR, INFO, WARN, Finding

# signature type -> (signature bytes, padding bytes)
SIG_LAYOUT = {
    0x10000: (0x200, 0x3C), 0x10001: (0x100, 0x3C), 0x10002: (0x3C, 0x40),
    0x10003: (0x200, 0x3C), 0x10004: (0x100, 0x3C), 0x10005: (0x3C, 0x40),
}
NINTENDO_ISSUER_PREFIX = "Root-CA00000003-XS"
MIN_TICKET = 0x2C0


class NspError(Exception):
    pass


@dataclass(frozen=True)
class TicketReport:
    name: str
    sig_type: int
    issuer: str
    blank_signature: bool


def read_pfs0(f):
    """Return [(name, offset, size)] for a PFS0 container (NSP)."""
    head = f.read(0x10)
    if len(head) < 0x10 or head[:4] != b"PFS0":
        raise NspError("not a PFS0/NSP file")
    count, strtab_size = struct.unpack_from("<II", head, 4)
    if count > 0x10000 or strtab_size > 0x1000000:
        raise NspError("implausible PFS0 header")
    entries = [struct.unpack("<QQII", f.read(0x18)) for _ in range(count)]
    strtab = f.read(strtab_size)
    base = 0x10 + count * 0x18 + strtab_size
    out = []
    for off, size, name_off, _ in entries:
        end = strtab.find(b"\0", name_off)
        out.append((strtab[name_off:end if end >= 0 else None].decode("utf-8", "replace"), base + off, size))
    return out


def parse_ticket(name, data):
    if len(data) < 4 + 0x100:
        raise NspError(f"{name}: ticket too short")
    (sig_type,) = struct.unpack_from("<I", data, 0)
    if sig_type not in SIG_LAYOUT:
        return TicketReport(name, sig_type, "", False)
    sig_len, pad = SIG_LAYOUT[sig_type]
    issuer_off = 4 + sig_len + pad
    if len(data) < issuer_off + 0x40:
        raise NspError(f"{name}: ticket truncated")
    sig = data[4:4 + sig_len]
    issuer = data[issuer_off:issuer_off + 0x40].split(b"\0", 1)[0].decode("ascii", "replace")
    return TicketReport(name, sig_type, issuer, len(set(sig)) == 1)


def inspect_nsp(path):
    reports = []
    with open(path, "rb") as f:
        for name, off, size in read_pfs0(f):
            if name.lower().endswith(".tik"):
                f.seek(off)
                reports.append(parse_ticket(name, f.read(min(size, 0x400))))
    return reports


def ticket_findings(path):
    path = Path(path)
    label = path.name
    try:
        reports = inspect_nsp(path)
    except (NspError, OSError, struct.error) as e:
        return [Finding(ERROR, "nsp-unreadable", f"{label}: {e}")]
    if not reports:
        return [Finding(INFO, "nsp-no-ticket", f"{label}: contains no ticket, nothing to check")]
    out = []
    for r in reports:
        where = f"{label} ({r.name})"
        if r.sig_type not in SIG_LAYOUT:
            out.append(Finding(WARN, "ticket-odd-sig", f"{where}: unknown signature type 0x{r.sig_type:X}"))
        elif r.blank_signature:
            out.append(Finding(
                ERROR, "ticket-blank-signature",
                f"{where}: signature is blank (fake-signed ticket)",
                "A console rejects this ticket (install error 0x291, 'common ticket is modified'). "
                "This tool does not patch around that. Use a dump of your own purchase (nxdumptool) or re-download it from the eShop."))
        elif not r.issuer.startswith(NINTENDO_ISSUER_PREFIX):
            out.append(Finding(
                ERROR, "ticket-bad-issuer", f"{where}: issuer is '{r.issuer}', not a Nintendo ticket signer",
                "A console rejects tickets not issued by Nintendo (error 0x291). Use a dump of your own purchase."))
        else:
            out.append(Finding(INFO, "ticket-plausible",
                               f"{where}: looks like a Nintendo-issued ticket (signature not cryptographically verified)"))
    return out


def nsp_files(path):
    p = Path(path)
    return [p] if p.is_file() else sorted(p.rglob("*.nsp"))
