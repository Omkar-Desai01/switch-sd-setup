# How it works

## Design principles

- **Official sources only.** Components come from each project's own GitHub releases and are checked against the
  SHA-256 digest GitHub publishes. Where none is published the tool says so.
- **Nothing destructive without proof.** Formatting needs a fresh, verified, full backup of that exact card (matched by
  volume serial) and a typed confirmation. Updates snapshot everything they overwrite first.
- **Read the card, not a record.** Installed versions are worked out from the files, so cards built by hand work.
- **Standard library only.** No runtime dependencies; the desktop app uses tkinter.

## Components

| Component | Source | Installed as |
|---|---|---|
| Atmosphere | Atmosphere-NX/Atmosphere | zip extracted to the card root |
| hekate + Nyx | CTCaer/hekate | zip; the payload is written to `payload.bin` and `atmosphere/reboot_payload.bin` |
| Sphaira | ITotalJustice/sphaira | `switch/sphaira/sphaira.nro` |
| Checkpoint, nxdumptool, 90DNS Tester | their repositories | single `.nro` files under `switch/` |
| Goldleaf, JKSV (optional) | their repositories | single `.nro` files under `switch/` |

`manifest.json` describes each component (repository, asset pattern, destination) and the expected card layout.
Adding homebrew needs no code change. Lockpick_RCM is a manual step because its repository is blocked on GitHub.

## Module map

| Module | Role |
|---|---|
| `fetch`, `vault` | Release lookup, verified downloads, rate-limit fallback, saved known-good copies |
| `install`, `config` | Safe extraction (no path escapes), receipt, default `hekate_ipl.ini` and hosts file |
| `drives`, `backup`, `format` | Removable-drive detection, full and essential backups, gated format |
| `versions`, `update` | File-level version detection, snapshots, stale-file handling, rollback |
| `repair`, `reports` | Clean-boot test, crash-report reader |
| `verify`, `doctor`, `nand`, `emummc`, `compat`, `ticket` | Health checks |
| `wizard`, `cli`, `gui` | Guided flow, command line, desktop window |

## Facts the checks rely on

- hekate places an emuMMC on a raw partition at `partition start + 0x8000` sectors and writes the same 32-bit sector
  into `emuMMC/RAWn/raw_based`.
- BOOT0 and BOOT1 are 4 MiB each; a RAW GPP backup of a standard eMMC is 31,268,536,320 bytes and is split into
  `rawnand.bin.NN` parts on FAT32 cards.
- Atmosphere releases list the newest firmware they support in their release notes; `compat.json` records that.
- System modules use title ids `0100000000000000`–`01000000000007FF`.
- Atmosphere writes crash reports as text under `atmosphere/crash_reports`.

## Limits

- The tool cannot read the console's firmware version; you provide it to `doctor --firmware`.
- The ticket inspector does not verify signatures cryptographically.
- Windows only.
