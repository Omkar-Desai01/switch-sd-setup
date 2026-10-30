import unittest

from sdsetup.cli import interactive_menu
from test_wizard import Scripted


class MenuTests(unittest.TestCase):
    def run_menu(self, answers, results=None):
        calls = []

        def run(argv):
            calls.append(argv)
            return 0
        io = Scripted(answers)
        code = interactive_menu(io, run)
        return code, calls, io

    def test_choices_map_to_commands(self):
        code, calls, _ = self.run_menu(["1", "2", "3", "5", "q"])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [["wizard", "--dry-run"], ["wizard"], ["drives"], ["vault", "status"]])

    def test_doctor_asks_for_card_and_firmware(self):
        _, calls, _ = self.run_menu(["4", "E:\\", "23.0.0", "4", "F:\\", "", "q"])
        self.assertEqual(calls, [["doctor", "E:\\", "--firmware", "23.0.0"], ["doctor", "F:\\"]])

    def test_blank_card_and_bad_choice_run_nothing(self):
        _, calls, io = self.run_menu(["4", "", "x", "q"])
        self.assertEqual(calls, [])
        self.assertIn("Not a valid choice", io.text)

    def test_ctrl_c_inside_a_command_returns_to_menu(self):
        def run(argv):
            raise KeyboardInterrupt
        io = Scripted(["3", "q"])
        self.assertEqual(interactive_menu(io, run), 0)
        self.assertIn("exit code 130", io.text)


if __name__ == "__main__":
    unittest.main()


class NewEntriesTests(unittest.TestCase):
    def test_update_repair_entries_ask_for_the_card(self):
        calls = []
        io = Scripted(["6", "E:\\", "7", "E:\\", "8", "E:\\", "9", "E:\\", "q"])
        interactive_menu(io, lambda argv: calls.append(argv) or 0)
        self.assertEqual(calls, [["update", "E:\\"], ["rollback", "E:\\"],
                                 ["repair", "--clean-boot", "E:\\"], ["repair", "--restore", "E:\\"]])
