"""Entry point for the windowed exe (no console). Errors are shown in a dialog because there is no console."""
import os
import sys
import traceback

# A windowed exe has no stdout/stderr; libraries that print would crash without these.
for name in ("stdout", "stderr"):
    if getattr(sys, name) is None:
        setattr(sys, name, open(os.devnull, "w"))

if __name__ == "__main__":
    try:
        from sdsetup.gui import main
        sys.exit(main())
    except Exception:
        try:
            import tkinter
            from tkinter import messagebox
            root = tkinter.Tk()
            root.withdraw()
            messagebox.showerror("Switch SD Setup", traceback.format_exc()[-1500:])
        except Exception:
            pass
        sys.exit(1)
