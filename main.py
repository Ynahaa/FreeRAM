# -*- coding: utf-8 -*-
"""
main.py — FreeRAM 入口
================================
- 进程优先级设为 IDLE（不跟游戏抢 CPU）
- 单实例检测（双击 exe 呼出已有窗口）
- 自带工作集定时修剪
"""

import argparse
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import threading
import time
import traceback

def _app_dir() -> str:
    """exe 所在目录（PyInstaller 打包后 sys._MEIPASS 是临时目录，不可用）。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

SCRIPT_DIR = _app_dir()
LOG_FILE = os.path.join(SCRIPT_DIR, "log.txt")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")

DEFAULT_CONFIG = {
    "process_name": "DeltaForceClient-Win64-Shipping.exe",
    "start_minimized": False,
    "mem_available_threshold_pct": 15.0,
    "standby_threshold_mb": 1024,
    "game_cpu_idle_threshold": 40.0,
    "auto_clean_cooldown_sec": 120,
    "notify_cooldown_sec": 600,
    "notify_game_state": True,
    "poll_interval_sec": 5,
    "trim_background": True,
    "trim_bg_critical_only": True,
    "trim_bg_min_working_mb": 50,
    "trim_bg_max_count": 20,
    "trim_bg_cooldown_sec": 300,
    "trim_bg_exclude": [],
    "auto_start": False,
    "dark_mode": False,
}

k32 = ctypes.windll.kernel32
IDLE_PRIORITY_CLASS = 0x00000040
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000

# ═══════════════════════════════════════════════════════

def setup_logging(verbose: bool = False):
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)-5s] %(message)s", datefmt="%H:%M:%S")
    fh = RotatingFileHandler(LOG_FILE, encoding="utf-8", maxBytes=1048576, backupCount=2)
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if verbose or "--once" in sys.argv or "--info" in sys.argv:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.DEBUG if verbose else logging.INFO)
        ch.setFormatter(fmt)
        root.addHandler(ch)
    return logging.getLogger(__name__)


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            cfg.update(loaded)
        except Exception:
            pass
    else:
        # 首次运行：用默认值创建 config.json
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    return cfg


def save_config(cfg: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


_MUTEX_HANDLE = None


def is_another_instance_running() -> bool:
    """使用 Windows 命名 Mutex 检测单实例（进程崩溃 OS 自动释放）。"""
    global _MUTEX_HANDLE
    _MUTEX_HANDLE = k32.CreateMutexW(None, False, "Local\\FreeRAM_SingleInstance")
    if _MUTEX_HANDLE == 0:
        return False
    if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        k32.CloseHandle(_MUTEX_HANDLE)
        _MUTEX_HANDLE = None
        return True
    return False


def set_low_priority():
    """将当前进程设为 IDLE 优先级 — 只在系统完全空闲时才获得 CPU。"""
    try:
        k32.SetPriorityClass(k32.GetCurrentProcess(), IDLE_PRIORITY_CLASS)
    except Exception:
        try:
            k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        except Exception:
            pass


def self_trim_loop():
    """后台线程：每 30 秒修剪工具自身的进程工作集，减少内存占用。"""
    while True:
        time.sleep(30)
        try:
            psapi = ctypes.windll.psapi
            h = k32.GetCurrentProcess()
            k32.SetProcessWorkingSetSize(h, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
            psapi.EmptyWorkingSet(h)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="FreeRAM")
    parser.add_argument("--process", "-p", default=None)
    parser.add_argument("--once", "-1", action="store_true", help="执行一次清理后退出")
    parser.add_argument("--info", "-i", action="store_true", help="显示内存状态后退出")
    parser.add_argument("--verbose", "-v", action="store_true", help="DEBUG 日志")
    parser.add_argument("--minimized", "-m", action="store_true", help="启动后最小化到托盘")
    parser.add_argument("--scan", "-s", action="store_true", help="扫描运行中的进程，帮助找到游戏进程名")
    parser.add_argument("--keywords", "-k", default=None, help="扫描关键字，逗号分隔")
    args = parser.parse_args()

    cfg = load_config()
    process_name = args.process or cfg.get("process_name", "DeltaForceClient-Win64-Shipping.exe")

    # 保存配置（命令行指定的优先级更高）
    if args.process:
        cfg["process_name"] = args.process
        save_config(cfg)

    log = setup_logging(verbose=args.verbose)

    # ── 一次性命令 ──
    if args.info:
        from memory_cleaner import get_memory_info
        info = get_memory_info()
        print(f"总内存:     {info['total_mb']:>8.0f} MB")
        print(f"可用:       {info['available_mb']:>8.0f} MB")
        print(f"使用率:     {info['used_pct']:>7.1f}%")
        print(f"备用列表:   {info['standby_mb']:>8.0f} MB")
        return

    if args.once:
        from memory_cleaner import full_clean
        result = full_clean(process_name)
        print(f"清理前可用: {result['before_available_mb']:.0f} MB")
        print(f"清理后可用: {result['after_available_mb']:.0f} MB")
        print(f"预计释放:   {result['freed_mb_estimate']:.0f} MB")
        return

    if args.scan:
        _scan_processes(args.keywords)
        return

    # ── 单实例检测（Windows Mutex，崩溃安全）──
    if is_another_instance_running():
        log.info("已有实例运行中，尝试呼出窗口...")
        _bring_existing_to_front()
        return

    # ── 启动 ──
    set_low_priority()

    # 预热备用列表缓存（避免首次查询卡 1-3 秒 PowerShell）
    _warm_standby_cache()

    # 注入配置到 safe_detector（覆盖默认阈值）
    _apply_config_to_detector(cfg)

    log.info("━" * 36)
    log.info(f"FreeRAM 启动, 目标: {process_name}")
    log.info(f"进程优先级: IDLE")
    log.info("━" * 36)

    # 自身工作集修剪线程
    trim_thread = threading.Thread(target=self_trim_loop, daemon=True)
    trim_thread.start()

    try:
        from PySide6.QtWidgets import QApplication
        from tray_icon import MemCleanTray
        from gui import MemCleanGUI

        # QApplication 必须在 QSystemTrayIcon / QWidget 之前创建
        _app = QApplication.instance() or QApplication([])

        tray = MemCleanTray(process_name=process_name, gui=None, config=cfg)
        gui = MemCleanGUI(tray_app=tray, process_name=process_name)
        tray.gui = gui

        # 启动时最小化
        start_minimized = args.minimized or cfg.get("start_minimized", False)
        if start_minimized:
            gui.root.withdraw()

        # QSystemTrayIcon 与 GUI 共享同一 Qt 事件循环，无需 daemon 线程
        log.info("GUI 已启动")
        gui.run()
    except Exception:
        log.critical(f"启动失败:\n{traceback.format_exc()}")
    finally:
        # 清理旧版 .pid 残留（从 PID 文件迁移到 Mutex 的过渡期）
        try:
            old_pid = os.path.join(SCRIPT_DIR, ".pid")
            if os.path.exists(old_pid):
                os.remove(old_pid)
        except OSError:
            pass
        log.info("FreeRAM 已退出")


def _scan_processes(custom_keywords=None):
    """列出系统中名称匹配关键字的进程，帮助用户找到游戏进程名。"""
    import psutil
    if custom_keywords:
        keywords = [k.strip().lower() for k in custom_keywords.split(",") if k.strip()]
    else:
        keywords = ["delta", "force", "game", "shipping", "win64", "client"]
    print("\n正在扫描进程...")
    print("═" * 50)
    all_procs = []
    for proc in psutil.process_iter(["name", "pid"]):
        try: all_procs.append((proc.info["name"], proc.info["pid"]))
        except: continue
    all_procs.sort(key=lambda x: x[0].lower())

    matched = []
    for name, pid in all_procs:
        nl = name.lower()
        for kw in keywords:
            if kw in nl:
                matched.append((name, pid))
                break

    if matched:
        print("可能的目标进程:")
        print("─" * 50)
        for name, pid in matched:
            print(f"  {name:<45} PID: {pid}")
    else:
        print("未找到匹配关键字的进程。")
    print("─" * 50)
    print(f"系统共 {len(all_procs)} 个进程，其中 {len(matched)} 个匹配")
    print("\n在 config.json 中设置 process_name 为上述某个名称。")
    print("或在设置面板（齿轮图标）中修改。")
    input("\n按回车键退出...")


def _warm_standby_cache():
    """后台线程：启动时提前查询备用列表，避免首次检测卡 PowerShell 延迟。"""
    def _warm():
        from memory_cleaner import _get_standby_size_mb
        _get_standby_size_mb()
    threading.Thread(target=_warm, daemon=True).start()


def _apply_config_to_detector(cfg: dict):
    """将 config.json 中的阈值写入 safe_detector 模块。"""
    try:
        import safe_detector as sd
        sd.MEM_AVAILABLE_THRESHOLD_PCT = cfg.get("mem_available_threshold_pct", 15.0)
        sd.STANDBY_THRESHOLD_MB = cfg.get("standby_threshold_mb", 1024)
        sd.GAME_CPU_IDLE_THRESHOLD = cfg.get("game_cpu_idle_threshold", 40.0)
        sd.AUTO_CLEAN_COOLDOWN_SEC = cfg.get("auto_clean_cooldown_sec", 120)
    except Exception:
        pass


def _bring_existing_to_front():
    """尝试通过 .hwnd 文件呼出已有实例的窗口。"""
    hwnd_file = os.path.join(SCRIPT_DIR, ".hwnd")
    try:
        if os.path.exists(hwnd_file):
            with open(hwnd_file, "r") as f:
                hwnd = int(f.read().strip())
            user32 = ctypes.windll.user32
            if user32.IsWindow(hwnd):
                user32.ShowWindow(hwnd, 9)   # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                return
    except Exception:
        pass
    # 如果无法呼出，至少提示
    try:
        ctypes.windll.user32.MessageBoxW(
            0, "FreeRAM 已在后台运行。\n请右键点击右下角托盘图标打开窗口。",
            "FreeRAM", 0x40,
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
