"""Background tasks: extracting, installing DLC, building. Each runs in a QThread and reports by signals."""
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from .. import builder, datafolder, errors, fetch, installer, paths


class Task(QThread):
    log = Signal(str)
    status = Signal(str)
    progress = Signal(float, str)   # 0..1, detail
    done = Signal(bool, str)        # success, message

    def __init__(self, fn, name="task"):
        super().__init__()
        self.fn, self.name = fn, name
        self.stage = ""
        self._cancel = False
        self.blocked = False    # the data folder was denied (builder.FolderBlocked)
        self.warning = False    # it worked, but its message is a warning (damaged DLC)

    def cancel(self):
        self._cancel = True

    def cancelled(self):
        return self._cancel

    def report_status(self, text):
        self.stage = text
        self.status.emit(text)

    def run(self):
        try:
            msg = self.fn(self)
            self.done.emit(True, msg or "")
        except (installer.Cancelled, fetch.Cancelled):
            self.done.emit(False, "Cancelled.")
        except (installer.InstallError, builder.BuildError) as e:
            self.blocked = self.blocked or isinstance(e, builder.FolderBlocked)
            log = paths.log_error(f"{self.name} ({self.stage}): {e}\n{getattr(e, 'detail', '')}", e)
            self.done.emit(False, errors.message(e, self.name, log, self.stage))
        except BaseException as e:  # never let a thread die silently (SystemExit from a tool included)
            log = paths.log_error(f"{self.name} ({self.stage}): unexpected {type(e).__name__}", e)
            self.done.emit(False, errors.message(e, self.name, log, self.stage))


class Probe(QObject):
    """Runs fn() on a daemon thread (a program to start, a package to open: not on the GUI thread) and
    emits `result` with what it returned, in the GUI thread; None when it raised (logged). A daemon
    thread cannot hold up closing the window."""
    result = Signal(object)

    def __init__(self, fn, name="probe", parent=None):
        super().__init__(parent)
        self.fn, self.name = fn, name

    def start(self):
        import threading
        threading.Thread(target=self._run, name=self.name, daemon=True).start()

    def _run(self):
        try:
            value = self.fn()
        except BaseException as e:
            paths.log_error(f"{self.name}: unexpected {type(e).__name__}", e)
            value = None
        try:
            self.result.emit(value)
        except RuntimeError:   # the page was deleted meanwhile
            pass


class TaskReporter(builder.Reporter):
    """Adapts a Task to the builder's Reporter."""

    def __init__(self, task: Task):
        self.t = task

    def log(self, text):
        self.t.log.emit(text)

    def status(self, text):
        self.t.report_status(text)

    def progress(self, fraction, detail=""):
        self.t.progress.emit(float(fraction), detail)

    def cancelled(self):
        return self.t.cancelled()


def extract_task(package, dest):
    def fn(t: Task):
        def prog(done, total, name):
            t.progress.emit(done / total if total else 0, name)
        t.report_status("Extracting the game files")
        notes = []
        total = installer.extract_game(package, Path(dest), prog, t.cancelled, notes.append)
        return f"Extracted {total / 2**30:.1f} GB. " + " ".join(notes)
    return Task(fn, "extract game files")


def verify_task(folder):
    """Check an extracted game folder (decoding the executable of another printing takes a while)."""
    def fn(t: Task):
        t.report_status("Checking the game files")
        ok, msg = installer.check_game_folder(Path(folder))
        if not ok:
            raise installer.InstallError(msg)
        return msg
    return Task(fn, "check game files")


def dlc_task(folder, user_data):
    def fn(t: Task):
        def prog(done, total, name):
            t.progress.emit(done / total if total else 0, name)
        t.report_status("Installing DLC")
        damaged = []
        n = installer.install_dlc_folder(folder, Path(user_data), prog, t.cancelled, t.log.emit, damaged)
        t.warning = bool(damaged)
        return f"Installed {n} DLC package(s)." + installer.damaged_note(damaged)
    return Task(fn, "install DLC")


def build_task(opts, game_files):
    def fn(t: Task):
        rt = builder.run_build(opts, TaskReporter(t), Path(game_files))
        return f"Built: {rt}"
    return Task(fn, "build")


def prebuild_task(cfg):
    """Prebuild shaders (#226): rebuild the listed shaders from the game files, then let the game build
    every pipeline into the driver's cache and exit."""
    def fn(t: Task):
        from .. import game, shader_prebuild
        g, looked = game.find_game(cfg)
        if g is None:
            raise builder.BuildError(game.missing_message(looked))
        try:
            return shader_prebuild.prebuild(cfg, g, t.report_status, lambda f, d: t.progress.emit(f, d),
                                            t.cancelled, t.log.emit)
        except InterruptedError:
            raise installer.Cancelled() from None
        except shader_prebuild.PrebuildError as e:
            raise builder.BuildError(str(e)) from e
    return Task(fn, "prebuild shaders")


def move_task(old, new, with_user, with_game):
    """Copy the user data of the old data folder into the new one (the old one is left as it is)."""
    def fn(t: Task):
        t.report_status("Copying your saves and files to the new folder")

        def prog(frac, name):
            t.progress.emit(frac, name)
        try:
            n = datafolder.copy_user_data(Path(old), Path(new), with_user, with_game, prog, t.cancelled, t.log.emit)
        except InterruptedError:
            raise installer.Cancelled()
        except OSError as e:
            if datafolder.is_access_denied_error(e):
                t.blocked = True
                raise installer.InstallError(datafolder.blocked_message(new, str(e))) from e
            raise
        return f"Copied {n / 2**30:.1f} GB to {new}. The old folder {old} was not changed."
    return Task(fn, "move data folder")
