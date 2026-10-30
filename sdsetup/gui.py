"""Windows desktop front end (tkinter) over the same core as the command line."""
import contextlib
import ctypes
import os
import queue
import re
import sys
import threading
import webbrowser
from pathlib import Path

from . import SUPPORT_URL, __version__
from .cli import _default_cache, _protected, build_parser, main as cli_main, make_provider
from .drives import DriveError, list_drives, safety_problems
from .format import is_admin
from .manifest import APP_DIR, DEFAULT_MANIFEST, load_manifest
from .repair import run_clean_boot, run_restore
from .update import run_rollback, run_update
from .vault import Vault
from .wizard import Console, run_wizard

BACKUP_ROOT = Path.home() / "SwitchSD-backups"
SNAPSHOT_ROOT = BACKUP_ROOT / "updates"
_LEVELS = [
    (re.compile(r"^\s*(error|ERROR)\b"), "error"),
    (re.compile(r"^\s*WARN\b"), "warn"),
    (re.compile(r"^\s*INFO\b"), "dim"),
    (re.compile(r"^\s*fix:"), "hint"),
    (re.compile(r"backup OK|all checks passed|vault OK|formatted\.|saved \d|0 error\(s\)"), "ok"),
]


def line_tag(line):
    """Log colour for one printed line (doctor, verify and wizard output share these prefixes)."""
    for rx, tag in _LEVELS:
        if rx.search(line):
            return tag
    return None


class UiBridge:
    """Lets a worker thread run code on the UI thread and wait for the answer (dialogs), or post output."""

    def __init__(self):
        self.q = queue.Queue()
        self.ui_thread = threading.current_thread()

    def call(self, fn):
        if threading.current_thread() is self.ui_thread:
            return fn()
        done, box = threading.Event(), {}
        self.q.put((fn, done, box))
        done.wait()
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def post(self, fn):
        self.q.put((fn, threading.Event(), {}))

    def pump(self):
        while True:
            try:
                fn, done, box = self.q.get_nowait()
            except queue.Empty:
                return
            try:
                box["value"] = fn()
            except BaseException as e:  # reported to the waiting worker, not lost
                box["error"] = e
            done.set()


class LogWriter:
    """File-like object that forwards printed text to the log from any thread."""

    def __init__(self, sink):
        self.sink = sink

    def write(self, s):
        if s:
            self.sink(s)
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


class GuiConsole(Console):
    """Wizard I/O as dialogs. ask_fn(prompt, default) and confirm_fn(prompt, default) run on the UI thread."""

    def __init__(self, bridge, ask_fn, confirm_fn):
        self.bridge, self.ask_fn, self.confirm_fn = bridge, ask_fn, confirm_fn

    def say(self, text=""):
        print(text)

    def ask(self, prompt, default=""):
        value = self.bridge.call(lambda: self.ask_fn(prompt.strip(), default))
        return (value or "").strip() or default

    def confirm(self, prompt, default=False):
        return bool(self.bridge.call(lambda: self.confirm_fn(prompt.strip(), default)))


def relaunch_as_admin():
    """Ask Windows (UAC) to start this program again elevated. Returns True if the request was accepted."""
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, ""
    else:
        exe, params = sys.executable, "-m sdsetup gui"
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, str(APP_DIR), 1)
    return rc > 32


class App:
    def __init__(self, root, manifest):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root, self.manifest = root, manifest
        self.bridge = UiBridge()
        self.busy = False
        self._after = None
        self.source = tk.StringVar(value="latest")
        self.action_buttons = []
        self.drives = {}
        self._build()
        self.refresh_drives()
        self._tick()

    # ---------------------------------------------------------------- layout
    def _build(self):
        tk, ttk = self.tk, self.ttk
        root = self.root
        root.title("Switch SD Setup")
        root.geometry("1080x760")
        root.minsize(900, 700)
        try:
            ttk.Style(root).theme_use("vista")
        except tk.TclError:
            pass
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)

        head = ttk.Frame(outer)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        ttk.Label(head, text="Switch SD Setup", font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Label(head, text="  Atmosphere, hekate and homebrew onto an SD card", foreground="#555").pack(side="left")
        admin = is_admin()
        if not admin:
            self.admin_btn = ttk.Button(head, text="Restart as administrator", command=self.on_admin)
            self.admin_btn.pack(side="right")
        ttk.Label(head, text="Administrator: yes" if admin else "Administrator: no (needed only to format)",
                  foreground="#2e7d32" if admin else "#8a6d00").pack(side="right", padx=10)

        left = ttk.Frame(outer)
        left.grid(row=1, column=0, sticky="ns", padx=(0, 12))
        ttk.Label(left, text="1. Choose the SD card", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        cols = ("drive", "label", "size", "state")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=6, selectmode="browse")
        for c, w, t in (("drive", 50, "Drive"), ("label", 110, "Label"), ("size", 70, "Size"), ("state", 120, "Status")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="x", pady=(4, 2))
        self.tree.tag_configure("unsafe", foreground="#999999")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.reason = ttk.Label(left, text="", wraplength=380, foreground="#8a6d00", justify="left")
        self.reason.pack(anchor="w", fill="x")
        self.refresh_btn = ttk.Button(left, text="Refresh drives", command=self.refresh_drives)
        self.refresh_btn.pack(anchor="w", pady=(2, 10))

        src = ttk.LabelFrame(left, text="2. Software source", padding=8)
        src.pack(fill="x", pady=(0, 10))
        ttk.Radiobutton(src, text="Newest from GitHub (saved copy if GitHub fails)", variable=self.source,
                        value="latest").pack(anchor="w")
        ttk.Radiobutton(src, text="Saved known-good versions only (no internet)", variable=self.source,
                        value="vault").pack(anchor="w")

        bold = ("Segoe UI", 10, "bold")

        def button(parent, text, cmd):
            b = ttk.Button(parent, text=text, command=cmd)
            self.action_buttons.append(b)
            return b

        ttk.Label(left, text="3. New or blank card", font=bold).pack(anchor="w")
        button(left, "Set up this card…", lambda: self.on_setup(False)).pack(fill="x", pady=2, ipady=3)
        button(left, "Dry run (changes nothing)", lambda: self.on_setup(True)).pack(fill="x", pady=2)

        ttk.Label(left, text="Card that already works, or stopped working", font=bold).pack(anchor="w", pady=(8, 0))
        grid = ttk.Frame(left)
        grid.pack(fill="x")
        grid.columnconfigure((0, 1), weight=1, uniform="g")
        for i, (text, cmd) in enumerate((
            ("Update / check versions…", self.on_update), ("Roll back last update…", self.on_rollback),
            ("Clean-boot test…", self.on_clean_boot), ("Switch add-ons back on", self.on_restore),
        )):
            button(grid, text, cmd).grid(row=i // 2, column=i % 2, sticky="ew", padx=(0, 3) if i % 2 == 0 else (3, 0), pady=2)
        button(left, "Health check (doctor)…", self.on_check).pack(fill="x", pady=2)

        ttk.Label(left, text="Saved versions and backups", font=bold).pack(anchor="w", pady=(8, 0))
        row = ttk.Frame(left)
        row.pack(fill="x")
        row.columnconfigure((0, 1, 2), weight=1, uniform="r")
        for i, (text, cmd) in enumerate((("Saved versions", self.on_vault_status),
                                         ("Update saved…", self.on_vault_update),
                                         ("Backups folder", self.on_open_backups))):
            button(row, text, cmd).grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 3, 0 if i == 2 else 3), pady=2)

        right = ttk.Frame(outer)
        right.grid(row=1, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.log = tk.Text(right, wrap="word", state="disabled", font=("Consolas", 10), bg="#1e1e1e",
                           fg="#d4d4d4", insertbackground="#d4d4d4", relief="flat", padx=8, pady=6)
        sb = ttk.Scrollbar(right, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        for tag, color in (("error", "#f48771"), ("warn", "#e5c07b"), ("dim", "#8b949e"),
                           ("hint", "#79c0ff"), ("ok", "#98c379"), ("head", "#61afef")):
            self.log.tag_configure(tag, foreground=color)
        self.log.tag_configure("head", font=("Consolas", 10, "bold"))

        bar = ttk.Frame(outer)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.status = ttk.Label(bar, text="Ready")
        self.status.pack(side="left")
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=160)
        self.progress.pack(side="right")
        self.support = ttk.Label(bar, text=f"Buy me a coffee   |   v{__version__}", foreground="#0b57d0",
                                 cursor="hand2", font=("Segoe UI", 9, "underline"))
        self.support.pack(side="right", padx=14)
        self.support.bind("<Button-1>", lambda e: self.open_support())

        self.append("Pick your SD card on the left, then choose an action.\n"
                    "Tip: start with \"Dry run\"; it shows every step and changes nothing.\n", "dim")

    # ---------------------------------------------------------------- log
    def append(self, text, tag=None):
        t = self.log
        t.configure(state="normal")
        for i, part in enumerate(text.split("\r")):
            if i > 0:  # carriage return: progress lines rewrite the current line
                t.delete("end-1c linestart", "end-1c")
            for line in part.splitlines(keepends=True):
                t.insert("end", line, tag or line_tag(line))
        t.see("end")
        t.configure(state="disabled")

    # ---------------------------------------------------------------- drives
    def refresh_drives(self):
        self.tree.delete(*self.tree.get_children())
        self.drives = {}
        try:
            drives = list_drives()
        except DriveError as e:
            self.append(f"error: {e}\n", "error")
            return
        for d in drives:
            problems = safety_problems(d, protected_paths=_protected(self._args("drives")))
            self.drives[d.letter] = (d, problems)
            state = "ready" if not problems else ("not removable" if d.kind != "removable" else "protected")
            self.tree.insert("", "end", iid=d.letter, tags=() if not problems else ("unsafe",),
                             values=(f"{d.letter}:", d.label or "-", f"{d.total / 1e9:.0f} GB", state))
        usable = [l for l, (_, p) in self.drives.items() if not p]
        if len(usable) == 1:
            self.tree.selection_set(usable[0])
        self._on_select()

    def _on_select(self):
        sel = self.tree.selection()
        text = ""
        if sel:
            problems = self.drives[sel[0]][1]
            if problems:
                text = problems[0]
        elif not any(not p for _, p in self.drives.values()):
            text = "No removable drive found. Put the SD card in a reader and press Refresh."
        self.reason.configure(text=text)

    def selected_drive(self, need_safe=True):
        sel = self.tree.selection()
        if not sel:
            self._info("Choose your SD card in the list first.")
            return None
        drive, problems = self.drives[sel[0]]
        if need_safe and problems:
            self._info(f"{drive.letter}: cannot be used.\n\n" + "\n".join(problems))
            return None
        return drive

    # ---------------------------------------------------------------- dialogs
    def _info(self, text):
        from tkinter import messagebox
        messagebox.showinfo("Switch SD Setup", text, parent=self.root)

    def _ask(self, prompt, default=""):
        from tkinter import simpledialog
        return simpledialog.askstring("Switch SD Setup", prompt, initialvalue=default, parent=self.root)

    def _confirm(self, prompt, default=False):
        from tkinter import messagebox
        return messagebox.askyesno("Switch SD Setup", prompt, default="yes" if default else "no", parent=self.root)

    # ---------------------------------------------------------------- tasks
    def _args(self, *extra):
        return build_parser().parse_args(["--cache", str(_default_cache()), *extra])

    def run_task(self, title, fn, notify=False):
        if self.busy:
            return
        self.set_busy(True, title)
        self.append(f"\n=== {title} ===\n", "head")
        writer = LogWriter(lambda s: self.bridge.post(lambda: self.append(s)))

        def work():
            code = 2
            try:
                with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                    code = fn()
            except Exception as e:
                message = f"error: {type(e).__name__}: {e}\n"  # `e` is unbound once this block ends
                self.bridge.post(lambda: self.append(message, "error"))
            self.bridge.post(lambda: self.task_done(title, code, notify))

        threading.Thread(target=work, daemon=True).start()

    def task_done(self, title, code, notify):
        self.set_busy(False)
        self.refresh_drives()
        ok = code in (0, None)
        self.append(f"=== {'finished' if ok else 'stopped (see messages above)'} ===\n", "ok" if ok else "warn")
        if notify:
            self._info("Finished. The checklist for the console steps is in your SwitchSD-backups folder."
                       if ok else "The task stopped before finishing. Nothing further was changed; see the log.")

    def set_busy(self, busy, title=""):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for b in (*self.action_buttons, self.refresh_btn):
            b.configure(state=state)
        self.status.configure(text=f"Working: {title}" if busy else "Ready")
        (self.progress.start if busy else self.progress.stop)(*([12] if busy else []))

    # ---------------------------------------------------------------- actions
    def on_setup(self, dry):
        drive = self.selected_drive()
        if not drive:
            return
        if not dry and not self._confirm(
                f"Set up {drive.describe()}?\n\nThe card is backed up first. Formatting is optional and asks again."):
            return
        args = self._args("wizard", "--source", self.source.get())
        console = GuiConsole(self.bridge, self._ask, self._confirm)

        def task():
            provider = make_provider(args, lambda m: print("  " + m))
            return run_wizard(self.manifest, console, provider, backup_root=BACKUP_ROOT,
                              protected_paths=_protected(args), dry_run=dry, preselect=drive.letter)

        self.run_task(f"{'Dry run' if dry else 'Set up'} {drive.letter}:", task, notify=not dry)

    def on_check(self):
        from tkinter import filedialog
        sel = self.tree.selection()
        start = f"{sel[0]}:\\" if sel else None
        target = filedialog.askdirectory(title="Choose the SD card (drive) or a card folder to check",
                                         initialdir=start, parent=self.root)
        if not target:
            return
        argv = ["doctor", os.path.normpath(target)]
        firmware = self._ask("Console firmware, e.g. 23.0.0 (optional)", "")
        if firmware and firmware.strip():
            argv += ["--firmware", firmware.strip()]
        if self._confirm("Also check a NAND backup folder on this PC?"):
            nand = filedialog.askdirectory(title="Choose the NAND backup folder", parent=self.root)
            if nand:
                argv += ["--nand-backup", os.path.normpath(nand)]
        self.run_task(f"Check {argv[1]}", lambda: cli_main(argv))

    def _maintenance(self, title, make_task):
        """Run an existing-card task on the selected (safe) drive; questions come back as dialogs."""
        drive = self.selected_drive()
        if not drive:
            return
        args = self._args("update", drive.root, "--source", self.source.get())
        console = GuiConsole(self.bridge, self._ask, self._confirm)
        self.run_task(f"{title} {drive.letter}:", lambda: make_task(drive, args, console), notify=False)

    def on_update(self):
        def task(drive, args, console):
            provider = make_provider(args, lambda m: print("  " + m))
            return run_update(self.manifest, console, provider, drive.root, Vault(args.vault),
                              snapshot_root=SNAPSHOT_ROOT)
        self._maintenance("Update / check", task)

    def on_rollback(self):
        self._maintenance("Roll back", lambda drive, args, console: run_rollback(console, drive.root, SNAPSHOT_ROOT))

    def on_clean_boot(self):
        self._maintenance("Clean-boot test", lambda drive, args, console: run_clean_boot(console, drive.root))

    def on_restore(self):
        self._maintenance("Switch add-ons on", lambda drive, args, console: run_restore(console, drive.root))

    def on_vault_status(self):
        def task():
            cli_main(["vault", "status"])
            return cli_main(["vault", "verify"])
        self.run_task("Saved versions", task)

    def on_vault_update(self):
        if self._confirm("Save today's newest releases as the known-good versions?\n\n"
                         "Do this only after you have confirmed the new versions work. "
                         "The newest copy plus one previous version are kept."):
            self.run_task("Update saved versions", lambda: cli_main(["vault", "update"]))

    def open_support(self):
        webbrowser.open(SUPPORT_URL)

    def on_open_backups(self):
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        os.startfile(BACKUP_ROOT)

    def on_admin(self):
        if self._confirm("Windows will ask for permission, then the program restarts. Continue?", True):
            if relaunch_as_admin():
                self.close()

    def on_close(self):
        if self.busy and not self._confirm("A task is still running. Close anyway?"):
            return
        self.close()

    def close(self):
        if self._after:
            self.root.after_cancel(self._after)
            self._after = None
        self.root.destroy()

    def _tick(self):
        self.bridge.pump()
        self._after = self.root.after(50, self._tick)


def main():
    import tkinter as tk
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    App(root, load_manifest(DEFAULT_MANIFEST))
    root.mainloop()
    return 0
