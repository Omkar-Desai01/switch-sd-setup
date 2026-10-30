from pathlib import Path

HEKATE_IPL = """\
[config]
autoboot=0
autoboot_list=0
bootwait=3
backlight=100
autohosoff=1
autonogc=1
updater2p=1
bootprotect=0

[Atmosphere emuMMC]
pkg3=atmosphere/package3
emummcforce=1
icon=bootloader/res/icon_payload.bmp

[Atmosphere sysNAND]
pkg3=atmosphere/package3
emummc_force_disable=1
icon=bootloader/res/icon_payload.bmp

[Stock sysNAND]
pkg3=atmosphere/package3
emummc_force_disable=1
stock=1
icon=bootloader/res/icon_switch.bmp
"""

_BLOCKED_TLDS = [
    "nintendo.com", "nintendo.net", "nintendo.jp", "nintendo.co.jp", "nintendo.co.uk",
    "nintendo-europe.com", "nintendowifi.net", "nintendo.es", "nintendo.co.kr",
    "nintendo.tw", "nintendo.com.hk", "nintendo.com.au", "nintendo.co.nz",
    "nintendo.at", "nintendo.be", "nintendods.cz", "nintendo.dk", "nintendo.de",
    "nintendo.fi", "nintendo.fr", "nintendo.gr", "nintendo.hu", "nintendo.it",
    "nintendo.nl", "nintendo.no", "nintendo.pt", "nintendo.ru", "nintendo.co.za",
    "nintendo.se", "nintendo.ch", "nintendo.pl", "nintendoswitch.com",
    "nintendoswitch.com.cn", "nintendoswitch.cn",
]

# Blocks Nintendo servers for the emuMMC so it cannot go online by accident.
# The connection-test hosts must still answer, or the console reports a network fault.
HOSTS_EMUMMC = (
    "# Block Nintendo servers on emuMMC (same layout as 90DNS).\n"
    + "".join(f"127.0.0.1 *{d}\n" for d in _BLOCKED_TLDS)
    + "95.216.149.205 *conntest.nintendowifi.net\n"
    + "95.216.149.205 *ctest.cdn.nintendo.net\n"
)

CONFIG_FILES = {
    "bootloader/hekate_ipl.ini": HEKATE_IPL,
    "atmosphere/hosts/emummc.txt": HOSTS_EMUMMC,
}


def write_default_config(target, *, reset=False, dry_run=False):
    """Create config files that no release zip ships. Existing files are kept unless reset."""
    lines = []
    for rel, text in CONFIG_FILES.items():
        path = Path(target) / rel
        if path.exists() and not reset:
            lines.append(f"[config] kept existing {rel}")
            continue
        lines.append(f"[config] {'would write' if dry_run else 'wrote'} {rel}")
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    return lines
