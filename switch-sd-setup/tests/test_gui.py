import threading
import time
import unittest

from sdsetup.gui import GuiConsole, LogWriter, UiBridge, line_tag
from sdsetup.manifest import load_manifest


def run_in_worker(bridge, fn):
    """Run fn on a thread while this (the UI) thread pumps the bridge; return (result, error)."""
    out = {}

    def work():
        try:
            out["value"] = fn()
        except BaseException as e:
            out["error"] = e

    t = threading.Thread(target=work)
    t.start()
    deadline = time.time() + 5
    while t.is_alive() and time.time() < deadline:
        bridge.pump()
        time.sleep(0.005)
    t.join(1)
    return out.get("value"), out.get("error")


class BridgeTests(unittest.TestCase):
    def test_call_on_ui_thread_is_direct(self):
        self.assertEqual(UiBridge().call(lambda: 7), 7)

    def test_worker_call_runs_on_ui_thread_and_returns(self):
        bridge = UiBridge()
        seen = []
        value, err = run_in_worker(bridge, lambda: bridge.call(lambda: seen.append(threading.current_thread()) or 42))
        self.assertEqual((value, err), (42, None))
        self.assertIs(seen[0], threading.main_thread())

    def test_exception_reaches_the_waiting_worker(self):
        bridge = UiBridge()

        def boom():
            raise ValueError("dialog failed")
        _, err = run_in_worker(bridge, lambda: bridge.call(boom))
        self.assertIsInstance(err, ValueError)

    def test_post_runs_on_pump_without_blocking(self):
        bridge, ran = UiBridge(), []
        t = threading.Thread(target=lambda: bridge.post(lambda: ran.append(1)))
        t.start()
        t.join()
        self.assertEqual(ran, [])
        bridge.pump()
        self.assertEqual(ran, [1])


class ConsoleTests(unittest.TestCase):
    def make(self, answers, confirms):
        bridge = UiBridge()
        asked = []

        def ask(prompt, default):
            asked.append((prompt, default))
            return answers.pop(0)

        def confirm(prompt, default):
            asked.append((prompt, default))
            return confirms.pop(0)
        return bridge, GuiConsole(bridge, ask, confirm), asked

    def test_ask_and_confirm_go_through_dialogs(self):
        bridge, io, asked = self.make(["ERASE E:", None, "  x "], [True, False])
        value, err = run_in_worker(bridge, lambda: (
            io.ask("\n  type it", ""), io.ask("fs", "exfat"), io.ask("label", "SD"),
            io.confirm("\nsure?", False), io.confirm("again?", True)))
        self.assertIsNone(err)
        self.assertEqual(value, ("ERASE E:", "exfat", "x", True, False))  # cancel (None) falls back to default
        self.assertEqual(asked[0], ("type it", ""))  # prompt whitespace trimmed for the dialog

    def test_log_writer_forwards_text(self):
        got = []
        w = LogWriter(got.append)
        w.write("a")
        w.write("")
        w.flush()
        self.assertEqual(got, ["a"])
        self.assertFalse(w.isatty())


class TagTests(unittest.TestCase):
    def test_line_tags(self):
        cases = {
            "ERROR nand-no-rawgpp: x": "error", "error: boom": "error", "  ERROR something": "error",
            "WARN  backup-on-card: x": "warn", "INFO  fw-ok: y": "dim", "      fix: do this": "hint",
            "  backup OK: 3 files": "ok", "0 error(s), 0 warning(s)": "ok", "plain line": None,
        }
        for line, tag in cases.items():
            self.assertEqual(line_tag(line), tag, line)


class AppSmokeTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
        except tk.TclError as e:
            self.skipTest(f"no display: {e}")
        self.root.withdraw()
        from sdsetup.gui import App
        self.app = App(self.root, load_manifest())
        self.addCleanup(self.app.close)

    def log_text(self):
        return self.app.log.get("1.0", "end")

    def test_widgets_and_drive_list_build(self):
        self.root.update()
        self.assertEqual(len(self.app.action_buttons), 10)
        self.assertIn("C", self.app.drives)  # every Windows PC has C:
        self.assertTrue(self.app.drives["C"][1])  # and it is protected
        self.assertIn("Dry run", self.log_text())

    def test_carriage_return_rewrites_progress_line(self):
        self.app.append("start\n")
        self.app.append("  file: 10%")
        self.app.append("\r  file: 90%")
        self.app.append("\r  file: 100%\n")
        text = self.log_text()
        self.assertIn("  file: 100%", text)
        self.assertNotIn("10%", text)
        self.assertNotIn("90%", text)

    def test_task_runs_in_background_and_logs(self):
        self.app.run_task("Saved versions", lambda: print("hello from the worker") or 0)
        self.assertTrue(self.app.busy)
        deadline = time.time() + 10
        while self.app.busy and time.time() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.app.busy)
        text = self.log_text()
        self.assertIn("hello from the worker", text)
        self.assertIn("finished", text)

    def test_task_error_is_reported_not_raised(self):
        def boom():
            raise RuntimeError("kaput")
        self.app.run_task("Failing", boom)
        deadline = time.time() + 10
        while self.app.busy and time.time() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertIn("RuntimeError: kaput", self.log_text())
        self.assertIn("stopped", self.log_text())

    def test_support_link_opens_the_coffee_page_only_on_click(self):
        import webbrowser
        opened = []
        real = webbrowser.open
        webbrowser.open = opened.append
        self.addCleanup(setattr, webbrowser, "open", real)
        self.assertEqual(opened, [])  # never opened on its own
        self.assertIn("Buy me a coffee", self.app.support.cget("text"))
        self.app.open_support()
        self.assertEqual(opened, ["https://buymeacoffee.com/ok_27"])

    def test_unsafe_drive_cannot_be_used(self):
        shown = []
        self.app._info = shown.append
        self.app.tree.selection_set("C")
        self.assertIsNone(self.app.selected_drive())
        self.assertIn("cannot be used", shown[0])


if __name__ == "__main__":
    unittest.main()
