# vault

Saved copies of the releases `sdsetup` installs, so a card can still be built when GitHub or a repository is unavailable, or when you want the exact versions that worked.

- `index.json` records tag, file name, SHA-256 and source repo of every saved copy. Each file is re-hashed when used; a changed file is refused.
- `sdsetup vault status` lists what is saved, `sdsetup vault verify` re-checks hashes, `sdsetup vault update` saves the current releases (the newest plus one previous version are kept per component).
- `sdsetup install E:\ --source vault` uses only these copies, with no network. Without `--source`, the newest release is tried first and the vault is the fallback.

Licences: these are unmodified upstream release files and keep their own licences (Atmosphere, hekate and most homebrew here are GPL). The source repo of each copy is in `index.json`. If you hand the vault to other people, keep this folder intact and point them to those repositories for source code.

Not included: Lockpick_RCM (its repository is blocked on GitHub).

This folder is empty in the Git repository and in downloaded releases of Switch SD Setup. It is filled on your own
machine by `sdsetup vault update` (or the "Update saved…" button), so you decide which versions count as known-good.
