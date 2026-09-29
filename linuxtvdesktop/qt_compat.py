"""Picks PyQt5 or PySide6 at import time and re-exports the Qt names the app uses."""

import importlib
import logging
import os
import sys


def _load_qt_binding():
    preferred = os.getenv("LINUXTV_QT_BINDING")
    if preferred:
        order = [preferred]
    elif sys.platform.startswith("linux"):
        order = ["PyQt5", "PySide6"]
    else:
        order = ["PySide6", "PyQt5"]

    for binding in order:
        try:
            if binding == "PyQt5":
                qt_core = importlib.import_module("PyQt5.QtCore")
                qt_gui = importlib.import_module("PyQt5.QtGui")
                qt_widgets = importlib.import_module("PyQt5.QtWidgets")
                qt_quickwidgets = importlib.import_module("PyQt5.QtQuickWidgets")
                signal_type = qt_core.pyqtSignal
                slot_type = qt_core.pyqtSlot
                property_type = qt_core.pyqtProperty
            elif binding == "PySide6":
                qt_core = importlib.import_module("PySide6.QtCore")
                qt_gui = importlib.import_module("PySide6.QtGui")
                qt_widgets = importlib.import_module("PySide6.QtWidgets")
                qt_quickwidgets = importlib.import_module("PySide6.QtQuickWidgets")
                signal_type = qt_core.Signal
                slot_type = qt_core.Slot
                property_type = qt_core.Property
            else:
                logging.warning("Unknown Qt binding requested: %s", binding)
                continue

            return (
                binding,
                qt_core.QEvent,
                qt_core.QObject,
                qt_core.QRect,
                qt_core.Qt,
                qt_core.QTimer,
                qt_core.QUrl,
                signal_type,
                slot_type,
                property_type,
                qt_gui.QFontDatabase,
                qt_gui.QIcon,
                qt_gui.QImage,
                qt_gui.QKeyEvent,
                qt_gui.QPixmap,
                qt_gui.QColor,
                qt_gui.QPainter,
                qt_widgets.QApplication,
                qt_widgets.QMainWindow,
                qt_widgets.QMessageBox,
                qt_widgets.QWidget,
                qt_quickwidgets.QQuickWidget,
            )
        except ImportError:
            continue

    raise ImportError("No supported Qt binding found. Install PyQt5 or PySide6.")


(
    QT_BINDING,
    QEvent,
    QObject,
    QRect,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
    Property,
    QFontDatabase,
    QIcon,
    QImage,
    QKeyEvent,
    QPixmap,
    QColor,
    QPainter,
    QApplication,
    QMainWindow,
    QMessageBox,
    QWidget,
    QQuickWidget,
) = _load_qt_binding()
