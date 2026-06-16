"""
memory_cleaner.py — Windows 内存清理核心
===========================================
双轨清理：
  A. 清系统备用列表 (Standby List)
  B. 修剪目标进程工作集 (EmptyWorkingSet)
"""

import ctypes
from ctypes import wintypes, byref, sizeof, Structure, c_void_p, c_ulong, c_size_t
import logging
import os
import subprocess
import time
import psutil

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────
SystemMemoryListInformation = 0x50
SE_INC_WORKING_SET_NAME = "SeIncreaseWorkingSetPrivilege"
SE_PROF_SINGLE_PROCESS_NAME = "SeProfileSingleProcessPrivilege"
PROCESS_SET_QUOTA = 0x0100
PROCESS_SET_INFORMATION = 0x0200
MEM_COMMIT = 0x1000
MEM_RESET = 0x80000
PAGE_READWRITE = 0x04

# ── 结构体 ────────────────────────────────────────────
class LUID(Structure):
    _fields_ = [("LowPart", ctypes.c_ulong), ("HighPart", ctypes.c_long)]

class LUID_AND_ATTRIBUTES(Structure):
    _fields_ = [("Luid", LUID), ("Attributes", ctypes.c_ulong)]

class TOKEN_PRIVILEGES(Structure):
    _fields_ = [
        ("PrivilegeCount", ctypes.c_ulong),
        ("Privileges", LUID_AND_ATTRIBUTES * 1),
    ]


class PROCESS_MEMORY_COUNTERS(Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


PROCESS_QUERY_INFORMATION = 0x0400

# ── DLL 绑定 ──────────────────────────────────────────
k32 = ctypes.windll.kernel32
a32 = ctypes.windll.advapi32
papi = ctypes.windll.psapi
ntdll = ctypes.windll.ntdll

# ── 权限提升 ──────────────────────────────────────────
def _raise_privilege(name: str) -> bool:
    h_token = wintypes.HANDLE()
    if not k32.OpenProcessToken(k32.GetCurrentProcess(), 0x0020 | 0x0008, byref(h_token)):
        return False
    luid = LUID()
    if not a32.LookupPrivilegeValueW(None, name, byref(luid)):
        k32.CloseHandle(h_token)
        return False
    tp = TOKEN_PRIVILEGES()
    tp.PrivilegeCount = 1
    tp.Privileges[0].Luid = luid
    tp.Privileges[0].Attributes = 0x00000002
    ret = a32.AdjustTokenPrivileges(h_token, False, byref(tp), sizeof(tp), None, None)
    k32.CloseHandle(h_token)
    return ret != 0

# ── 内存信息 ──────────────────────────────────────────
_standby_cache_val = 0.0
_standby_cache_time = 0.0
_STANDBY_CACHE_TTL = 10.0  # PowerShell 查询结果缓存 10 秒

def get_memory_info() -> dict:
    mem = psutil.virtual_memory()
    standby = _get_standby_size_mb(mem)
    return {
        "total_mb":     round(mem.total / 1048576, 1),
        "available_mb": round(mem.available / 1048576, 1),
        "used_pct":     mem.percent,
        "standby_mb":   standby,
    }

def _get_standby_size_mb(mem=None) -> float:
    """获取备用列表大小。psutil 优先，失败则 PowerShell，结果缓存 10 秒。"""
    global _standby_cache_val, _standby_cache_time

    # 优先用 psutil（零开销）
    try:
        if mem is None:
            mem = psutil.virtual_memory()
        val = mem.cached
        if val and val > 0:
            return round(val / 1048576, 1)
    except Exception:
        pass

    # psutil 返回 0 → 检查缓存
    now = time.time()
    if _standby_cache_val > 0 and (now - _standby_cache_time) < _STANDBY_CACHE_TTL:
        return _standby_cache_val

    # 回退：PowerShell（缓存结果 10 秒）
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Counter '\\Memory\\Standby Cache Normal Priority Bytes').CounterSamples[0].CookedValue"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if result.returncode == 0 and result.stdout.strip():
            val = round(float(result.stdout.strip()) / 1048576, 1)
            _standby_cache_val = val
            _standby_cache_time = now
            return val
    except Exception:
        logger.debug("备用列表查询失败", exc_info=True)

    return 0.0

# ── 清理一：备用列表 ──────────────────────────────────
def clean_standby_list() -> bool:
    if _try_nt_clean():
        return True
    b_ok = _try_virtual_alloc_pressure(mb=256)
    if not b_ok:
        logger.debug("VirtualAlloc 失败，回退 bytearray")
        try:
            _pressure_flush(mb=200)
        except Exception:
            logger.debug("bytearray 也失败", exc_info=True)
    try:
        k32.SetProcessWorkingSetSize(k32.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1))
    except Exception:
        logger.debug("SetProcessWorkingSetSize 失败", exc_info=True)
    return True

def _try_nt_clean() -> bool:
    try:
        _raise_privilege(SE_PROF_SINGLE_PROCESS_NAME)
        _raise_privilege(SE_INC_WORKING_SET_NAME)
        ntdll.NtSetSystemInformation.restype = ctypes.c_long
        ntdll.NtSetSystemInformation.argtypes = [c_ulong, c_void_p, c_ulong]
        status = ntdll.NtSetSystemInformation(SystemMemoryListInformation, None, 0)
        if status < 0:
            logger.debug(f"NtSetSystemInformation 返回 {status:#x}")
            return False
        logger.debug("NtSetSystemInformation 成功")
        return True
    except Exception:
        logger.debug("NtSetSystemInformation 调用失败", exc_info=True)
        return False

def _try_virtual_alloc_pressure(mb: int = 256) -> bool:
    try:
        size = mb * 1024 * 1024
        addr = k32.VirtualAlloc(None, size, MEM_COMMIT | MEM_RESET, PAGE_READWRITE)
        if addr:
            k32.VirtualFree(addr, 0, 0x8000)
            return True
        return False
    except Exception:
        return False

def _pressure_flush(mb: int = 200):
    size = mb * 1024 * 1024
    buf = bytearray(size)
    for offset in range(0, size, 4096 * 16):
        buf[offset] = 0
    del buf

# ── 清理二：修剪进程 ──────────────────────────────────
def trim_process_workset(process_name: str) -> int:
    targets = {n.strip().lower() for n in process_name.split(",") if n.strip()}
    trimmed = 0
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            if proc.info["name"].lower() in targets:
                h = k32.OpenProcess(PROCESS_SET_QUOTA, False, proc.info["pid"])
                if h:
                    papi.EmptyWorkingSet(h)
                    k32.CloseHandle(h)
                    trimmed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return trimmed

# ── 清理三：后台进程 ──────────────────────────────────

# 系统关键进程 + 本工具自身 —— 绝对不碰
BACKGROUND_EXEMPT = {
    "system", "system idle process", "registry", "csrss.exe", "winlogon.exe",
    "wininit.exe", "smss.exe", "services.exe", "lsass.exe", "svchost.exe",
    "dwm.exe", "explorer.exe", "audiodg.exe", "spoolsv.exe", "wmiprvse.exe",
    "searchindexer.exe", "searchprotocolhost.exe", "searchfilterhost.exe",
    "sihost.exe", "taskhostw.exe", "fontdrvhost.exe", "runtimebroker.exe",
    "shellexperiencehost.exe", "startmenuexperiencehost.exe", "textinputhost.exe",
    "freeram.exe", "python.exe", "pythonw.exe",
}


def _is_system_process(pid: int) -> bool:
    """通过可执行文件路径判断是否为 Windows 系统进程。
    覆盖 C:\\Windows\\System32、SysWOW64 等子目录，
    捕获白名单里没有的新系统进程名。"""
    try:
        exe_path = psutil.Process(pid).exe().lower()
        return exe_path.startswith("c:\\windows\\")
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return True  # 读不到路径 → 保守处理，当作系统进程不碰


# ── 清理四：优先级降级 ──────────────────────────────

PROCESS_MODE_BACKGROUND_BEGIN = 0x00100000  # Win8+

_PRIORITY_DEMOTED: set[int] = set()  # 已降级的 PID，避免重复操作


def demote_background_processes(config: dict, game_process: str = "") -> int:
    """
    对非游戏、非系统的后台进程降级为后台模式（CPU / I/O / 内存优先级降低）。
    返回降级成功的进程数。
    """
    global _PRIORITY_DEMOTED
    if not config.get("trim_background", False):
        return 0

    game_targets = {n.strip().lower() for n in game_process.split(",") if n.strip()}
    user_exclude = {n.lower() for n in config.get("trim_bg_exclude", [])}
    our_pid = os.getpid()
    count = 0

    for proc in psutil.process_iter(["name", "pid"]):
        try:
            name = proc.info["name"]
            pid = proc.info["pid"]
            nl = name.lower()

            if pid == our_pid: continue
            if pid in _PRIORITY_DEMOTED: continue
            if nl in BACKGROUND_EXEMPT: continue
            if _is_system_process(pid): continue
            if nl in user_exclude: continue
            if nl in game_targets: continue

            h = k32.OpenProcess(PROCESS_SET_INFORMATION, False, pid)
            if h:
                k32.SetPriorityClass(h, PROCESS_MODE_BACKGROUND_BEGIN)
                k32.CloseHandle(h)
                _PRIORITY_DEMOTED.add(pid)
                count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue

    return count


# ── 清理五：工作集保留/上限 ─────────────────────────

_WS_LIMITED: set[int] = set()  # 已设过工作集限制的 PID，避免每轮重复系统调用


def apply_working_set_limits(config: dict, game_process: str = "") -> int:
    """
    Tier 2：给游戏进程设最小工作集（内核强制执行，物理页被保留）；
    给非游戏大进程设最大工作集上限（仅当当前工作集超过上限时才设）。
    每个 PID 只设一次，避免每轮重复 OpenProcess + SetProcessWorkingSetSize。
    返回受影响的进程数。
    """
    global _WS_LIMITED
    if not config.get("trim_background", False):
        return 0

    game_reserve_mb = config.get("game_reserve_ws_mb", 4096)
    non_game_cap_mb = config.get("non_game_cap_ws_mb", 1024)
    game_targets = {n.strip().lower() for n in game_process.split(",") if n.strip()}
    user_exclude = {n.lower() for n in config.get("trim_bg_exclude", [])}
    our_pid = os.getpid()
    count = 0

    for proc in psutil.process_iter(["name", "pid"]):
        try:
            name = proc.info["name"]
            pid = proc.info["pid"]
            nl = name.lower()

            if pid == our_pid: continue
            if pid in _WS_LIMITED: continue       # 已设过限制，跳过
            if nl in BACKGROUND_EXEMPT: continue
            if _is_system_process(pid): continue
            if nl in user_exclude: continue

            h = k32.OpenProcess(PROCESS_SET_QUOTA, False, pid)
            if not h:
                continue

            if nl in game_targets:
                # 游戏进程：保留最小工作集
                min_ws = game_reserve_mb * 1024 * 1024
                max_ws = min_ws * 4  # 不实际限制，只保下限
                k32.SetProcessWorkingSetSize(h, ctypes.c_size_t(min_ws),
                                             ctypes.c_size_t(max_ws))
                _WS_LIMITED.add(pid)
                count += 1
            else:
                # 非游戏进程：只有当前工作集超过上限才设
                cap_ws = non_game_cap_mb * 1024 * 1024
                cur_ws = _get_working_set_size(h)
                if cur_ws > cap_ws:
                    k32.SetProcessWorkingSetSize(
                        h, ctypes.c_size_t(64 * 1024 * 1024),
                        ctypes.c_size_t(cap_ws))
                    _WS_LIMITED.add(pid)
                    count += 1

            k32.CloseHandle(h)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue

    return count


def trim_background_processes(config: dict, game_process: str = "") -> list:
    """
    遍历所有进程，按工作集大小降序对符合条件的后台进程执行 EmptyWorkingSet。
    无数量上限、无 CPU 闲置限制——大工作集进程无论 CPU 占用多少都该压。
    返回 [(name, pid, before_mb, after_mb), ...] 列表。
    """
    if not config.get("trim_background", False):
        return []

    min_working_mb = config.get("trim_bg_min_working_mb", 50)
    user_exclude = {n.lower() for n in config.get("trim_bg_exclude", [])}
    game_targets = {n.strip().lower() for n in game_process.split(",") if n.strip()}
    our_pid = os.getpid()

    try:
        fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
        fg_pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(fg_hwnd, ctypes.byref(fg_pid))
        fg_pid = fg_pid.value
    except Exception:
        fg_pid = 0

    now = time.time()

    # 第一遍：收集所有候选进程（仅安全检查，不限制数量和 CPU）
    candidates = []
    for proc in psutil.process_iter(["name", "pid", "memory_info", "create_time"]):
        try:
            name = proc.info["name"]
            pid = proc.info["pid"]
            nl = name.lower()

            # 安全检查
            if pid == our_pid: continue
            if nl in BACKGROUND_EXEMPT: continue
            if _is_system_process(pid): continue
            if nl in user_exclude: continue
            if nl in game_targets: continue
            if pid == fg_pid: continue

            # 物理内存检查
            ws = proc.info["memory_info"]
            if ws is None: continue
            wsmb = ws.WorkingSetSize / 1048576
            if wsmb < min_working_mb: continue

            # 存活时间检查（> 60 秒，防止修剪正在初始化的进程）
            ct = proc.info["create_time"]
            if ct is not None and (now - ct) < 60: continue

            candidates.append((name, pid, wsmb))
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue

    # 按工作集大小降序：优先处理最占内存的进程
    candidates.sort(key=lambda x: x[2], reverse=True)

    # 第二遍：执行修剪
    results = []
    for name, pid, before_ws in candidates:
        try:
            h = k32.OpenProcess(PROCESS_SET_QUOTA, False, pid)
            if h:
                papi.EmptyWorkingSet(h)
                k32.CloseHandle(h)
                try:
                    after_ws = psutil.Process(pid).memory_info().WorkingSetSize / 1048576
                except Exception:
                    after_ws = 0
                freed = round(before_ws - after_ws, 1)
                if freed > 0:
                    results.append((name, pid, before_ws, after_ws))
                    logger.info(f"[修剪后台] {name} (PID {pid}): {before_ws:.0f}MB → {after_ws:.0f}MB")
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue

    return results


# ── 清理六：反馈式游戏修剪 (Tier 3) ────────────────

_FAULT_THRESHOLD = 500      # 0.5 秒内 page fault 增量超过此值视为触碰热页
_FEEDBACK_COOLDOWN_SEC = 0.0  # 上次触碰热页的时间戳，用于动态延长冷却


def _get_page_fault_count(process_handle) -> int:
    """读取进程的累计 PageFaultCount。"""
    pmc = PROCESS_MEMORY_COUNTERS()
    pmc.cb = sizeof(pmc)
    if papi.GetProcessMemoryInfo(process_handle, byref(pmc), sizeof(pmc)):
        return pmc.PageFaultCount
    return -1


def _get_working_set_size(process_handle) -> int:
    """读取进程当前工作集大小（字节）。"""
    pmc = PROCESS_MEMORY_COUNTERS()
    pmc.cb = sizeof(pmc)
    if papi.GetProcessMemoryInfo(process_handle, byref(pmc), sizeof(pmc)):
        return pmc.WorkingSetSize
    return 0


def trim_game_with_feedback(game_pid: int) -> dict:
    """
    Tier 3：对游戏进程执行 EmptyWorkingSet，监控 PageFaultCount 副作用。
    触碰热页时标记 unsafe 并动态延长冷却。
    返回 {"freed_mb": float, "safe": bool, "fault_delta": int}
    """
    global _FEEDBACK_COOLDOWN_SEC

    h = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION,
                        False, game_pid)
    if not h:
        return {"freed_mb": 0, "safe": False, "fault_delta": 0}

    before_faults = _get_page_fault_count(h)
    before_ws = _get_working_set_size(h)

    papi.EmptyWorkingSet(h)
    time.sleep(0.5)  # 等待 page fault 积累

    after_faults = _get_page_fault_count(h)
    after_ws = _get_working_set_size(h)
    k32.CloseHandle(h)

    fault_delta = after_faults - before_faults if before_faults >= 0 else 0
    freed_mb = round((before_ws - after_ws) / 1048576, 1) if after_ws < before_ws else 0

    safe = fault_delta <= _FAULT_THRESHOLD
    if not safe:
        logger.warning(
            f"[反馈修剪] 游戏 PID {game_pid}: page fault 暴增 {fault_delta} "
            f"(释放 {freed_mb:.0f}MB) — 触碰热页，延长冷却")
        _FEEDBACK_COOLDOWN_SEC = time.time() + 120  # 额外冷却 2 分钟
    else:
        logger.info(
            f"[反馈修剪] 游戏 PID {game_pid}: 安全释放 {freed_mb:.0f}MB "
            f"(page fault +{fault_delta})")

    return {"freed_mb": max(0, freed_mb), "safe": safe, "fault_delta": fault_delta}


def is_feedback_cooldown_active() -> bool:
    """反馈修剪是否处于冷却期（触碰过热页后延长冷却）。"""
    return time.time() < _FEEDBACK_COOLDOWN_SEC


# ── 组合 ──────────────────────────────────────────────
def full_clean(process_name: str = "DeltaForceClient.exe") -> dict:
    before = get_memory_info()
    clean_standby_list()
    trimmed = trim_process_workset(process_name)
    after = get_memory_info()
    freed_mb = round(after["available_mb"] - before["available_mb"], 1) if after["available_mb"] > before["available_mb"] else 0
    return {
        "before_available_mb": before["available_mb"],
        "before_standby_mb":  before["standby_mb"],
        "after_available_mb": after["available_mb"],
        "after_standby_mb":   after["standby_mb"],
        "freed_mb_estimate":  freed_mb,
        "processes_trimmed":  trimmed,
    }

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--info":
        info = get_memory_info()
        print(f"总内存:     {info['total_mb']:>8.0f} MB")
        print(f"可用:       {info['available_mb']:>8.0f} MB")
        print(f"使用率:     {info['used_pct']:>7.1f}%")
        print(f"备用列表:   {info['standby_mb']:>8.0f} MB")
    else:
        print("执行完整清理...")
        result = full_clean("DeltaForceClient.exe")
        print(f"清理前可用: {result['before_available_mb']:.0f} MB")
        print(f"清理后可用: {result['after_available_mb']:.0f} MB")
        print(f"预计释放:   {result['freed_mb_estimate']:.0f} MB")
