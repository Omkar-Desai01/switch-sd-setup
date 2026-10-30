# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-29

First public beta. Tested with fake drives and on copies of a real card; not yet field-tested on a wide range of
cards and card readers, so it is published as a pre-release.

### Added
- Desktop app (`SwitchSDSetup.exe`) and command-line tool (`sdsetup.exe`).
- Guided setup: pick the card, back it up, optionally format, install, verify, and get a checklist for the
  console-side steps.
- Installs Atmosphere, hekate, Sphaira, Checkpoint, nxdumptool and 90DNS Tester from the official GitHub
  releases, with SHA-256 checks. Goldleaf and JKSV are optional.
- Drive safety: only removable drives can be formatted or installed to, the Windows system drive and the drive
  holding the tool are always refused, and a format needs a fresh verified backup plus a typed confirmation.
- Update an existing card: detects installed versions from the files themselves (hand-built cards work too),
  snapshots what it will overwrite, updates only what is behind, reports leftovers from old versions and can roll
  the whole update back.
- Repair: clean-boot test that switches third-party add-ons and patches off and back on, without deleting anything.
- Health check (`doctor`): NAND backup completeness, emuMMC sector against the partition table, firmware against
  the installed Atmosphere release, Atmosphere crash reports, and a read-only inspection of NSP tickets.
- Vault of saved known-good releases, used automatically when GitHub is unavailable or rate-limited.
- Essential backup mode for updates (skips folders over 1 GB such as games and emuMMC images).

### Not included by design
- Signature or ticket-check patches, game downloads, firmware or key files.

[0.1.0]: https://github.com/YOUR-GITHUB-USERNAME/switch-sd-setup/releases/tag/v0.1.0
