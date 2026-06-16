"""
safe_detector.py — 安全时机检测
===============================
三级判断 + 冷却机制：
  0. 冷却期 (两次清理 ≥ 2 分钟间隔)
  1. 阈值关 (可用内存 < 15% 且 备用列表 > 1GB)
  2. 窗口关 (前台不是游戏 → 安全；是游戏 + CPU 低 → 也安全)
"""

import time
import threading
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

# 多进程缓存 —— {原始进程名: 值}
_game_pid_cache: dict[str, int | None] = {}
_game_proc_cache: dict[str, object] = {}
_game_cpu_cache: dict[str, float] = {}
_game_running_cache: dict[str, bool] = {}
_prev_game_running: dict[str, bool] = {}  # 上一轮状态，用于检测变化
_pending_changes: list[tuple[str, bool]] = []  # 待消费的状态变化 (name, is_running)
_pending_lock = threading.Lock()
_pid_check_counter = 0


def force_rescan():
    """强制下一轮 is_game_idle 立即重扫进程列表（保存设置后调用）。"""
    global _pid_check_counter
    _pid_check_counter = 5


def is_game_idle(process_name: str = "DeltaForceClient.exe") -> bool:
    global _game_pid_cache, _game_proc_cache, _game_cpu_cache
    global _game_running_cache, _prev_game_running, _pending_changes, _pid_check_counter
    _pid_check_counter += 1

    targets = [n.strip().lower() for n in process_name.split(",") if n.strip()]
    target_set = set(targets)

    if _pid_check_counter >= 5 or not _game_running_cache:
        _pid_check_counter = 0
        # 保存旧状态用于检测变化
        _prev_game_running = dict(_game_running_cache)
        # 重置缓存
        _game_pid_cache.clear()
        _game_proc_cache.clear()
        _game_cpu_cache.clear()
        _game_running_cache.clear()

        # 扫描所有进程，匹配全部目标
        for proc in psutil.process_iter(["name", "pid"]):
            try:
                nl = proc.info["name"].lower()
                if nl in target_set and nl not in _game_pid_cache:
                    _game_pid_cache[nl] = proc.info["pid"]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # 更新每个目标的运行状态和 CPU（必须在变化检测之前）
        for name in targets:
            pid = _game_pid_cache.get(name)
            if pid is None:
                _game_running_cache[name] = False
                _game_cpu_cache[name] = 0.0
                continue
            try:
                proc = _game_proc_cache.get(name)
                if proc is None or proc.pid != pid:
                    proc = psutil.Process(pid)
                    _game_proc_cache[name] = proc
                if proc.is_running():
                    try:
                        cpu = proc.cpu_percent()
                    except Exception:
                        cpu = 0.0
                    _game_cpu_cache[name] = cpu
                    _game_running_cache[name] = True
                else:
                    _game_pid_cache[name] = None
                    _game_running_cache[name] = False
                    _game_cpu_cache[name] = 0.0
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                _game_pid_cache[name] = None
                _game_running_cache[name] = False
                _game_cpu_cache[name] = 0.0

        # 检测状态变化（状态更新后执行，加锁 + 去重）
        with _pending_lock:
            for name in targets:
                was = _prev_game_running.get(name, False)
                now = _game_running_cache.get(name, False)
                if was != now:
                    item = (name, now)
                    if item not in _pending_changes:
                        _pending_changes.append(item)

            for old_name in _prev_game_running:
                if old_name not in target_set and _prev_game_running[old_name]:
                    item = (old_name, False)
                    if item not in _pending_changes:
                        _pending_changes.append(item)

    # ── 非重扫轮次：仅更新 CPU 和空闲判断 ──
    all_idle = True
    for name in targets:
        pid = _game_pid_cache.get(name)
        if pid is None:
            continue
        try:
            proc = _game_proc_cache.get(name)
            if proc is not None and proc.is_running():
                try:
                    cpu = proc.cpu_percent()
                except Exception:
                    cpu = 0.0
                _game_cpu_cache[name] = cpu
                if cpu >= GAME_CPU_IDLE_THRESHOLD:
                    all_idle = False
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return all_idle


def get_cached_game_state() -> tuple[bool, float, dict, list]:
    """
    返回缓存的游戏状态——供 GUI DataCollector 复用。
    返回 (any_running, max_cpu, {name: {running, cpu, pid}}, changed_list)
    changed_list = [(name, is_running), ...]，消费后清空。
    """
    global _pending_changes
    states = {}
    any_running = False
    max_cpu = 0.0
    for name, pid in _game_pid_cache.items():
        running = _game_running_cache.get(name, False)
        cpu = _game_cpu_cache.get(name, 0.0)
        states[name] = {"running": running, "cpu": cpu, "pid": pid}
        if running:
            any_running = True
            if cpu > max_cpu:
                max_cpu = cpu

    with _pending_lock:
        changes = list(_pending_changes)
        _pending_changes.clear()
    return (any_running, max_cpu, states, changes)

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
    # 始终调用 is_game_idle 以更新共享缓存（供 GUI 显示和 Tier 3 使用）
    gidle = is_game_idle(process_name)

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
