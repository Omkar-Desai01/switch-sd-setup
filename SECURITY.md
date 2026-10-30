# Security policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please report privately through GitHub: **Security tab > Report a vulnerability** on this repository
(https://github.com/YOUR-GITHUB-USERNAME/switch-sd-setup/security/advisories/new). Do not open a public issue for
security problems.

Include what you did, what you expected, and what happened. You will get a reply within a few days.

## What counts

This tool writes to removable drives, so these are treated as high priority:

- any way to make it format, overwrite or delete on a drive that is not removable, or on the Windows system drive;
- bypassing the backup, confirmation or elevation checks before a format;
- path traversal when extracting downloaded archives, or accepting a download whose hash does not match;
- data loss during update, rollback or clean-boot.

Never include keys, firmware, game files or console serial numbers in a report.
