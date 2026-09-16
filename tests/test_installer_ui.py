import queue
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from instalar_app_windows import Installer


class InstallerUiTests(unittest.TestCase):
    def setUp(self):
        self.ui = Installer.__new__(Installer)
        self.ui.source = Path("download")
        self.ui.destination = Path("installed")
        self.ui.log = Path("install.log")
        self.ui.events = queue.Queue()
        self.ui.busy = True
        self.ui.executable = None
        self.ui.progress = {}
        self.ui.status = Mock()
        self.ui.close_button = Mock()
        self.ui.open_button = Mock()
        self.ui.after = Mock()
        self.ui.destroy = Mock()

    def test_worker_queues_success_without_calling_tk_from_thread(self):
        exe = Path("installed/app.exe")
        with patch("instalar_app_windows.install", return_value=exe):
            worker = threading.Thread(target=self.ui.worker, args=(None,))
            worker.start()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.ui.events.get_nowait(), ("done", exe))
        self.ui.after.assert_not_called()
        self.ui.status.configure.assert_not_called()

    def test_worker_failure_preserves_error_outside_exception_scope(self):
        with patch("instalar_app_windows.install", side_effect=RuntimeError("download failed")):
            self.ui.worker(None)
        self.assertEqual(self.ui.events.get_nowait(), ("error", "download failed"))
        self.ui.after.assert_not_called()

    def test_done_event_enables_open_without_launching_application(self):
        exe = Path("installed/app.exe")
        self.ui.events.put(("done", exe))
        with patch("instalar_app_windows.subprocess.Popen") as launch:
            self.ui.poll()
        self.assertFalse(self.ui.busy)
        self.assertEqual(self.ui.executable, exe)
        self.ui.open_button.configure.assert_called_once_with(state="normal")
        launch.assert_not_called()

    def test_failure_does_not_enable_open_or_declare_completion(self):
        self.ui.events.put(("error", "test failure"))
        with patch("instalar_app_windows.messagebox.showerror") as show:
            self.ui.poll()
        show.assert_called_once_with("Instalação interrompida", "test failure")
        self.assertIsNone(self.ui.executable)
        self.assertFalse(self.ui.busy)
        self.ui.open_button.configure.assert_not_called()

    def test_close_is_blocked_while_installation_is_running(self):
        with patch("instalar_app_windows.messagebox.showinfo") as show:
            self.ui.close()
        show.assert_called_once()
        self.ui.destroy.assert_not_called()

    def test_close_is_allowed_after_completion_or_error(self):
        self.ui.busy = False
        self.ui.close()
        self.ui.destroy.assert_called_once()

    def test_explicit_open_launches_installed_executable_only(self):
        self.ui.executable = Path("installed/app.exe")
        with patch("instalar_app_windows.subprocess.Popen") as launch:
            self.ui.open_app()
        self.assertEqual(launch.call_args.args[0], [str(self.ui.executable)])
        self.assertEqual(launch.call_args.kwargs["cwd"], self.ui.destination)
        self.ui.destroy.assert_called_once()

    def test_launch_error_keeps_installer_open(self):
        self.ui.executable = Path("installed/app.exe")
        with patch("instalar_app_windows.subprocess.Popen", side_effect=OSError("locked")), \
                patch("instalar_app_windows.messagebox.showerror") as show:
            self.ui.open_app()
        show.assert_called_once()
        self.ui.destroy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
