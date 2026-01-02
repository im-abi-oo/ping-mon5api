# main.py
import sys
import time
import threading
import logging
from typing import Optional

import requests
from PySide6.QtCore import QThread, Signal, Slot, Qt
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton
)

# --------- config ----------
INTERVAL = 300  # ثابتا 5 دقیقه = 300 ثانیه
MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 2  # seconds
REQUEST_TIMEOUT = 10  # seconds
URL = "https://mon5abi.onrender.com/api/ping"
HEADERS = {"Content-Type": "application/json", "x-secret": "4321"}
# --------------------------

# logging
logger = logging.getLogger("saino")
logger.setLevel(logging.DEBUG)
fh = logging.FileHandler("saino.log", encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
logger.addHandler(fh)


class PingWorker(QThread):
    status_signal = Signal(str)       # messages for status label
    countdown_signal = Signal(int)    # seconds remaining until next send
    finished_signal = Signal()

    def __init__(self, identifier: str, parent=None):
        super().__init__(parent)
        self.identifier = identifier
        self._stop_event = threading.Event()

    def run(self):
        logger.info("Worker started: id=%s", self.identifier)
        while not self._stop_event.is_set():
            # SEND with retries
            success = False
            for attempt in range(1, MAX_RETRIES + 1):
                if self._stop_event.is_set():
                    break
                try:
                    payload = {"identifier": self.identifier, "name": "vps"}
                    logger.debug("Attempt %d sending: %s", attempt, payload)
                    resp = requests.post(URL, json=payload, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                    code = resp.status_code
                    # try parse json, else text
                    try:
                        body = resp.json()
                        body_str = str(body)
                    except Exception:
                        body_str = resp.text.strip()
                    # shorten long bodies for UI
                    display_body = (body_str[:300] + "...") if len(body_str) > 300 else body_str
                    if 200 <= code < 300:
                        msg = f"ارسال موفق ({code}) — {display_body}"
                        self.status_signal.emit(msg)
                        logger.info("Success %s", msg)
                    else:
                        msg = f"پاسخ {code} — {display_body}"
                        self.status_signal.emit(msg)
                        logger.warning("Non-2xx %s", msg)
                    success = True
                    break
                except Exception as e:
                    logger.exception("Request failed on attempt %d", attempt)
                    self.status_signal.emit(f"خطا در ارسال (تلاش {attempt}) — {str(e)[:200]}")
                    # exponential backoff, but responsive to stop
                    delay = INITIAL_RETRY_DELAY * (2 ** (attempt - 1))
                    if delay > 30:
                        delay = 30
                    # wait with early exit
                    waited = 0
                    while waited < delay and not self._stop_event.is_set():
                        self._stop_event.wait(1)
                        waited += 1

            if not success:
                self.status_signal.emit("ارسال ناموفق پس از تلاش‌ها ❌")
                logger.warning("All retries failed for id=%s", self.identifier)

            # COUNTDOWN INTERVAL (show remaining seconds each second)
            remaining = INTERVAL
            while remaining > 0 and not self._stop_event.is_set():
                # emit remaining seconds for UI (countdown)
                self.countdown_signal.emit(remaining)
                # wait 1 second but break quickly if stopped
                self._stop_event.wait(1)
                remaining -= 1

        logger.info("Worker stopping (stop_event set)")
        self.finished_signal.emit()

    def stop(self):
        self._stop_event.set()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ping Monitor — Saino")
        self.setFixedSize(460, 260)
        self.setStyleSheet("""
            QWidget { background: #0f0f12; color: #e6eef6; font-family: "Segoe UI", Tahoma, Arial; }
            QLabel { color: #cfd8e3; }
            QLineEdit { background: #121217; color: #ffffff; border: 1px solid #25303a; padding: 6px; border-radius:4px; }
            QPushButton { background: #2f6df6; color: white; padding: 8px 14px; border-radius:6px; }
            QPushButton[secondary="true"] { background: #2b2b2b; color: #ddd; }
            QLabel#timer { color: #00e5ff; font-size: 28px; font-family: "Consolas"; }
            QLabel#status { color: #b9c6d9; font-size: 12px; }
            QLabel#interval { color: #9fb0c9; font-size: 11px; }
        """)

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel("IDENTIFIER"))
        self.identifier_edit = QLineEdit()
        self.identifier_edit.setPlaceholderText("مثال: worker (1)")
        layout.addWidget(self.identifier_edit)

        # fixed interval info
        interval_label = QLabel("خوش اومدی")
        interval_label.setObjectName("interval")
        layout.addWidget(interval_label)

        self.timer_label = QLabel("05:00")
        self.timer_label.setObjectName("timer")
        self.timer_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.timer_label)

        self.status_label = QLabel("آماده")
        self.status_label.setObjectName("status")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self.on_start)
        btn_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setProperty("secondary", True)
        self.stop_btn.clicked.connect(self.on_stop)
        self.stop_btn.setEnabled(False)
        btn_layout.addWidget(self.stop_btn)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

        self.worker: Optional[PingWorker] = None

    @Slot()
    def on_start(self):
        identifier = self.identifier_edit.text().strip()
        if not identifier:
            self.status_label.setText("IDENTIFIER وارد نشده")
            return
        if self.worker is not None:
            self.status_label.setText("در حال اجراست")
            return

        self.worker = PingWorker(identifier=identifier)
        self.worker.status_signal.connect(self.on_status)
        self.worker.countdown_signal.connect(self.on_countdown)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.identifier_edit.setEnabled(False)
        self.status_label.setText("در حال اجرا...")

    @Slot()
    def on_stop(self):
        if self.worker:
            self.worker.stop()
            self.status_label.setText("در حال توقف... (صبر کن) ")
            self.stop_btn.setEnabled(False)

    @Slot(str)
    def on_status(self, msg: str):
        # show short or multiline status
        self.status_label.setText(msg)

    @Slot(int)
    def on_countdown(self, remaining_seconds: int):
        mins = remaining_seconds // 60
        secs = remaining_seconds % 60
        self.timer_label.setText(f"{mins:02d}:{secs:02d}")

    @Slot()
    def on_finished(self):
        logger.info("Worker finished -> UI cleanup")
        self.worker = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.identifier_edit.setEnabled(True)
        self.status_label.setText("متوقف شد")
        self.timer_label.setText("05:00")

    def closeEvent(self, event):
        if self.worker:
            logger.info("Window closing — stopping worker")
            self.worker.stop()
            # wait up to 3 seconds for clean stop
            self.worker.wait(3000)
        event.accept()


def main():
    logger.info("Application starting")
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    app.exec()
    logger.info("Application exited")


if __name__ == "__main__":
    main()
