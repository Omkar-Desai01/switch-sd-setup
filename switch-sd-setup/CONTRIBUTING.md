# Contributing

Thanks for helping. Bug reports from real cards and readers are the most valuable contribution right now.

## Scope

Welcome: safer drive handling, better detection and repair, more components in `manifest.json`, clearer messages,
documentation, tests, translations.

**Not accepted**, and pull requests will be closed: signature or ticket-check patches, downloading games, distributing
keys or firmware, and anything else whose purpose is to run software the user does not own. The tool may *explain* why
a console rejects something; it must not work around it.

## Setup

Python 3.10 or newer on Windows. The program has no runtime dependencies.

```bash
git clone https://github.com/YOUR-GITHUB-USERNAME/switch-sd-setup.git
cd switch-sd-setup
python -m unittest discover -s tests          # all tests
python -m sdsetup gui                         # run from source
python -m pip install pyinstaller
python build.py                               # release/SwitchSDSetup/ (runs the tests first)
```

## Ground rules

- **Never test against a real drive.** All drive logic goes through the `WinApi` wrapper in `sdsetup/drives.py`, and the
  tests use a fake API plus temporary folders. Anything that can erase data needs tests that prove the refusal paths.
- **Keep it dependency-free.** Standard library only (tkinter for the window).
- **Fail safe.** If something is unclear (unknown drive, odd partition layout), refuse and say why rather than guess.
- **Explain to the user.** Errors say what happened and what to do next. Findings from `doctor` carry a `fix:` hint.
- **Match the surrounding code.** Small functions, plain names, comments only where the reason is not obvious.
- **No personal data in fixtures.** Do not commit real console serials, eMMC ids, keys or card dumps.

## Making a change

1. Open an issue for anything large so we can agree on the approach first.
2. Branch, change, add tests, run the suite.
3. Add a line under `## [Unreleased]` in `CHANGELOG.md`.
4. Open a pull request using the template.

## Releasing (maintainer)

1. Move the "Unreleased" entries to a new version heading in `CHANGELOG.md` and update the version in
   `sdsetup/__init__.py` and `pyproject.toml` (a test checks they agree).
2. Commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. The Release workflow runs the tests, builds the executables, writes `SHA256SUMS.txt`, attests the build, and
   publishes the GitHub release with notes taken from the changelog. Versions starting with `v0.` are marked as pre-releases.
