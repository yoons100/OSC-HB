import json
import os
import queue
import socket
import sys
import threading
import time
try:
    import winreg
except Exception:
    winreg = None
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QSpinBox,
    QSystemTrayIcon, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    QMenu
)

try:
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import ThreadingOSCUDPServer
    from pythonosc.udp_client import SimpleUDPClient
except Exception as e:
    Dispatcher = None
    ThreadingOSCUDPServer = None
    SimpleUDPClient = None

APP_NAME = "OSC Heartbeat Monitor"

BASE_DIR = Path.home() / "Documents" / "Heartbeat"
BASE_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = BASE_DIR / "config.json"

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    "listen_ip": "0.0.0.0",
    "app_mode": "server",
    "listen_port": 9000,
    "heartbeat_timeout_sec": 5,
    "pc_alive_interval_sec": 1,
    "send_pc_alive": True,
    "show_disconnect_popup": True,
    "play_alert_sound": False,
    "minimize_to_tray_on_launch": True,
    "start_with_windows": False,
    "devices": [
        {"enabled": True, "name": "iPad Main", "ip": "192.168.0.101", "port": 8000},
        {"enabled": True, "name": "iPad Backup", "ip": "192.168.0.102", "port": 8000},
    ]
}


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)
    return str(Path(base) / name)


def ensure_dirs():
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Logger:
    def __init__(self):
        ensure_dirs()

    def write(self, msg: str):
        try:
            f = LOG_DIR / f"heartbeat_{datetime.now().strftime('%Y-%m-%d')}.log"
            with f.open("a", encoding="utf-8") as fp:
                fp.write(f"[{now_str()}] {msg}\n")
        except Exception:
            pass


class Config:
    def __init__(self):
        self.data = DEFAULT_CONFIG.copy()
        self.load()

    def load(self):
        ensure_dirs()
        if CONFIG_PATH.exists():
            try:
                loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                data = DEFAULT_CONFIG.copy()
                data.update(loaded)
                self.data = data
            except Exception:
                self.data = DEFAULT_CONFIG.copy()
        else:
            self.save()

    def save(self):
        ensure_dirs()
        CONFIG_PATH.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")


class OSCServerThread(threading.Thread):
    def __init__(self, app_ref, listen_ip, listen_port):
        super().__init__(daemon=True)
        self.app_ref = app_ref
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.server = None

    def run(self):
        if Dispatcher is None:
            self.app_ref.event_queue.put(("error", "python-osc is not installed."))
            return
        disp = Dispatcher()
        disp.map("/heartbeat", self.on_heartbeat)
        disp.map("/heartbeat/*", self.on_heartbeat)
        disp.set_default_handler(self.on_any)
        try:
            self.server = ThreadingOSCUDPServer((self.listen_ip, int(self.listen_port)), disp)
            self.app_ref.event_queue.put(("server_started", f"Listening {self.listen_ip}:{self.listen_port}"))
            self.server.serve_forever()
        except Exception as e:
            self.app_ref.event_queue.put(("error", f"OSC listen error: {e}"))

    def on_heartbeat(self, address, *args):
        ip = None
        try:
            name = address.split("/")[-1] if address.startswith("/heartbeat/") else None
            self.app_ref.event_queue.put(("heartbeat", {"name": name, "address": address, "time": time.time()}))
        except Exception:
            pass

    def on_any(self, address, *args):
        if address in ("/alive", "/ipad/alive") or address.startswith("/alive/") or address.startswith("/ipad/alive/"):
            name = address.split("/")[-1] if address.count("/") >= 2 else None
            self.app_ref.event_queue.put(("heartbeat", {"name": name, "address": address, "time": time.time()}))

    def stop(self):
        try:
            if self.server:
                self.server.shutdown()
                self.server.server_close()
        except Exception:
            pass


class RawUDPServerThread(threading.Thread):
    def __init__(self, app_ref, listen_ip, listen_port):
        super().__init__(daemon=True)
        self.app_ref = app_ref
        self.listen_ip = listen_ip
        self.listen_port = int(listen_port)
        self.sock = None
        self.running = False

    def run(self):
        self.running = True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((self.listen_ip, self.listen_port))
            self.sock.settimeout(0.5)
            self.app_ref.event_queue.put(("server_started", f"Listening {self.listen_ip}:{self.listen_port}"))
            while self.running:
                try:
                    data, addr = self.sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                text = data.split(b"\x00", 1)[0].decode("utf-8", errors="ignore")
                if text.startswith("/heartbeat") or text.startswith("/alive") or text.startswith("/ipad/alive"):
                    name = text.split("/")[-1] if text.count("/") >= 2 else None
                    self.app_ref.event_queue.put(("heartbeat", {"name": name, "ip": addr[0], "port": addr[1], "address": text, "time": time.time()}))
                elif text.startswith("/pc/alive"):
                    name = text.split("/")[-1] if text.count("/") >= 2 else None
                    self.app_ref.event_queue.put(("pc_alive", {"name": name, "ip": addr[0], "port": addr[1], "address": text, "time": time.time()}))
        except Exception as e:
            self.app_ref.event_queue.put(("error", f"UDP listen error: {e}"))

    def stop(self):
        self.running = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass



class SilentPopup(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setStyleSheet("""
            QWidget {
                background: #2b2b2b;
                color: white;
                border: 1px solid #666;
                border-radius: 8px;
            }
            QLabel {
                background: transparent;
                border: none;
                padding: 4px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        self.title = QLabel(APP_NAME)
        self.title.setStyleSheet("font-weight: bold; background: transparent; border: none;")
        self.message = QLabel("")
        self.message.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(self.title)
        layout.addWidget(self.message)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def show_message(self, text: str, duration_ms: int = 3000):
        self.message.setText(text)
        self.adjustSize()
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.right() - self.width() - 24
            y = geo.bottom() - self.height() - 48
            self.move(max(x, geo.left()), max(y, geo.top()))
        self.show()
        self.raise_()
        self.timer.start(duration_ms)


class SettingsWindow(QMainWindow):
    def __init__(self, app_ref):
        super().__init__()
        self.app_ref = app_ref
        self.setWindowTitle("OSC Heartbeat Settings")
        self.setWindowIcon(QIcon(resource_path("hb.ico")))
        self.resize(760, 520)
        self.build_ui()
        self.load_from_config()

    def build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)

        form = QFormLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Server Mode - Wait for heartbeat, then reply", "server")
        self.mode_combo.addItem("Client Mode - Send heartbeat and wait for reply", "client")
        self.listen_port = QSpinBox(); self.listen_port.setRange(1, 65535)
        self.timeout = QSpinBox(); self.timeout.setRange(1, 60)
        self.alive_interval = QSpinBox(); self.alive_interval.setRange(1, 60)
        form.addRow("Mode", self.mode_combo)
        form.addRow("Listen Port", self.listen_port)
        form.addRow("Heartbeat Timeout (sec)", self.timeout)
        form.addRow("Heartbeat Interval (sec)", self.alive_interval)
        layout.addLayout(form)

        self.send_pc_alive = QCheckBox("Enable OSC Reply/Send")
        self.popup = QCheckBox("Show Popup on Disconnect")
        self.sound = QCheckBox("Play Alert Sound")
        self.min_tray = QCheckBox("Start Minimized to Tray")
        self.startup = QCheckBox("Start with Windows")
        for c in [self.send_pc_alive, self.popup, self.sound, self.min_tray, self.startup]:
            layout.addWidget(c)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Enable", "Name", "IP Address", "Receive Port"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(QLabel("Device List (Server: devices sending heartbeat / Client: servers to check)"))
        layout.addWidget(self.table)

        btns = QHBoxLayout()
        add = QPushButton("+"); add.clicked.connect(self.add_row)
        remove = QPushButton("-"); remove.clicked.connect(self.remove_row)
        save = QPushButton("Save & Restart"); save.clicked.connect(self.save_settings)
        close = QPushButton("Close"); close.clicked.connect(self.hide)
        btns.addWidget(add); btns.addWidget(remove); btns.addStretch(1); btns.addWidget(save); btns.addWidget(close)
        layout.addLayout(btns)
        self.setCentralWidget(root)

    def add_row(self, device=None):
        r = self.table.rowCount()
        self.table.insertRow(r)
        enabled = QTableWidgetItem()
        enabled.setFlags(enabled.flags() | Qt.ItemIsUserCheckable)
        enabled.setCheckState(Qt.Checked if (device or {}).get("enabled", True) else Qt.Unchecked)
        self.table.setItem(r, 0, enabled)
        self.table.setItem(r, 1, QTableWidgetItem((device or {}).get("name", f"Device {r+1}")))
        self.table.setItem(r, 2, QTableWidgetItem((device or {}).get("ip", "192.168.0.")))
        self.table.setItem(r, 3, QTableWidgetItem(str((device or {}).get("port", 8000))))

    def remove_row(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def load_from_config(self):
        d = self.app_ref.config.data
        mode = d.get("app_mode", "server")
        idx = self.mode_combo.findData(mode)
        self.mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.listen_port.setValue(int(d.get("listen_port", 9000)))
        self.timeout.setValue(int(d.get("heartbeat_timeout_sec", 5)))
        self.alive_interval.setValue(int(d.get("pc_alive_interval_sec", 1)))
        self.send_pc_alive.setChecked(bool(d.get("send_pc_alive", True)))
        self.popup.setChecked(bool(d.get("show_disconnect_popup", True)))
        self.sound.setChecked(bool(d.get("play_alert_sound", False)))
        self.min_tray.setChecked(bool(d.get("minimize_to_tray_on_launch", True)))
        self.startup.setChecked(bool(d.get("start_with_windows", False)))
        self.table.setRowCount(0)
        for dev in d.get("devices", []):
            self.add_row(dev)

    def save_settings(self):
        devices = []
        for r in range(self.table.rowCount()):
            try:
                devices.append({
                    "enabled": self.table.item(r, 0).checkState() == Qt.Checked,
                    "name": self.table.item(r, 1).text().strip() or f"Device {r+1}",
                    "ip": self.table.item(r, 2).text().strip(),
                    "port": int(self.table.item(r, 3).text().strip() or "8000"),
                })
            except Exception:
                QMessageBox.warning(self, "Input Error", f"Please check device row {r+1}.")
                return
        self.app_ref.config.data.update({
            "app_mode": self.mode_combo.currentData(),
            "listen_port": self.listen_port.value(),
            "heartbeat_timeout_sec": self.timeout.value(),
            "pc_alive_interval_sec": self.alive_interval.value(),
            "send_pc_alive": self.send_pc_alive.isChecked(),
            "show_disconnect_popup": self.popup.isChecked(),
            "play_alert_sound": self.sound.isChecked(),
            "minimize_to_tray_on_launch": self.min_tray.isChecked(),
            "start_with_windows": self.startup.isChecked(),
            "devices": devices,
        })
        self.app_ref.config.save()
        self.app_ref.apply_startup_setting()
        self.app_ref.restart_monitor()
        self.app_ref.show_info_notice("Settings saved and monitoring restarted.", 2500)


class HeartbeatApp:
    def __init__(self):
        self.qt = QApplication(sys.argv)
        self.qt.setWindowIcon(QIcon(resource_path("hb.ico")))
        self.qt.setQuitOnLastWindowClosed(False)
        self.config = Config()
        self.logger = Logger()
        self.event_queue = queue.Queue()
        self.last_seen = {}
        self.status = {}
        self.previous_status = {}
        self.server_thread = None
        self.last_alive_send = 0
        self.settings = SettingsWindow(self)
        self.tray = QSystemTrayIcon()
        self.green_icon = QIcon(resource_path("hb_green.ico"))
        self.red_icon = QIcon(resource_path("hb_red.ico"))
        self.tray.setIcon(self.red_icon)
        self.tray.setVisible(True)
        self.setup_menu()
        self.tray.activated.connect(self.on_tray_activated)
        self.silent_popup = SilentPopup()
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(250)
        self.logger.write("APP START")
        self.restart_monitor()
        if not self.config.data.get("minimize_to_tray_on_launch", True):
            self.settings.show()

    def setup_menu(self):
        self.tray_menu = QMenu()

        self.action_settings = QAction("Settings", self.tray_menu)
        self.action_settings.triggered.connect(self.show_settings)

        self.action_log = QAction("Open Log Folder", self.tray_menu)
        self.action_log.triggered.connect(self.open_log_folder)

        self.action_quit = QAction("Quit", self.tray_menu)
        self.action_quit.triggered.connect(self.quit)

        self.tray_menu.addAction(self.action_settings)
        self.tray_menu.addAction(self.action_log)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.action_quit)
        self.tray.setContextMenu(self.tray_menu)

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_settings()
        elif reason == QSystemTrayIcon.Context:
            self.tray_menu.popup(QCursor.pos())

    def open_log_folder(self):
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(LOG_DIR))
        except Exception as e:
            self.tray.showMessage(APP_NAME, f"Could not open log folder: {e}", QSystemTrayIcon.Warning, 3000)

    def show_settings(self):
        self.settings.load_from_config()
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def restart_monitor(self):
        if self.server_thread:
            self.server_thread.stop()
        self.last_seen.clear()
        self.status.clear()
        self.previous_status.clear()
        # Use raw UDP server to detect sender IP reliably.
        self.server_thread = RawUDPServerThread(self, self.config.data.get("listen_ip", "0.0.0.0"), self.config.data.get("listen_port", 9000))
        self.server_thread.start()
        for dev in self.enabled_devices():
            self.status[dev["name"]] = False
            self.previous_status[dev["name"]] = None
        self.update_tray()

    def enabled_devices(self):
        return [d for d in self.config.data.get("devices", []) if d.get("enabled", True)]

    def tick(self):
        while True:
            try:
                typ, payload = self.event_queue.get_nowait()
            except queue.Empty:
                break
            if typ == "heartbeat":
                self.handle_heartbeat(payload)
            elif typ == "pc_alive":
                self.handle_pc_alive(payload)
            elif typ == "server_started":
                self.logger.write(payload)
            elif typ == "error":
                self.logger.write("ERROR " + payload)
                self.tray.showMessage(APP_NAME, payload, QSystemTrayIcon.Warning, 4000)
        self.check_status()
        if self.config.data.get("app_mode", "server") == "client" and self.config.data.get("send_pc_alive", True):
            if time.time() - self.last_alive_send >= int(self.config.data.get("pc_alive_interval_sec", 1)):
                self.send_heartbeat()
                self.last_alive_send = time.time()
        self.update_tray()

    def match_devices(self, payload):
        ip = payload.get("ip")
        name = payload.get("name")
        matched = []
        for dev in self.enabled_devices():
            if ip and dev.get("ip") == ip:
                matched.append(dev)
            elif name and name.lower() == dev.get("name", "").lower():
                matched.append(dev)
        if not matched and len(self.enabled_devices()) == 1:
            matched = [self.enabled_devices()[0]]
        return matched

    def handle_heartbeat(self, payload):
        if self.config.data.get("app_mode", "server") != "server":
            return
        now = payload.get("time", time.time())
        for dev in self.match_devices(payload):
            name = dev["name"]
            self.last_seen[name] = now
            if self.config.data.get("send_pc_alive", True):
                self.send_pc_alive_to(dev)

    def handle_pc_alive(self, payload):
        # Client Mode: this PC sends /heartbeat and waits for /pc/alive reply.
        if self.config.data.get("app_mode", "server") != "client":
            return
        now = payload.get("time", time.time())
        for dev in self.match_devices(payload):
            self.last_seen[dev["name"]] = now

    def show_info_notice(self, message: str, duration_ms: int = 2500):
        self.silent_popup.show_message(message, duration_ms)

    def show_disconnect_notice(self, message: str):
        self.silent_popup.show_message(message, 3000)
        if self.config.data.get("play_alert_sound", False):
            QApplication.beep()

    def check_status(self):
        timeout = int(self.config.data.get("heartbeat_timeout_sec", 5))
        now = time.time()
        for dev in self.enabled_devices():
            name = dev["name"]
            online = name in self.last_seen and (now - self.last_seen[name] <= timeout)
            prev = self.status.get(name, False)
            self.status[name] = online
            if self.previous_status.get(name) is None:
                self.previous_status[name] = online
                self.logger.write(f"{name} {'CONNECTED' if online else 'WAITING'}")
            elif online != prev:
                self.logger.write(f"{name} {'RECONNECTED' if online else 'DISCONNECTED'}")
                if not online and self.config.data.get("show_disconnect_popup", True):
                    self.show_disconnect_notice(f"{name} disconnected")
                elif not online and self.config.data.get("play_alert_sound", False):
                    QApplication.beep()

    def send_heartbeat(self):
        for dev in self.enabled_devices():
            try:
                self.send_simple_osc(dev["ip"], int(dev.get("port", 9000)), "/heartbeat", 1)
            except Exception:
                pass

    def send_pc_alive_to(self, dev):
        try:
            self.send_simple_osc(dev["ip"], int(dev.get("port", 8000)), "/pc/alive", 1)
        except Exception:
            pass

    def send_simple_osc(self, ip, port, address, value):
        def pad4(b):
            return b + (b"\0" * ((4 - len(b) % 4) % 4))
        data = pad4(address.encode()+b"\0") + pad4(b",i\0") + int(value).to_bytes(4, "big", signed=True)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(data, (ip, port))
        s.close()

    def update_tray(self):
        devices = self.enabled_devices()
        all_ok = bool(devices) and all(self.status.get(d["name"], False) for d in devices)
        self.tray.setIcon(self.green_icon if all_ok else self.red_icon)
        mode = self.config.data.get("app_mode", "server").upper()
        lines = [f"{APP_NAME} ({mode})"]
        for d in devices:
            lines.append(("[OK] " if self.status.get(d["name"], False) else "[OFF] ") + d["name"])
        if not devices:
            lines.append("No devices configured")
        self.tray.setToolTip("\n".join(lines[:12]))


    def apply_startup_setting(self):
        try:
            run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE) as key:
                if self.config.data.get("start_with_windows", False):
                    exe = sys.executable if getattr(sys, "frozen", False) else str(Path(__file__).resolve())
                    cmd = f'"{exe}"'
                    winreg.SetValueEx(key, "OSCHeartbeatMonitor", 0, winreg.REG_SZ, cmd)
                    self.logger.write("STARTUP ENABLED")
                else:
                    try:
                        winreg.DeleteValue(key, "OSCHeartbeatMonitor")
                        self.logger.write("STARTUP DISABLED")
                    except FileNotFoundError:
                        pass
        except Exception as e:
            self.logger.write(f"STARTUP SETTING ERROR {e}")

    def quit(self):
        self.logger.write("APP EXIT")
        if self.server_thread:
            self.server_thread.stop()
        self.tray.setVisible(False)
        self.qt.quit()

    def run(self):
        return self.qt.exec()


if __name__ == "__main__":
    app = HeartbeatApp()
    sys.exit(app.run())
