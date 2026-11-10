"""Verify the frozen launcher loads Qt and renders its window using disposable data.

Run with the build machine's Python: python packaging/smoke_gui.py DIST/forza-launcher.exe
The child imports the bundled modules, so this also catches missing/incompatible DLLs.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback


def inside(report):
    try:
        from PySide6.QtCore import QCoreApplication, QEvent, QTimer
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QPixmap
        from forza_launcher import game, paths
        from forza_launcher.config import Config
        from forza_launcher.ui import theme
        from forza_launcher.ui.window import MainWindow
        # The optional backend must be importable in the frozen package. Import
        # only: no device discovery, connection or effects during this smoke test.
        from forza_launcher.haptics.vendor.dualsense.main import DualSense

        # Never discover a player's installation or write their config/forza.toml.
        paths.config_dir = lambda: report.parent
        game.find_game = lambda cfg: (None, [])
        cfg = Config(report.parent / 'launcher.json')
        cfg.loaded = True
        cfg.advanced['data_root'] = str(report.parent / 'data')
        cfg.gpu.update(checked=True, vendor='nvidia')
        cfg.d['check_updates'] = False
        app = QApplication([])
        theme.load_fonts()
        if sorted(theme.LOADED) != sorted(theme.FONT_FILES):
            raise RuntimeError(f'Bundled fonts did not load: {sorted(set(theme.FONT_FILES) - set(theme.LOADED))}')
        app.setStyleSheet(theme.stylesheet())
        if QPixmap(theme.CHECK).isNull():
            raise RuntimeError('Checkbox checkmark asset did not load')
        window = MainWindow(cfg)
        window.show()
        for page in sorted(window._pages):
            window.show_page(page)
            app.processEvents()
            if window.grab().isNull():
                raise RuntimeError(f'Page {page} did not render')
        window.show_page(2)
        search = window.settings.search
        search.edit.setText('mnk_sensitivity')
        if search.results.count() != 1:
            raise RuntimeError('Bundled settings search did not find a configuration key')
        search._choose_current()
        app.processEvents()
        if window.settings.tabs.tabText(window.settings.tabs.currentIndex()) != 'Controls':
            raise RuntimeError('Bundled settings search did not navigate to Controls')
        for family in range(window.controls.family.count()):
            window.controls.family.setCurrentIndex(family)
            app.processEvents()
            if window.controls.diagram.grab().isNull():
                raise RuntimeError('Controller artwork did not render')
        window.controls.haptics_enabled.setChecked(True)
        window.controls.haptics_fields['throttle_strength'].setValue(2)
        if Config(cfg.path).d['haptics']['throttle_strength'] != 2:
            raise RuntimeError('Haptics preferences did not persist')
        window.controls.xbox_enabled.setChecked(True)
        window.controls.xbox_fields['xbox_brake'].setValue(60)
        if Config(cfg.path).d['haptics']['xbox_brake'] != 60:
            raise RuntimeError('Xbox trigger preferences did not persist')
        def finish():
            # Upstream's read-only probes are daemon threads. Let their child
            # processes release the disposable working directory before cleanup.
            if window.setup._probes:
                QTimer.singleShot(25, finish)
            else:
                window.close()
        QTimer.singleShot(250, finish)
        app.exec()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        report.write_text('PASS: bundled Qt loaded and launcher pages rendered\n', encoding='utf-8')
        return 0
    except BaseException:
        report.write_text(traceback.format_exc(), encoding='utf-8')
        return 1


def main():
    if sys.argv[1:2] == ['--inside']:
        return inside(Path(sys.argv[2]))
    executable = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='forza-gui-smoke-') as directory:
        report = Path(directory) / 'result.txt'
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
        result = subprocess.run([str(executable), '--run-script', str(Path(__file__).resolve()),
                                 '--inside', str(report)], env=env, cwd=directory, timeout=30)
        message = report.read_text(encoding='utf-8') if report.exists() else 'FAIL: child produced no report\n'
        print(message, end='')
        return 0 if result.returncode == 0 and message.startswith('PASS:') else 1


if __name__ == '__main__':
    raise SystemExit(main())
