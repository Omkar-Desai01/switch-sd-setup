"""Build release/SwitchSDSetup/{SwitchSDSetup.exe (desktop app), sdsetup.exe (terminal)} with the vault next to them.

Usage: py -3 build.py        (needs: py -3 -m pip install pyinstaller)
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "release" / "SwitchSDSetup"

README_FIRST = """SWITCH SD SETUP - quick start

SwitchSDSetup.exe   the desktop app. Double-click it.
sdsetup.exe         the same tool for a terminal (sdsetup.exe --help). Double-click gives a text menu.

1. Plug in the SD card (in a card reader) and open SwitchSDSetup.exe.
2. Pick the card in the list, then press "Dry run". It shows every step and changes nothing.
3. When that looks right, press "Set up this card...".
   - To format the card, use "Restart as administrator" in the window first.
   - Keep the "vault" folder next to the exe: it holds saved copies of the software so
     setup still works if GitHub is down.
4. Follow SETUP-CHECKLIST.txt (saved in your user folder, SwitchSD-backups) on the console.

Windows may warn about an unknown publisher, or Defender may flag a new unsigned exe.
The source is in the project folder; allow it only if you built it yourself.

Free and open source. If it saved you time: https://buymeacoffee.com/ok_27
"""


def run(cmd):
    print("+", " ".join(map(str, cmd)))
    subprocess.run(cmd, cwd=ROOT, check=True)


def main():
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests"])
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    sep = ";"  # PyInstaller --add-data separator on Windows
    data = ["--add-data", f"{ROOT / 'manifest.json'}{sep}.", "--add-data", f"{ROOT / 'compat.json'}{sep}."]
    for name, entry, mode in (("sdsetup", "run_sdsetup.py", "--console"), ("SwitchSDSetup", "run_gui.py", "--windowed")):
        run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", mode,
             "--name", name, "--distpath", OUT, "--workpath", ROOT / "build", "--specpath", ROOT / "build",
             *data, ROOT / entry])
    shutil.copytree(ROOT / "vault", OUT / "vault")
    shutil.copy2(ROOT / "README.md", OUT / "README.md")
    (OUT / "README-FIRST.txt").write_text(README_FIRST, encoding="utf-8")
    for p in sorted(OUT.iterdir()):
        size = p.stat().st_size / 1e6 if p.is_file() else sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6
        print(f"{p.name:20} {size:6.1f} MB")


if __name__ == "__main__":
    main()
