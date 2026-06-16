"""
tray_icon.py — 系统托盘 UI (QSystemTrayIcon)
==============================================
使用 PySide6 QSystemTrayIcon，与 GUI 共享 Qt 事件循环。
后台 5 秒轮询自动清理（daemon 线程）。
"""

import logging
import time
import threading

import psutil
from PySide6.QtWidgets import QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QBrush
from PySide6.QtCore import Qt, QTimer

from memory_cleaner import full_clean, get_memory_info, trim_background_processes
from safe_detector import is_safe_to_clean, mark_clean_done, mark_bg_trim_done, is_bg_trim_in_cooldown

logger = logging.getLogger(__name__)


def _make_icon_image(color: tuple[int, int, int] = (76, 175, 80), size: int = 64) -> QIcon:
    """用 QPainter 绘制纯色圆形图标，返回 QIcon。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor(*color)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(4, 4, size - 8, size - 8)
    painter.end()
    return QIcon(pm)


class MemCleanTray:
    """系统托盘控制器 —— 所有 Qt 交互都在主线程，后台轮询在 daemon 线程。"""

    def __init__(self, process_name: str = "DeltaForceClient.exe", gui=None,
                 config: dict | None = None):
        self.process_name = process_name
        self.gui = gui
        self.config = config or {}
        self.paused = False
        self.running = True
        self.last_result: dict | None = None
        self.clean_count = 0
        self.total_freed_mb = 0.0
        self._last_notify_time = 0.0
        self._stats_lock = threading.Lock()

        # 预建图标
        self.icon_green = _make_icon_image((76, 175, 80))
        self.icon_yellow = _make_icon_image((255, 193, 7))
        self.icon_blue = _make_icon_image((33, 150, 243))

        # QSystemTrayIcon
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(self.icon_green)
        self.tray.setToolTip("FreeRAM — 监控中")
        self.tray.activated.connect(self._on_activated)
        self._build_menu()
        self.tray.show()

        # 启动提示（延迟到事件循环就绪后）
        QTimer.singleShot(1500, lambda: self.tray.showMessage(
            "FreeRAM 已启动",
            "后台监控中，每 5 秒自动检测\n右键托盘图标可打开窗口",
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        ))

        # 后台轮询线程
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    # ── 菜单 ───────────────────────────────────────────

    def _build_menu(self):
        menu = QMenu()

        if self.last_result:
            a = menu.addAction(
                f"上次释放 {self.last_result['freed_mb_estimate']:.0f} MB")
            a.setEnabled(False)

        a = menu.addAction("显示窗口")
        a.triggered.connect(self._on_show_window)

        a = menu.addAction("立即清理")
        a.triggered.connect(self._on_manual_clean)

        pause_label = "恢复监控" if self.paused else "暂停监控"
        a = menu.addAction(pause_label)
        a.triggered.connect(self._on_toggle_pause)

        a = menu.addAction("暂停 30 分钟")
        a.triggered.connect(self._on_pause_timed_30)

        a = menu.addAction("暂停 1 小时")
        a.triggered.connect(self._on_pause_timed_60)

        menu.addSeparator()

        a = menu.addAction("退出")
        a.triggered.connect(self._on_exit)

        self._menu = menu
        self.tray.setContextMenu(menu)

    def _rebuild_menu(self):
        self._build_menu()

    # ── 托盘激活 ──────────────────────────────────────

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason):
        """左键双击托盘图标 → 显示窗口。"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._on_show_window()

    # ── 菜单回调（均在 GUI 线程执行）─────────────────

    def _on_show_window(self):
        if self.gui is not None:
            self.gui.show()

    def _on_manual_clean(self):
        """手动清理——重活放到后台线程，避免阻塞 GUI 事件循环。"""
        self._set_icon_blue()

        def _do():
            try:
                result = full_clean(self.process_name)
                self.last_result = result
                with self._stats_lock:
                    self.clean_count += 1
                    self.total_freed_mb += result["freed_mb_estimate"]
                # UI 更新必须在主线程
                QTimer.singleShot(0, self._update_icon_by_memory)
                QTimer.singleShot(0, lambda: self.tray.showMessage(
                    f"清理完成 — 第 {self.clean_count} 次",
                    f"释放约 {result['freed_mb_estimate']:.0f} MB"
                    f"\n修剪进程数: {result['processes_trimmed']}",
                    QSystemTrayIcon.MessageIcon.Information,
                    3000,
                ))
            except Exception:
                logger.error("手动清理异常", exc_info=True)
                QTimer.singleShot(0, self._update_icon_by_memory)

        threading.Thread(target=_do, daemon=True).start()

    def _on_toggle_pause(self):
        self.paused = not self.paused
        self._update_pause_state()
        self._rebuild_menu()

    def _on_pause_timed_30(self):
        self.paused = True
        self._update_pause_state()
        self._rebuild_menu()
        threading.Thread(target=self._auto_resume, args=(30 * 60,),
                         daemon=True).start()

    def _on_pause_timed_60(self):
        self.paused = True
        self._update_pause_state()
        self._rebuild_menu()
        threading.Thread(target=self._auto_resume, args=(60 * 60,),
                         daemon=True).start()

    def _auto_resume(self, seconds: int):
        time.sleep(seconds)
        if self.paused:
            self.paused = False
            # 从 daemon 线程触发的 UI 更新，需要通过 QTimer 投递
            QTimer.singleShot(0, self._update_pause_state)
            QTimer.singleShot(0, self._rebuild_menu)
            QTimer.singleShot(0, lambda: self.tray.showMessage(
                "FreeRAM", "监控已自动恢复",
                QSystemTrayIcon.MessageIcon.Information, 3000))

    def _on_exit(self):
        self.running = False
        if self.gui is not None:
            self.gui.stop()

    # ── 图标 / 状态更新 ───────────────────────────────

    def _update_pause_state(self):
        if self.paused:
            self.tray.setIcon(self.icon_yellow)
            self.tray.setToolTip("FreeRAM — 已暂停")
        else:
            self._update_icon_by_memory()
            self.tray.setToolTip("FreeRAM — 监控中")

    def _update_icon_by_memory(self):
        """根据当前内存压力更新图标颜色。"""
        try:
            info = get_memory_info()
            pct = info["used_pct"]
            if pct > 85:
                color = (233, 69, 96)
            elif pct > 70:
                color = (255, 193, 7)
            else:
                color = (76, 175, 80)
            self.tray.setIcon(_make_icon_image(color))
        except Exception:
            self.tray.setIcon(self.icon_green)

    def _set_icon_green(self):
        self.tray.setIcon(self.icon_green)
        self.tray.setToolTip(
            "FreeRAM — 监控中" if not self.paused else "FreeRAM — 已暂停")

    def _set_icon_blue(self):
        self.tray.setIcon(self.icon_blue)

    # ── 后台修剪 ──────────────────────────────────────

    def _try_background_trim(self):
        """修剪后台进程 + 降级非关键进程优先级（需配置开启 + 冷却通过）。"""
        if not self.config.get("trim_background", False):
            return
        critical_only = self.config.get("trim_bg_critical_only", True)
        if critical_only:
            mem = psutil.virtual_memory()
            avail_pct = (mem.available / mem.total) * 100
            if avail_pct > 8.0:
                return
        bg_cooldown = self.config.get("trim_bg_cooldown_sec", 300)
        if is_bg_trim_in_cooldown(bg_cooldown):
            return

        logger.info("[后台修剪] 开始扫描...")

        # Tier 4：降级非关键进程优先级（温和，不做冷却判断）
        from memory_cleaner import demote_background_processes
        demoted = demote_background_processes(self.config, self.process_name)
        if demoted:
            logger.info(f"[优先级降级] {demoted} 个进程已降为后台模式")

        # Tier 2：游戏工作集保留 + 非游戏上限
        from memory_cleaner import apply_working_set_limits
        limited = apply_working_set_limits(self.config, self.process_name)
        if limited:
            logger.info(f"[工作集限制] {limited} 个进程已应用")

        # Tier 3：游戏进程反馈式修剪（PageFaultCount 监控）
        from memory_cleaner import (trim_game_with_feedback,
                                     is_feedback_cooldown_active)
        from safe_detector import get_cached_game_state
        if not is_feedback_cooldown_active():
            __, __, states, __ = get_cached_game_state()
            for st in states.values():
                pid = st.get("pid")
                if pid is not None and st.get("running"):
                    fb = trim_game_with_feedback(pid)
                    if fb["freed_mb"] > 0:
                        with self._stats_lock:
                            self.total_freed_mb += fb["freed_mb"]
                    logger.info(
                        f"[反馈修剪] 游戏释放 {fb['freed_mb']:.0f}MB "
                        f"(page fault +{fb['fault_delta']}, "
                        f"safe={fb['safe']})")

        # Tier 1：激进修剪后台进程工作集
        results = trim_background_processes(self.config, self.process_name)
        if results:
            total_freed = sum(r[2] - r[3] for r in results)
            with self._stats_lock:
                self.total_freed_mb += total_freed
            logger.info(
                f"[后台修剪] 完成: {len(results)} 个进程, 释放 {total_freed:.0f}MB")
        mark_bg_trim_done()

    # ── 后台轮询（daemon 线程）────────────────────────

    def _poll_loop(self):
        interval = self.config.get("poll_interval_sec", 5)
        first_run = True
        while self.running:
            try:
                if not first_run:
                    time.sleep(interval)
                first_run = False
                if self.paused:
                    continue

                info = get_memory_info()
                gs = ""
                try:
                    if self.gui and self.gui.collector:
                        s = self.gui.collector.snapshot()
                        states = s.get("game_states", {})
                        running_names = [
                            n for n, st in states.items() if st["running"]]
                        if running_names:
                            gs = " | " + ", ".join(running_names)
                except Exception:
                    pass

                # 从 daemon 线程更新 UI：必须通过 QTimer 投递到主线程
                tip = (
                    f"可用 {info['available_mb']:.0f}MB "
                    f"({100 - info['used_pct']:.0f}%) "
                    f"| 累计释放 {self.total_freed_mb:.0f}MB{gs}"
                )
                QTimer.singleShot(0, lambda t=tip: self.tray.setToolTip(t))

                if not self.paused:
                    QTimer.singleShot(0, self._update_icon_by_memory)

                logger.info(
                    f"[心跳] 可用 {info['available_mb']:.0f}MB "
                    f"({100 - info['used_pct']:.0f}%) "
                    f"| 备用 {info['standby_mb']:.0f}MB")

                safe, reason = is_safe_to_clean(self.process_name)
                if not safe:
                    continue

                logger.info(f"[AutoClean] {reason}")
                QTimer.singleShot(0, self._set_icon_blue)
                result = full_clean(self.process_name)
                self.last_result = result
                with self._stats_lock:
                    self.clean_count += 1
                    self.total_freed_mb += result["freed_mb_estimate"]
                if self.gui:
                    self.gui.collector.add_clean_record(
                        result["freed_mb_estimate"], "自动")
                mark_clean_done()
                QTimer.singleShot(0, self._update_icon_by_memory)

                # Tier 2：后台进程修剪
                self._try_background_trim()

                # 限频通知
                notify_cooldown = self.config.get("notify_cooldown_sec", 600)
                now = time.time()
                if (result["freed_mb_estimate"] > 50
                        and (now - self._last_notify_time) > notify_cooldown):
                    self._last_notify_time = now
                    QTimer.singleShot(0, lambda: self.tray.showMessage(
                        f"自动清理 — 释放约 {result['freed_mb_estimate']:.0f} MB",
                        f"原因: {reason}",
                        QSystemTrayIcon.MessageIcon.Information,
                        3000,
                    ))
            except Exception:
                logger.error("轮询异常", exc_info=True)

    def run(self):
        """QSystemTrayIcon 已通过 show() 启动，无需单独事件循环。"""
        pass
