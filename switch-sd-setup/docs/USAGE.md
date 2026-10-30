# Usage guide

Everything the desktop app does is also available on the command line. From a source checkout use
`py -3 -m sdsetup <command>`; with the release zip use `sdsetup.exe <command>`. A drive can be written as `E:`.

## Commands

| Command | What it does |
|---|---|
| `wizard [--dry-run] [--source latest\|vault]` | Guided setup: pick the card, back up, optionally format, install, verify, checklist |
| `update E: [--dry-run] [--only ID] [--remove-leftovers] [--yes]` | Update an existing card; snapshots first |
| `rollback E: [--snapshot DIR] [--yes]` | Undo the last update |
| `repair E: --clean-boot` / `--restore` | Switch third-party add-ons off for a boot test / back on |
| `doctor E: [--firmware X] [--nand-backup DIR] [--nsp PATH]` | Health check with a `fix:` line per problem |
| `verify E:` | Layout, file hashes and config sanity |
| `install E: [--with ID] [--without ID] [--source ...] [--dry-run]` | Install components without the wizard |
| `backup E: --to DIR [--essential] [--verify-hash]` | Copy the card to the PC |
| `format E: --backup-dir DIR [--fs exfat\|fat32] [--dry-run]` | Quick-format a removable drive (erases it) |
| `drives` | List drives and which are safe to use as a card |
| `vault status\|verify\|update` | Manage the saved known-good releases |
| `components`, `fetch`, `gui`, `about`, `--version` | Listing, pre-download, open the window, info |

With no arguments `sdsetup.exe` shows a text menu.

## Guided setup

1. **Pick the card.** Only removable drives that are not the system drive are offered.
2. **Format?** Say no to add to or update the card as it is; say yes for a new or unreliable card.
3. **Backup.** Without a format: an *essential* backup (settings and apps, skipping folders over 1 GB such as games and
   emuMMC images). With a format: a full backup, which is required.
4. **Format** (optional): choose exFAT or FAT32 (Windows refuses FAT32 above 32 GB), a label, and type `ERASE E:`.
   Needs an administrator terminal.
5. **Install** the selected components, **verify** the card, and get a **checklist** saved next to the backups
   (`~/SwitchSD-backups/SETUP-CHECKLIST.txt`) together with a copy of the hekate payload to send over USB.

Console-side steps (RCM, NAND backup, key dump, emuMMC creation, going offline) are instructions only; the tool never
talks to the console.

## Format safety

`format` runs only if all of these hold:

- the drive is removable, is not the Windows system drive and does not hold the tool's own files;
- the filesystem fits (FAT32 up to 32 GB);
- a full backup marker from the last 24 hours matches the card's volume serial and finished without errors
  (or `--skip-backup`, which cannot be undone);
- the typed phrase `ERASE E:` matches exactly;
- the terminal is elevated;
- the volume serial is unchanged right before the format.

## Updating an existing card

```
sdsetup update E: --dry-run        # what is on the card vs the newest release; nothing changes
sdsetup update E:                  # snapshot, then update what is behind
sdsetup update E: --only atmosphere --remove-leftovers
sdsetup rollback E:
```

- **Versions come from the files.** Each release (newest, what this tool installed earlier, saved vault copies) is
  compared file by file with the card, so cards built by hand are recognised too. A recognised version with a
  damaged or missing file is reported as *files differ* and an update repairs it.
- **Snapshots** go to `~/SwitchSD-backups/updates/<time>` on the PC: the files an update overwrites plus your
  settings (`hekate_ipl.ini`, `atmosphere/config`, hosts files, `emummc.ini`). Rollback restores them, deletes
  files the update added, and keeps any file you changed after the update.
- **Leftovers** (files an older install wrote that the new release dropped) are listed. They are removed only
  when you ask, only if you have not edited them, and into the snapshot, so a rollback brings them back.

## When a card stopped working

1. `sdsetup doctor E: --firmware <your console firmware>`. It reads Atmosphere crash reports: a crash in a system
   module usually means Atmosphere does not match the firmware; a crash of a program in `atmosphere/contents`
   names that add-on; a game crash is not a setup problem.
2. `sdsetup update E:` to get the newest Atmosphere and hekate.
3. `sdsetup repair E: --clean-boot` renames `atmosphere/contents`, `exefs_patches` and `kip_patches` to
   `*.disabled-<time>` so you can test a minimal setup. Nothing is deleted; it also switches off cheats and mods stored
   there. `sdsetup repair E: --restore` puts everything back, even in a later session.
4. If an update made things worse, `sdsetup rollback E:`.

## Health check details

| Check | Looks at |
|---|---|
| emuMMC sector | `emummc.ini` sector equals a partition start + `0x8000` (hekate's protective offset); `raw_based` holds the same value; a file-based emuMMC has its `eMMC` folder |
| NAND backup | BOOT0/BOOT1 are 4 MiB each; RAW GPP (`rawnand.bin[.NN]`) totals at least 31,268,536,320 bytes; `partial.idx` means unfinished |
| Firmware vs Atmosphere | `--firmware` against the newest firmware the installed release lists in `compat.json` |
| Crash reports | `atmosphere/crash_reports` and `fatal_errors`, classified as system / add-on / game |
| NSP tickets | Read-only: flags blank signatures and non-Nintendo issuers, which a console rejects with error 0x291 |
| DNS.MITM | Startup log does not mention `hosts/emummc.txt` |

Exit code 1 means at least one error. The tool cannot read your console's firmware; type it into `--firmware`.

## Vault (saved known-good versions)

```
sdsetup vault update      # save today's releases (the newest plus one previous are kept)
sdsetup vault status
sdsetup vault verify
sdsetup install E: --source vault   # no network, exactly the saved versions
```

The vault lives in the `vault` folder next to the program and is created on your machine; releases of this project do
not ship third-party binaries. Every saved file is hash-checked before use. When GitHub or a repository is unavailable
or rate-limited, installs fall back to the vault and say so. Set `GITHUB_TOKEN` to raise the unauthenticated API limit
(60 requests per hour). Run `vault update` only after you have confirmed new releases work.

## Files the tool writes

| Where | What |
|---|---|
| Card: `config/sdsetup/receipt.json` | What was installed and the hash of every file |
| Card: `config/sdsetup/quarantine.json` | Moves made by a clean-boot test |
| PC: `~/SwitchSD-backups/` | Card backups, `SETUP-CHECKLIST.txt`, `hekate_payload.bin` |
| PC: `~/SwitchSD-backups/updates/` | Update snapshots |
| PC: `%LOCALAPPDATA%/sdsetup/cache` | Downloaded releases |
