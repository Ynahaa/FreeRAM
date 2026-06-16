"""
safe_detector.py — 安全时机检测
===============================
三级判断 + 冷却机制：
  0. 冷却期 (两次清理 ≥ 2 分钟间隔)
  1. 阈值关 (可用内存 < 15% 且 备用列表 > 1GB)
  2. 窗口关 (前台不是游戏 → 安全；是游戏 + CPU 低 → 也安全)
"""

import time
import psutil
import ctypes
from ctypes import wintypes, byref

user32 = ctypes.windll.user32

MEM_AVAILABLE_THRESHOLD_PCT = 15.0
MEM_CRITICAL_THRESHOLD_PCT = 8.0    # < 8% 无条件触发
STANDBY_THRESHOLD_MB = 512          # 放宽到 512MB
GAME_CPU_IDLE_THRESHOLD = 40.0
AUTO_CLEAN_COOLDOWN_SEC = 120

_last_auto_clean_time: float = 0.0
_last_bg_trim_time: float = 0.0

# ── 内存阈值 ──────────────────────────────────────────
def memory_is_tight() -> bool:
    mem = psutil.virtual_memory()
    available_pct = (mem.available / mem.total) * 100

    # 极其紧张 (< 8%)：无条件触发，不管备用列表
    if available_pct <= MEM_CRITICAL_THRESHOLD_PCT:
        return True

    # 紧张 (8-15%)：需备用列表 > 512MB
    if available_pct <= MEM_AVAILABLE_THRESHOLD_PCT:
        try:
            from memory_cleaner import _get_standby_size_mb
            standby_mb = _get_standby_size_mb(mem)
        except:
            standby_mb = (mem.cached or 0) / 1048576
        return standby_mb >= STANDBY_THRESHOLD_MB

    return False

_game_pid_cache: int | None = None
_game_proc_cache = None
_game_cpu_cache: float = 0.0
_game_running_cache: bool = False
_pid_check_counter = 0


def is_game_idle(process_name: str = "DeltaForceClient.exe") -> bool:
    global _game_pid_cache, _game_proc_cache, _game_cpu_cache, _game_running_cache, _pid_check_counter
    _pid_check_counter += 1

    if _pid_check_counter >= 5 or _game_pid_cache is None:
        _pid_check_counter = 0
        _game_pid_cache = None; _game_proc_cache = None
        _game_running_cache = False
        targets = [n.strip().lower() for n in process_name.split(",") if n.strip()]
        for proc in psutil.process_iter(["name", "pid"]):
            try:
                if proc.info["name"].lower() in targets:
                    _game_pid_cache = proc.info["pid"]; break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    if _game_pid_cache is not None:
        try:
            if _game_proc_cache is None or _game_proc_cache.pid != _game_pid_cache:
                _game_proc_cache = psutil.Process(_game_pid_cache)
            if _game_proc_cache.is_running():
                try:
                    _game_cpu_cache = _game_proc_cache.cpu_percent()
                except Exception:
                    _game_cpu_cache = 0.0
                _game_running_cache = True
                return _game_cpu_cache < GAME_CPU_IDLE_THRESHOLD
            else:
                _game_pid_cache = None; _game_proc_cache = None
                _game_running_cache = False
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            _game_pid_cache = None; _game_proc_cache = None
            _game_running_cache = False

    return True


def get_cached_game_state() -> tuple[bool, float, int | None]:
    """
    返回缓存的游戏状态——供 GUI DataCollector 复用，避免重复扫描进程列表。
    返回 (运行中, CPU%, PID)。由 is_game_idle() 每 5 秒更新缓存。
    """
    return (_game_running_cache, _game_cpu_cache, _game_pid_cache)

# ── 前台窗口 ──────────────────────────────────────────
def _get_foreground_process_name() -> str | None:
    try:
        hwnd = user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, byref(pid))
        return psutil.Process(pid.value).name()
    except Exception:
        return None

def is_game_in_foreground(process_name: str = "DeltaForceClient.exe") -> bool:
    fg = _get_foreground_process_name()
    if fg is None: return False
    targets = [n.strip().lower() for n in process_name.split(",") if n.strip()]
    return fg.lower() in targets

# ── 冷却 ──────────────────────────────────────────────
def mark_clean_done():
    global _last_auto_clean_time
    _last_auto_clean_time = time.time()

def is_in_cooldown() -> bool:
    return (time.time() - _last_auto_clean_time) < AUTO_CLEAN_COOLDOWN_SEC

def mark_bg_trim_done():
    global _last_bg_trim_time
    _last_bg_trim_time = time.time()

def is_bg_trim_in_cooldown(cooldown_sec: int = 300) -> bool:
    return (time.time() - _last_bg_trim_time) < cooldown_sec

# ── 综合判断 ──────────────────────────────────────────
def is_safe_to_clean(process_name: str = "DeltaForceClient.exe") -> tuple[bool, str]:
    import logging
    log = logging.getLogger(__name__)

    if is_in_cooldown():
        remaining = int(AUTO_CLEAN_COOLDOWN_SEC - (time.time() - _last_auto_clean_time))
        return False, f"冷却中（剩余 {remaining}s）"

    mem = psutil.virtual_memory()
    avail_pct = (mem.available / mem.total) * 100
    try:
        from memory_cleaner import _get_standby_size_mb
        standby = _get_standby_size_mb(mem)
    except:
        standby = (mem.cached or 0) / 1048576
    tight = memory_is_tight()
    fg = is_game_in_foreground(process_name)
    gidle = is_game_idle(process_name) if fg else True

    log.info(
        f"[检测] 可用 {avail_pct:.1f}% | 备用 {standby:.0f}MB | "
        f"紧张={tight} | 前台={fg} | 空闲={gidle}"
    )

    if not tight:
        return False, f"内存充足（可用 {avail_pct:.1f}%，备用 {standby:.0f}MB）"
    if not fg:
        return True, "游戏不在前台，安全窗口"
    if gidle:
        game_cpu = 0
        try:
            global _game_proc_cache
            if _game_proc_cache: game_cpu = _game_proc_cache.cpu_percent()
        except: pass
        return True, f"游戏前台 CPU {game_cpu:.0f}%，安全窗口"
    return False, "游戏活跃中，暂不清理"

if __name__ == "__main__":
    print("安全时机检测器测试")
    print(f"  内存紧张: {memory_is_tight()}")
    print(f"  游戏前台: {is_game_in_foreground('DeltaForceClient.exe')}")
    print(f"  游戏空闲: {is_game_idle('DeltaForceClient.exe')}")
    safe, reason = is_safe_to_clean("DeltaForceClient.exe")
    print(f"  综合判断: safe={safe}, reason={reason}")
