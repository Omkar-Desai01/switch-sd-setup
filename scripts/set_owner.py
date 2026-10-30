"""Replace the YOUR-GITHUB-USERNAME placeholder in the project files with your GitHub username (or org).

    python scripts/set_owner.py my-username

Run it once before the first commit. It only touches text files and prints what it changed.
"""
import re
import sys
from pathlib import Path

PLACEHOLDER = "YOUR-GITHUB-USERNAME"
ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "release", "build", "dist", "vault", "__pycache__"}
SUFFIXES = {".md", ".yml", ".yaml", ".html", ".toml", ".txt", ".json", ".py"}


def main(argv):
    if len(argv) != 2 or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", argv[1]):
        print("usage: python scripts/set_owner.py <github-username>")
        return 2
    owner = argv[1]
    changed = 0
    for path in sorted(ROOT.rglob("*")):
        if path.is_dir() or path.suffix not in SUFFIXES or SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        text = path.read_text(encoding="utf-8")
        if PLACEHOLDER in text:
            path.write_text(text.replace(PLACEHOLDER, owner), encoding="utf-8", newline="")
            print(f"updated {path.relative_to(ROOT).as_posix()}")
            changed += 1
    print(f"{changed} file(s) updated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
