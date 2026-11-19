"""Application bootstrap."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from . import perf  # noqa: F401  (imported first so startup timing begins here)
from .config import Config, data_dir


def _setup_logging():
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(data_dir() / "jarvis.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    for noisy in ("urllib3", "faster_whisper", "comtypes", "OpenGL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> int:
    _setup_logging()
    log = logging.getLogger("jarvis")

    if sys.platform == "win32":
        try:  # own taskbar identity + crisp DPI
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Jarvis.PersonalAI.1")
        except Exception:
            pass
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from .ui.hologram.renderer import default_surface_format

    QSurfaceFormat.setDefaultFormat(default_surface_format())
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    app.setApplicationName("JARVIS")
    app.setOrganizationName("JARVIS")

    from .ui import theme
    from .ui.icon import make_icon
    from .ui.main_window import MainWindow

    app.setStyleSheet(theme.stylesheet())
    app.setWindowIcon(make_icon())

    cfg = Config.load()
    perf.bind(cfg)
    log.info("Starting JARVIS (data: %s)", data_dir())
    win = MainWindow(cfg)
    if cfg.start_maximized:
        win.showMaximized()
    else:
        win.show()
    return app.exec()
