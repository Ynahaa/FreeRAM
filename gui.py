# -*- coding: utf-8 -*-
"""
gui.py — 前端界面 v5 (PySide6 — 无边框一体化设计)
======================================================
- 自定义标题栏（拖拽移动 + 窗口控制按钮）
- 浅色 / 深色双主题
- 关闭窗口 → 隐藏到托盘
"""

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

def _app_dir():
    return os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))

HWND_FILE = os.path.join(_app_dir(), ".hwnd")

# ══════════════════════════════════════════
#  开机自启
# ══════════════════════════════════════════

def _toggle_autostart(enable: bool):
    startup = os.path.join(os.getenv("APPDATA", ""),
                          "Microsoft\\Windows\\Start Menu\\Programs\\Startup")
    lnk = os.path.join(startup, "FreeRAM.lnk")
    if enable:
        try:
            exe = sys.executable
            import subprocess
            ps = f"""
            $WshShell = New-Object -ComObject WScript.Shell
            $Shortcut = $WshShell.CreateShortcut('{lnk}')
            $Shortcut.TargetPath = '{exe}'
            $Shortcut.WorkingDirectory = '{os.path.dirname(exe)}'
            $Shortcut.Save()
            """
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                          capture_output=True, creationflags=0x08000000 if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
        except: pass
    else:
        try:
            if os.path.exists(lnk):
                os.remove(lnk)
        except: pass

# ══════════════════════════════════════════
#  DataCollector
# ══════════════════════════════════════════

class DataCollector:
    def __init__(self, process_name: str):
        self.process_name = process_name
        self._lock = threading.Lock()
        self._running = True
        self.total_mb = self.available_mb = self.used_pct = self.standby_mb = 0.0
        self.game_running = False; self.game_cpu = 0.0
        self.game_states: dict = {}   # {name: {running, cpu, pid}}
        self.clean_count = 0; self.last_freed_mb = 0.0; self.last_trimmed = 0
        self.total_freed_mb = 0.0; self.is_paused = False; self.last_clean_time = ""
        self.clean_history: list = []   # [(time_str, freed_mb, reason), ...]
        self._on_game_state_change = None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        import psutil
        while self._running:
            try:
                mem = psutil.virtual_memory()
                total_mb = mem.total / 1048576; available_mb = mem.available / 1048576
                used_pct = mem.percent
                try:
                    from memory_cleaner import _get_standby_size_mb
                    standby_mb = _get_standby_size_mb(mem)
                except: standby_mb = 0.0

                # 游戏状态：复用 safe_detector 的共享缓存
                try:
                    from safe_detector import get_cached_game_state
                    game_running, game_cpu, states, changes = get_cached_game_state()
                except Exception:
                    game_running, game_cpu, states, changes = False, 0.0, {}, []

                # 逐个进程通知状态变化
                for name, is_running in changes:
                    cb = getattr(self, '_on_game_state_change', None)
                    if cb:
                        cb(name, is_running)

                with self._lock:
                    self.total_mb = round(total_mb, 1)
                    self.available_mb = round(available_mb, 1)
                    self.used_pct = used_pct
                    self.standby_mb = round(standby_mb, 1)
                    self.game_running = game_running
                    self.game_cpu = game_cpu
                    self.game_states = states
            except: pass
            time.sleep(1)

    def snapshot(self):
        with self._lock:
            return {k: getattr(self, k) for k in [
                "total_mb","available_mb","used_pct","standby_mb",
                "game_running","game_cpu","game_states",
                "clean_count","last_freed_mb",
                "last_trimmed","total_freed_mb","is_paused","last_clean_time"]}

    def update_clean_stats(self, a, b, c, d):
        with self._lock:
            self.clean_count = a; self.last_freed_mb = b; self.last_trimmed = c
            self.total_freed_mb = d
            self.last_clean_time = datetime.now().strftime("%H:%M:%S")

    def add_clean_record(self, freed_mb: float, reason: str = ""):
        with self._lock:
            self.clean_history.insert(0, (datetime.now().strftime("%H:%M:%S"), freed_mb, reason))
            if len(self.clean_history) > 10:
                self.clean_history = self.clean_history[:10]

    def update_paused(self, v):
        with self._lock: self.is_paused = v

    def stop(self): self._running = False


# ══════════════════════════════════════════
#  PySide6 GUI
# ══════════════════════════════════════════

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QFrame, QDialog, QLineEdit,
    QCheckBox, QScrollArea, QSizePolicy, QSystemTrayIcon,
)
from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QFont, QColor, QIcon, QPixmap, QAction, QPainterPath, QRegion

# ── 主题色值 ──────────────────────────────────────────

def _theme_qss(dark: bool) -> str:
    if dark:
        return """
* { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; }
QMainWindow { background: transparent; border-radius: 12px; }
QWidget#central { background: #111827; border-radius: 12px; }
QLabel { color: #D1D5DB; }
QFrame#titleBar { background: #1F2937; border-bottom: 1px solid #374151; border-top-left-radius: 12px; border-top-right-radius: 12px; }
QLabel#titleText { color: #F9FAFB; font-size: 13px; font-weight: bold; }
QLabel#targetText { color: #9CA3AF; font-size: 11px; }
QPushButton#winBtn { color: #D1D5DB; background: transparent; border: none; border-radius: 4px; font-size: 14px; padding: 4px 10px; }
QPushButton#winBtn:hover { background: #374151; }
QPushButton#winClose { color: #D1D5DB; background: transparent; border: none; border-radius: 4px; font-size: 14px; padding: 4px 10px; }
QPushButton#winClose:hover { background: #EF4444; color: #FFFFFF; }
QFrame#card { background: #1F2937; border: 1px solid #374151; border-radius: 12px; padding: 14px; }
QLabel#cardTitle { color: #9CA3AF; font-size: 11px; font-weight: bold; }
QLabel#bigNumber { color: #F9FAFB; font-size: 56px; font-weight: bold; }
QLabel#subText { color: #6B7280; font-size: 11px; }
QLabel#normalText { color: #D1D5DB; font-size: 12px; }
QProgressBar { background: #374151; border: none; border-radius: 3px; height: 6px; }
QPushButton { border-radius: 6px; padding: 8px 16px; font-size: 12px; font-weight: bold; }
QPushButton#primaryBtn { background: #F9FAFB; color: #111827; border: none; }
QPushButton#primaryBtn:hover { background: #FFFFFF; }
QPushButton#primaryBtn:pressed { background: #D1D5DB; }
QPushButton#primaryBtn:disabled { background: #374151; color: #6B7280; }
QPushButton#secondaryBtn { background: #1F2937; color: #D1D5DB; border: 1px solid #374151; }
QPushButton#secondaryBtn:hover { background: #374151; }
QPushButton#dangerBtn { background: transparent; color: #EF4444; border: 1px solid #EF4444; }
QPushButton#dangerBtn:hover { background: #7F1D1D; }
QLineEdit { background: #1F2937; color: #F9FAFB; border: 1px solid #374151; border-radius: 4px; padding: 6px 8px; font-size: 12px; }
QLineEdit:focus { border-color: #6366F1; }
QCheckBox { color: #D1D5DB; font-size: 12px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #4B5563; border-radius: 3px; background: #1F2937; }
QCheckBox::indicator:checked { background: #6366F1; border-color: #6366F1; }
QScrollArea { border: none; background: transparent; }
QWidget#settingsContent { background: #111827; }
QDialog { background: #111827; }
QLabel#sectionLabel { color: #9CA3AF; font-size: 11px; font-weight: bold; margin-top: 6px; }
QLabel#historyItem { color: #9CA3AF; font-size: 11px; }
"""
    else:
        return """
* { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; }
QMainWindow { background: transparent; border-radius: 12px; }
QWidget#central { background: #F3F4F6; border-radius: 12px; }
QLabel { color: #4B5563; }
QFrame#titleBar { background: #FFFFFF; border-bottom: 1px solid #E5E7EB; border-top-left-radius: 12px; border-top-right-radius: 12px; }
QLabel#titleText { color: #1F2937; font-size: 13px; font-weight: bold; }
QLabel#targetText { color: #9CA3AF; font-size: 11px; }
QPushButton#winBtn { color: #6B7280; background: transparent; border: none; border-radius: 4px; font-size: 14px; padding: 4px 10px; }
QPushButton#winBtn:hover { background: #E5E7EB; }
QPushButton#winClose { color: #6B7280; background: transparent; border: none; border-radius: 4px; font-size: 14px; padding: 4px 10px; }
QPushButton#winClose:hover { background: #EF4444; color: #FFFFFF; }
QFrame#card { background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 12px; padding: 14px; }
QLabel#cardTitle { color: #9CA3AF; font-size: 11px; font-weight: bold; }
QLabel#bigNumber { color: #1F2937; font-size: 56px; font-weight: bold; }
QLabel#subText { color: #9CA3AF; font-size: 11px; }
QLabel#normalText { color: #4B5563; font-size: 12px; }
QProgressBar { background: #E5E7EB; border: none; border-radius: 3px; height: 6px; }
QPushButton { border-radius: 6px; padding: 8px 16px; font-size: 12px; font-weight: bold; }
QPushButton#primaryBtn { background: #111827; color: #F9FAFB; border: none; }
QPushButton#primaryBtn:hover { background: #1F2937; }
QPushButton#primaryBtn:pressed { background: #030712; }
QPushButton#primaryBtn:disabled { background: #D1D5DB; color: #9CA3AF; }
QPushButton#secondaryBtn { background: #FFFFFF; color: #4B5563; border: 1px solid #D1D5DB; }
QPushButton#secondaryBtn:hover { background: #F3F4F6; }
QPushButton#dangerBtn { background: transparent; color: #EF4444; border: 1px solid #EF4444; }
QPushButton#dangerBtn:hover { background: #FEE2E2; }
QLineEdit { background: #FFFFFF; color: #1F2937; border: 1px solid #D1D5DB; border-radius: 4px; padding: 6px 8px; font-size: 12px; }
QLineEdit:focus { border-color: #6366F1; }
QCheckBox { color: #4B5563; font-size: 12px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #D1D5DB; border-radius: 3px; background: #FFFFFF; }
QCheckBox::indicator:checked { background: #6366F1; border-color: #6366F1; }
QScrollArea { border: none; background: transparent; }
QWidget#settingsContent { background: #F3F4F6; }
QDialog { background: #F3F4F6; }
QLabel#sectionLabel { color: #9CA3AF; font-size: 11px; font-weight: bold; margin-top: 6px; }
QLabel#historyItem { color: #6B7280; font-size: 11px; }
"""

# ── 自定义标题栏 ──────────────────────────────────────

class TitleBar(QFrame):
    """可拖拽的自定义标题栏，含图标、标题、窗口控制按钮。"""

    def __init__(self, win: QMainWindow, icon: QIcon, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(36)
        self._win = win
        self._drag_start: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 4, 0)
        layout.setSpacing(8)

        # 图标 + 标题
        icon_lbl = QLabel()
        icon_lbl.setPixmap(icon.pixmap(20, 20))
        icon_lbl.setFixedSize(20, 20)
        layout.addWidget(icon_lbl)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("titleText")
        layout.addWidget(title_lbl)

        layout.addStretch()

        # 目标进程标签
        self.target_lbl = QLabel("")
        self.target_lbl.setObjectName("targetText")
        layout.addWidget(self.target_lbl)
        layout.addSpacing(12)

        # 窗口控制按钮
        for (text, slot, obj_name) in [
            ("\u2500", self._on_min, "winBtn"),       # ─
            ("\u25A1", self._on_max_toggle, "winBtn"), # □
            ("\u00D7", self._on_close, "winClose"),     # ×
        ]:
            btn = QPushButton(text)
            btn.setObjectName(obj_name)
            btn.setFixedSize(28, 28)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._drag_start
            self._win.move(self._win.pos() + delta)
            self._drag_start = event.globalPosition().toPoint()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_max_toggle()
        super().mouseDoubleClickEvent(event)

    def _on_min(self):
        self._win.showMinimized()

    def _on_max_toggle(self):
        if self._win.isMaximized():
            self._win.showNormal()
        else:
            self._win.showMaximized()

    def _on_close(self):
        # 触发 MemCleanGUI._on_close —— 隐藏到托盘
        self._win.close()


# ══════════════════════════════════════════
#  MemCleanGUI
# ══════════════════════════════════════════

class MemCleanGUI:
    def __init__(self, tray_app, process_name="DeltaForceClient.exe"):
        self.tray = tray_app
        self.process_name = process_name
        self._running = True
        self._dark = tray_app.config.get("dark_mode", False)

        self.collector = DataCollector(process_name)
        self.collector._on_game_state_change = self._on_game_state

        self.app = QApplication.instance() or QApplication([])

        self.win = QMainWindow()
        self.win.setWindowTitle("FreeRAM")
        self.win.setFixedSize(780, 480)

        # 无边框
        self.win.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowMinimizeButtonHint
        )
        # 圆角需要透明背景
        self.win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Windows：原生圆角 + 阴影（Win11 API + Win10 mask fallback）
        self._setup_rounded_window()

        # 图标（标题栏 + 任务栏共用）
        self._app_icon = self._make_icon()
        if self._app_icon:
            self.app.setWindowIcon(self._app_icon)

        # closeEvent → 隐藏到托盘
        self.win.closeEvent = lambda event: (self._on_close(), event.ignore())

        self._build_ui()
        self._apply_theme()

        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.start(500)

        self._sync_timer = QTimer()
        self._sync_timer.timeout.connect(self._sync)
        self._sync_timer.start(2000)

        self._clean_done = False
        self._clean_check_timer = QTimer()
        self._clean_check_timer.timeout.connect(self._check_clean_done)
        self._clean_check_timer.start(300)  # 轮询清理完成标志，可靠恢复按钮

        self._load_winpos()
        self._refresh()

    # ── Theme ────────────────────────────────────

    def _apply_theme(self):
        self.app.setStyleSheet(_theme_qss(self._dark))

    def _set_dark_mode(self, enable: bool):
        self._dark = enable
        self._apply_theme()

    # ── Build UI ─────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        self.win.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 标题栏 ──
        icon = self._app_icon or QIcon()
        self._titlebar = TitleBar(self.win, icon, "FreeRAM")
        self._titlebar.target_lbl.setText(f"目标: {self.process_name}")
        root.addWidget(self._titlebar)

        # ── 内容区 ──
        content = QWidget()
        content.setObjectName("central")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(24, 20, 24, 20)
        cl.setSpacing(12)

        # 内存状态区
        mem_section = QVBoxLayout()
        mem_section.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mem_section.setSpacing(4)

        self.mem_pct = QLabel("--%")
        self.mem_pct.setObjectName("bigNumber")
        self.mem_pct.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mem_section.addWidget(self.mem_pct)

        self.mem_detail = QLabel("")
        self.mem_detail.setObjectName("subText")
        self.mem_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mem_section.addWidget(self.mem_detail)

        self.bar = QProgressBar()
        self.bar.setMaximum(100); self.bar.setValue(0); self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        mem_section.addWidget(self.bar)

        cl.addLayout(mem_section)

        # ── 三列信息卡片 ──
        cards = QHBoxLayout()
        cards.setSpacing(10)

        # 游戏状态卡片 (50%)
        game_card, self.game_dot, self.game_label = self._make_info_card("🎮 游戏状态", "●", "未运行")
        cards.addWidget(game_card, 5)

        # 清理统计卡片 (30%)
        stats_card, _, self.stats_label = self._make_info_card("📊 清理统计", "", "暂无清理")
        cards.addWidget(stats_card, 3)

        # 监控状态卡片 (20%)
        monitor_card, self.monitor_dot, self.monitor_label = self._make_info_card("⚙ 监控", "●", "监控中")
        cards.addWidget(monitor_card, 2)

        cl.addLayout(cards)

        # ── 清理历史 ──
        hist_frame = QFrame()
        hist_frame.setObjectName("card")
        hist_layout = QVBoxLayout(hist_frame)
        hist_layout.setContentsMargins(14, 10, 14, 10)
        hist_layout.setSpacing(4)
        hist_title = QLabel("清理历史")
        hist_title.setObjectName("cardTitle")
        hist_layout.addWidget(hist_title)
        self.history_label = QLabel("暂无清理记录")
        self.history_label.setObjectName("historyItem")
        self.history_label.setWordWrap(True)
        hist_layout.addWidget(self.history_label)
        cl.addWidget(hist_frame)

        # ── 按钮栏 ──
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)

        self.clean_btn = QPushButton("立即清理")
        self.clean_btn.setObjectName("primaryBtn")
        self.clean_btn.setFixedWidth(120)
        self.clean_btn.clicked.connect(self._on_clean)
        btn_bar.addWidget(self.clean_btn)

        hide_btn = QPushButton("隐藏到托盘")
        hide_btn.setObjectName("secondaryBtn")
        hide_btn.clicked.connect(self._on_close)
        btn_bar.addWidget(hide_btn)

        set_btn = QPushButton("设置")
        set_btn.setObjectName("secondaryBtn")
        set_btn.clicked.connect(self._open_settings)
        btn_bar.addWidget(set_btn)

        self.pause_btn = QPushButton("暂停监控")
        self.pause_btn.setObjectName("secondaryBtn")
        self.pause_btn.clicked.connect(self._on_pause_toggle)
        btn_bar.addWidget(self.pause_btn)

        p30 = QPushButton("暂停 30 分")
        p30.setObjectName("secondaryBtn")
        p30.clicked.connect(self._on_pause_30)
        btn_bar.addWidget(p30)

        p60 = QPushButton("暂停 1 时")
        p60.setObjectName("secondaryBtn")
        p60.clicked.connect(self._on_pause_60)
        btn_bar.addWidget(p60)

        btn_bar.addStretch()
        cl.addLayout(btn_bar)

        root.addWidget(content, 1)

    def _make_info_card(self, title: str, dot_text: str, value_text: str):
        card = QFrame()
        card.setObjectName("card")
        l = QVBoxLayout(card)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(6)

        ct = QLabel(title)
        ct.setObjectName("cardTitle")
        l.addWidget(ct)

        row = QHBoxLayout()
        row.setSpacing(6)
        dot = QLabel(dot_text)
        dot.setStyleSheet(f"font-size: 12px; border: none; background: transparent;")
        row.addWidget(dot)
        val = QLabel(value_text)
        val.setObjectName("normalText")
        val.setWordWrap(True)
        row.addWidget(val, 1)
        l.addLayout(row)

        return card, dot, val

    # ── Refresh ──────────────────────────────────

    def _refresh(self):
        try:
            s = self.collector.snapshot()
            pct = s["used_pct"]

            # 颜色
            if self._dark:
                if pct > 85: c = "#F87171"
                elif pct > 70: c = "#FBBF24"
                else: c = "#34D399"
                bar_bg = "#374151"
            else:
                if pct > 85: c = "#EF4444"
                elif pct > 70: c = "#F59E0B"
                else: c = "#10B981"
                bar_bg = "#E5E7EB"

            self.mem_pct.setText(f"{pct:.1f}%")
            self.mem_pct.setStyleSheet(f"font-size: 56px; font-weight: bold; color: {c}; border: none; background: transparent;")

            self.bar.setValue(int(pct))
            self.bar.setStyleSheet(
                f"QProgressBar::chunk {{ background: {c}; border-radius: 3px; }}"
                f"QProgressBar {{ background: {bar_bg}; border: none; border-radius: 3px; height: 6px; }}"
            )

            self.mem_detail.setText(
                f"可用 {s['available_mb']:.0f} MB  ·  备用 {s['standby_mb']:.0f} MB  ·  总计 {s['total_mb']:.0f} MB"
            )

            # 游戏状态 — 多进程逐行显示
            if s.get("is_paused"):
                gray = "#6B7280" if self._dark else "#D1D5DB"
                self.game_dot.setStyleSheet(
                    f"color: {gray}; font-size: 12px; "
                    f"border: none; background: transparent;")
                self.game_label.setText("已暂停监控")
            else:
                states = s.get("game_states", {})
                if states:
                    green = "#10B981"
                    gray = "#6B7280" if self._dark else "#D1D5DB"
                    lines = []
                    any_running = False
                    for name, st in states.items():
                        if st["running"]:
                            any_running = True
                            lines.append(
                                f'<span style="color:{green};">●</span> {name}'
                                f'  | CPU{st["cpu"]:.0f}% | 内存{st["ws_mb"]:.0f}MB')
                        else:
                            lines.append(
                                f'<span style="color:{gray};">○</span> {name}'
                                f'  | 未运行')
                    self.game_dot.setStyleSheet(
                        f"color: {green if any_running else gray}; font-size: 12px; "
                        f"border: none; background: transparent;")
                    self.game_label.setText("<br>".join(lines))
                else:
                    gray = "#6B7280" if self._dark else "#D1D5DB"
                    self.game_dot.setStyleSheet(
                        f"color: {gray}; font-size: 12px; "
                        f"border: none; background: transparent;")
                    self.game_label.setText("未运行")

            # 清理统计
            if s["last_freed_mb"] > 0:
                self.stats_label.setText(
                    f"累计 {s['total_freed_mb']:.0f} MB\n上次 {s['last_freed_mb']:.0f} MB / {s['clean_count']} 次"
                )
            else:
                self.stats_label.setText("暂无清理")

            # 监控状态
            if s["is_paused"]:
                self.monitor_dot.setStyleSheet(f"color: {'#FBBF24' if self._dark else '#F59E0B'}; font-size: 12px; border: none; background: transparent;")
                self.monitor_label.setText("已暂停")
                self.pause_btn.setText("恢复监控")
            else:
                self.monitor_dot.setStyleSheet("color: #10B981; font-size: 12px; border: none; background: transparent;")
                self.monitor_label.setText("监控中")
                self.pause_btn.setText("暂停监控")

            # 清理历史
            hist = getattr(self.collector, 'clean_history', [])
            if hist:
                lines = []
                for t, mb, reason in hist[:5]:
                    lines.append(f"{t}  {mb:.0f} MB  {reason}")
                self.history_label.setText("\n".join(lines))
            else:
                self.history_label.setText("暂无清理记录")

        except: pass

    def _sync(self):
        try:
            lr = self.tray.last_result
            self.collector.update_clean_stats(
                self.tray.clean_count,
                lr["freed_mb_estimate"] if lr else 0,
                lr["processes_trimmed"] if lr else 0,
                self.tray.total_freed_mb)
            self.collector.update_paused(self.tray.paused)
        except: pass

    # ── Icon ─────────────────────────────────────

    def _make_icon(self):
        try:
            from PySide6.QtGui import QPainter, QBrush
            from PySide6.QtCore import QRectF
            pm = QPixmap(64, 64)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            colors = [QColor("#10B981"), QColor("#6366F1"), QColor("#F59E0B")]
            bar_w, gap = 10, 4
            bottom = 54
            heights = [22, 36, 18]
            start_x = (64 - (bar_w * 3 + gap * 2)) // 2
            for i, (h, c) in enumerate(zip(heights, colors)):
                x = start_x + i * (bar_w + gap)
                p.setBrush(QBrush(c))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(QRectF(x, bottom - h, bar_w, h), 2, 2)
            p.end()
            return QIcon(pm)
        except Exception:
            return None

    # ── Pause ────────────────────────────────────

    def _on_pause_toggle(self):
        self.tray._on_toggle_pause()

    def _on_pause_30(self):
        self.tray._on_pause_timed_30()

    def _on_pause_60(self):
        self.tray._on_pause_timed_60()

    # ── Clean ────────────────────────────────────

    def _on_clean(self):
        self.clean_btn.setText("清理中..."); self.clean_btn.setEnabled(False)

        def do():
            try:
                from memory_cleaner import full_clean
                r = full_clean(self.process_name)
                self.tray.last_result = r
                with self.tray._stats_lock:
                    self.tray.clean_count += 1
                    self.tray.total_freed_mb += r["freed_mb_estimate"]
                self.collector.add_clean_record(r["freed_mb_estimate"], "手动")
                self.collector.update_clean_stats(
                    self.tray.clean_count, r["freed_mb_estimate"],
                    r["processes_trimmed"], self.tray.total_freed_mb)
            except Exception:
                pass
            finally:
                self._clean_done = True  # 标志位，由 GUI 线程轮询恢复按钮

        threading.Thread(target=do, daemon=True).start()

    def _check_clean_done(self):
        """GUI 线程轮询：清理完成后恢复按钮。用标志位替代跨线程 QTimer。"""
        if self._clean_done:
            self._clean_done = False
            self.clean_btn.setEnabled(True)
            self.clean_btn.setText("立即清理")

    def _on_game_state(self, name: str, running: bool):
        if not self.tray.config.get("notify_game_state", True):
            return
        if running:
            self.tray.tray.showMessage(
                "FreeRAM", f"🎮 {name} 已启动",
                QSystemTrayIcon.MessageIcon.Information, 3000)
        else:
            self.tray.tray.showMessage(
                "FreeRAM", f"🛑 已停止监控 {name}",
                QSystemTrayIcon.MessageIcon.Information, 3000)

    # ── Settings ─────────────────────────────────

    def _open_settings(self):
        dlg = QDialog(self.win)
        dlg.setWindowTitle("设置")
        dlg.setFixedSize(400, 540)
        dlg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        dlg.setStyleSheet(_theme_qss(self._dark))

        # 外层布局
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 自定义标题栏（颜色与主界面一致）──
        hdr_bg = "#1F2937" if self._dark else "#FFFFFF"
        hdr_border = "#374151" if self._dark else "#E5E7EB"
        hdr_text = "#F9FAFB" if self._dark else "#1F2937"

        header = QFrame()
        header.setFixedHeight(36)
        header.setStyleSheet(
            f"QFrame {{ background: {hdr_bg}; border-bottom: 1px solid {hdr_border}; "
            f"border-top-left-radius: 8px; border-top-right-radius: 8px; }}")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 0, 4, 0)
        hl.setSpacing(0)

        title_lbl = QLabel("设置")
        title_lbl.setStyleSheet(
            f"color: {hdr_text}; font-size: 13px; font-weight: bold; "
            f"border: none; background: transparent;")
        hl.addWidget(title_lbl)
        hl.addStretch()

        close_btn = QPushButton("\u00D7")  # ×
        close_btn.setObjectName("winClose")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(dlg.reject)
        hl.addWidget(close_btn)

        outer.addWidget(header)

        # ── 内容区 ──
        bg = "#111827" if self._dark else "#F3F4F6"
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ background: {bg}; border: none; }}")
        scroll.viewport().setStyleSheet(f"background: {bg};")
        content = QWidget()
        content.setStyleSheet(f"background: {bg};")
        l = QVBoxLayout(content)
        l.setContentsMargins(20, 14, 20, 14)
        l.setSpacing(6)

        cfg_path = os.path.join(_app_dir(), "config.json")
        cfg = {}
        try:
            with open(cfg_path, "r", encoding="utf-8") as f: cfg = json.load(f)
        except: pass

        def srow(title, default):
            lb = QLabel(title)
            f = lb.font(); f.setPointSize(11); lb.setFont(f)
            l.addWidget(lb)
            le = QLineEdit()
            le.setText(str(default))
            l.addWidget(le)
            return le

        def section(title):
            lb = QLabel(title)
            lb.setObjectName("sectionLabel")
            l.addWidget(lb)

        section("基础设置")
        pn = srow("进程名 (可设置多个进程，用逗号分隔)", cfg.get("process_name", ""))

        # ── 进程名查重 ──
        dup_warn = QLabel("")
        dup_warn.setStyleSheet("color: #EF4444; font-size: 11px; font-weight: bold; padding: 2px 0;")
        dup_warn.setWordWrap(True)
        dup_warn.hide()
        l.addWidget(dup_warn)

        def _dedup_process_name(text: str):
            pn.blockSignals(True)
            try:
                items = [x.strip() for x in text.split(",")]
                seen = set()
                unique = []
                has_dup = False
                for item in items:
                    if not item:
                        unique.append(item)
                        continue
                    lower = item.lower()
                    if lower in seen:
                        has_dup = True
                        continue
                    seen.add(lower)
                    unique.append(item)
                if has_dup:
                    new_text = ", ".join(unique)
                    pn.setText(new_text)
                    dup_warn.setText("⚠ 检测到重复进程名，已自动移除")
                    dup_warn.show()
                    # 2 秒后自动隐藏
                    QTimer.singleShot(2000, dup_warn.hide)
            finally:
                pn.blockSignals(False)

        pn.textChanged.connect(_dedup_process_name)

        mt = srow("内存阈值 % 可用", cfg.get("mem_available_threshold_pct", 15))
        st = srow("备用列表阈值 MB", cfg.get("standby_threshold_mb", 512))

        notify_cb = QCheckBox("游戏启停通知"); notify_cb.setChecked(cfg.get("notify_game_state", True)); l.addWidget(notify_cb)
        bg_cb = QCheckBox("清理后台闲置进程"); bg_cb.setChecked(cfg.get("trim_background", True)); l.addWidget(bg_cb)
        auto_cb = QCheckBox("开机自启"); auto_cb.setChecked(cfg.get("auto_start", False)); l.addWidget(auto_cb)
        dark_cb = QCheckBox("暗色模式"); dark_cb.setChecked(cfg.get("dark_mode", False)); l.addWidget(dark_cb)

        section("清理触发条件")
        ci = srow("游戏 CPU 空闲阈值 %", cfg.get("game_cpu_idle_threshold", 40))
        cc = srow("清理冷却时间 秒", cfg.get("auto_clean_cooldown_sec", 120))
        pi = srow("检测间隔 秒", cfg.get("poll_interval_sec", 5))
        nc = srow("通知冷却 秒", cfg.get("notify_cooldown_sec", 600))

        section("后台修剪")
        bgo = QCheckBox("仅在内存危及时修剪"); bgo.setChecked(cfg.get("trim_bg_critical_only", True)); l.addWidget(bgo)
        bgm = srow("最小工作集 MB", cfg.get("trim_bg_min_working_mb", 50))
        bgx = srow("每轮最多修剪数", cfg.get("trim_bg_max_count", 20))
        bgc = srow("后台修剪冷却 秒", cfg.get("trim_bg_cooldown_sec", 300))
        bge = srow("排除进程 (可设置多个进程，用逗号分隔)", ",".join(cfg.get("trim_bg_exclude", [])))

        def save():
            try:
                cfg["process_name"] = pn.text().strip()
                cfg["mem_available_threshold_pct"] = float(mt.text())
                cfg["standby_threshold_mb"] = int(st.text())
                cfg["notify_game_state"] = notify_cb.isChecked()
                cfg["trim_background"] = bg_cb.isChecked()
                cfg["auto_start"] = auto_cb.isChecked()
                cfg["dark_mode"] = dark_cb.isChecked()
                cfg["game_cpu_idle_threshold"] = float(ci.text())
                cfg["auto_clean_cooldown_sec"] = int(cc.text())
                cfg["poll_interval_sec"] = int(pi.text())
                cfg["notify_cooldown_sec"] = int(nc.text())
                cfg["trim_bg_critical_only"] = bgo.isChecked()
                cfg["trim_bg_min_working_mb"] = int(bgm.text())
                cfg["trim_bg_max_count"] = int(bgx.text())
                cfg["trim_bg_cooldown_sec"] = int(bgc.text())
                cfg["trim_bg_exclude"] = [x.strip() for x in bge.text().split(",") if x.strip()]
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2, ensure_ascii=False)
                _toggle_autostart(cfg["auto_start"])
                self._set_dark_mode(cfg["dark_mode"])
                dlg.setStyleSheet(_theme_qss(self._dark))
                import safe_detector as sd
                sd.MEM_AVAILABLE_THRESHOLD_PCT = cfg["mem_available_threshold_pct"]
                sd.STANDBY_THRESHOLD_MB = cfg["standby_threshold_mb"]
                sd.GAME_CPU_IDLE_THRESHOLD = cfg["game_cpu_idle_threshold"]
                sd.AUTO_CLEAN_COOLDOWN_SEC = cfg["auto_clean_cooldown_sec"]
                self.process_name = cfg["process_name"]
                self.tray.process_name = cfg["process_name"]
                self.tray.config = cfg
                self.collector.process_name = cfg["process_name"]
                self._titlebar.target_lbl.setText(f"目标: {cfg['process_name']}")
                # 立即重扫进程列表，避免等 5 轮轮询才更新
                from safe_detector import force_rescan
                force_rescan()
            except: pass
            dlg.accept()

        sb = QPushButton("保存")
        sb.clicked.connect(save)
        sb.setStyleSheet("""
            QPushButton {
                background: #10B981; color: #FFFFFF; font-size: 14px;
                font-weight: bold; padding: 10px 0px; border: none;
                border-radius: 6px;
            }
            QPushButton:hover { background: #34D399; }
            QPushButton:pressed { background: #059669; }
        """)
        l.addWidget(sb)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        dlg.exec()

    # ── Window management ─────────────────────────

    def _on_close(self):
        self._save_winpos()
        self._write_hwnd()       # 确保 .hwnd 始终有效，供单实例呼出
        self.win.clearMask()
        self.win.hide()

    def _save_winpos(self):
        try:
            wp = os.path.join(_app_dir(), ".winpos")
            g = self.win.geometry()
            with open(wp, "w") as f: f.write(f"{g.x()},{g.y()}")
        except: pass

    def _load_winpos(self):
        try:
            wp = os.path.join(_app_dir(), ".winpos")
            if os.path.exists(wp):
                with open(wp, "r") as f: parts = f.read().strip().split(",")
                if len(parts) == 2:
                    self.win.move(int(parts[0]), int(parts[1]))
        except: pass

    # ── Rounded corners ────────────────────────────

    def _setup_rounded_window(self):
        """Win11 原生圆角 API + 通用 QRegion mask 双保险。"""
        if sys.platform == "win32":
            try:
                from ctypes import windll, c_int, byref, sizeof
                self._hwnd = int(self.win.winId())
                # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2
                windll.dwmapi.DwmSetWindowAttribute(
                    self._hwnd, 33, byref(c_int(2)), sizeof(c_int))
                self._write_hwnd()
            except: pass
        self._apply_rounded_mask()

    def _write_hwnd(self):
        """将窗口句柄写入 .hwnd 文件，供单实例呼出使用。"""
        try:
            hwnd = getattr(self, '_hwnd', None)
            if hwnd:
                with open(HWND_FILE, "w") as f:
                    f.write(str(hwnd))
        except Exception:
            pass

    def _apply_rounded_mask(self):
        """用 QPainterPath 将窗口剪裁为 12px 圆角矩形。"""
        path = QPainterPath()
        path.addRoundedRect(self.win.rect().toRectF(), 12, 12)
        self.win.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def show(self):
        """恢复窗口 —— 调用方已在 GUI 线程，直接用 Qt 原生方法。"""
        self.win.show()
        self.win.raise_()
        self.win.activateWindow()
        self._apply_rounded_mask()

    def run(self):
        self.win.show()
        self.app.exec()

    def stop(self):
        """从任意线程安全地退出应用（QApplication.quit() 是 thread-safe 的）。"""
        self._save_winpos()
        self._running = False
        self.collector.stop()
        self.app.quit()
