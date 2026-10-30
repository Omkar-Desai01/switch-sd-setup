import json
import re
from pathlib import Path

from .manifest import RESOURCE_DIR
from .verify import ERROR, INFO, WARN, Finding

DEFAULT_COMPAT = RESOURCE_DIR / "compat.json"
_VERSION = re.compile(r"^\d+\.\d+(\.\d+)?$")


def load_compat(path=DEFAULT_COMPAT):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _tuple(v):
    parts = [int(x) for x in v.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def check_firmware(firmware, ams_version, compat):
    """Compare a console firmware version against the newest firmware an Atmosphere release lists."""
    if not _VERSION.match(firmware or ""):
        return [Finding(ERROR, "fw-bad-version", f"not a firmware version: {firmware!r} (use e.g. 23.0.0)")]
    if not ams_version:
        return [Finding(WARN, "fw-no-ams", "Atmosphere version unknown (no install receipt); pass --ams-version")]
    supported = compat.get("atmosphere", {}).get(ams_version)
    note = f"table last checked {compat.get('verified', '?')} against {compat.get('source', 'release notes')}"
    if not supported:
        return [Finding(WARN, "fw-unknown-ams", f"no firmware data for Atmosphere {ams_version} ({note})",
                        "Read that release's notes on GitHub for the supported firmware.")]
    fw, top = _tuple(firmware), _tuple(supported)
    if fw[:2] > top[:2]:
        return [Finding(ERROR, "fw-too-new", f"firmware {firmware} is newer than Atmosphere {ams_version} supports (up to {supported})",
                        "Do not boot it on this Atmosphere. Update Atmosphere first (re-run install), or stay on the older firmware.")]
    if fw > top:
        return [Finding(WARN, "fw-patch-newer", f"firmware {firmware} is a newer patch than Atmosphere {ams_version} lists ({supported}); usually fine",
                        "Check the release notes if the emuMMC fails to boot.")]
    return [Finding(INFO, "fw-ok", f"firmware {firmware} is within Atmosphere {ams_version} support (up to {supported}); {note}")]
