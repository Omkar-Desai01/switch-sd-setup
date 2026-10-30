"""Entry point used for the packaged exe (PyInstaller cannot run a package's __main__ with relative imports)."""
import sys

from sdsetup.cli import main

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):  # legacy Windows consoles choke on non-ASCII otherwise
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
