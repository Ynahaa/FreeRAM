# FreeRAM 深度分析

---

## 一、它能不能真的清理内存？

**技术上：能。** 调用了有效的 Windows 内核 API：

| 轨道 | API | 实际做的事 |
|------|-----|-----------|
| 备用列表 (Standby List) | `NtSetSystemInformation(SystemMemoryListInformation)` | 命令内存管理器把 Standby 页移到 Free 列表 |
| 进程工作集 (Working Set) | `EmptyWorkingSet(h)` | 把目标进程的物理页踢出工作集 |
| 内存压力 | `VirtualAlloc` + `VirtualFree` | 申请 256MB 迫使 Windows 压缩所有进程工作集 |
| 字节数组遍历 | `bytearray(200MB)` | 同上，python 侧模拟内存压力 |

---

## 二、清理效果显著吗？

**不显著。** 原因：

### 1. 清备用列表是数字游戏

备用列表（Standby List）在 Windows 内存模型里**本来就计入可用内存**。清除它只是把标签从"备用"变成"空闲"，`MemAvailable` 数字不变。

```
清理前: 已用 14GB / 备用 2GB / 空闲 0.5GB  →  可用 = 2.5GB
清理后: 已用 14GB / 备用 0GB / 空闲 2.5GB  →  可用 = 2.5GB  （没变！）
```

任何一个进程申请内存时，Windows 会**自动、零延迟**从备用列表取页——不需要你清。

### 2. 游戏活跃时从不清理

`safe_detector.py` 的三级判断保证了**不在游戏前台且活跃时动手**：

```
冷却期检查 → 内存阈值 → 前台窗口检测
                              ↓
                     游戏不在前台 → 安全清理
                     游戏在前台 + CPU 低 → 也安全
                     游戏在前台 + CPU 高 → 跳过  ← 多数时间在这
```

这意味着游戏过程中，工具在睡觉。退出游戏后才干活——而此时 Windows 自己已经在回收了。

### 3. 修剪工作集的代价

`EmptyWorkingSet` 踢出去的页变成硬页错误 (hard page fault) 的候选——下一次访问时要重建映射。设计者清楚这一点，所以加了安全检测。但这也捆绑了工具的手脚：效果和安全是矛盾的。

---

## 三、唯一实际有用的场景

> 玩了 2 小时游戏 → 退出 → 想立刻开另一个大应用（视频剪辑 / 3D 渲染）
> 此时游戏残留工作集被 `EmptyWorkingSet` 立刻压下去，不用等 Windows 慢慢回收。

这个场景存在，但对大多数用户来说，退出游戏后 10 秒内 Windows 已经回收完了。

---

## 四、代码评价

### 好的

- **架构清晰。** `main.py`（入口/单实例/日志）、`memory_cleaner.py`（核心逻辑）、`safe_detector.py`（判断）、`gui.py` + `tray_icon.py`（UI）——模块边界干净
- **单实例用 Windows Mutex 而非 PID 文件。** 进程崩溃时 OS 自动回收 Mutex，不会残留锁
- **`IDLE_PRIORITY_CLASS`。** 工具自身不会跟游戏抢 CPU
- **三级安全判断设计合理。** 冷却→阈值→前台窗口，逻辑可读。`MEM_CRITICAL_THRESHOLD_PCT = 8%` 的无条件触发是好的边界
- **双主题 QSS。** 浅色/深色一套分支搞定，没有 QPalette 地狱
- **`RotatingFileHandler`。** 日志不会无限膨胀

### 不好的

- **`safe_detector.py` 和 `gui.py` 的 `DataCollector` 各自维护了一套游戏进程扫描缓存**——完全重复，同一个进程每 5 秒被扫两次
- **`_get_standby_size_mb` 的 PowerShell 回退**有 1-3 秒阻塞延迟，在主循环（每 5 秒）里可能卡住
- **`_pressure_flush` 在清理内存之前先申请 200MB**——内存紧张时火上浇油
- **`trim_background_processes` 上限 20 个进程、CPU < 3%、存活 > 60 秒**——过于保守，真正占内存的后台进程可能被跳过
- **零测试。** 没有 `tests/` 目录。核心函数行为依赖生产环境 Windows API，没有 mock
- **`BACKGROUND_EXEMPT` 硬编码白名单**——Windows 11 更新引入新系统进程名后会过时。且白名单用进程名字符串而非 PID 判断
- **`REVIEW.md` 列的暗色模式"待开发"但代码里已经实现了**——文档与代码脱节

---

## 五、如果要做到游戏中真正有效，怎么改？

### Tier 1：激进修剪后台进程（安全，立竿见影，改 3 行）

**原理：** 把非游戏、非系统关键的后台进程全部 `EmptyWorkingSet`，释放的物理页立即可以被游戏使用。

**当前问题：**

```python
# memory_cleaner.py:trim_background_processes
if count >= max_count:       # ← 20 个就停
    break
if cpu >= 3.0:               # ← 有 CPU 但大工作集的进程被跳过
    continue
```

**改进：**

- 去掉 20 个上限
- 去掉 CPU < 3% 限制（一个进程可以有 5% CPU 但 2GB 工作集，照样该压）
- 用 `NtQuerySystemInformation(SystemProcessInformation)` 批量获取进程信息，比 `psutil.process_iter` 快一个数量级

**预期效果：** 多释放 2-5GB（取决于后台进程数量）。

---

### Tier 2：游戏进程工作集保留（内核强制执行，加 ~30 行）

**原理：** `SetProcessWorkingSetSize` 可以设**最小值**——告诉 Windows"这个进程至少保留 N GB 物理内存，全局压力下也别动它"。

```python
# 给游戏保留 8GB 工作集
min_ws = 8 * 1024 * 1024 * 1024
max_ws = 16 * 1024 * 1024 * 1024
k32.SetProcessWorkingSetSize(game_handle, min_ws, max_ws)

# 给非游戏进程设上限
k32.SetProcessWorkingSetSize(chrome_handle, 128*1024*1024, 512*1024*1024)
```

这是**内核强制执行**的硬限制——不是建议。当系统内存紧张时，Windows 会先动那些没被"保留"的进程。

当前代码完全没有用这个能力——它只用 `EmptyWorkingSet`（一次性踢出），没有利用工作集的上下限语义。

**预期效果：** 游戏在内存压力下不掉帧（因为物理页被强制保留）。

---

### Tier 3：冷热页分析 + 反馈修剪（高阶，加 ~200 行）

**原理：** 只踢游戏进程中"冷"的页，保留"热"的页。

**技术手段：**

1. `VirtualUnlock`（Windows 8+）—— 解锁虚拟地址范围，内核可回收物理页，但进程再次访问时**无声重新提交**，不产生访问违例。比 `EmptyWorkingSet` 温和

2. `OfferVirtualMemory`（Windows 8.1+）—— 主动告诉内核"这页我不要了，回收吧"

3. 反馈循环 —— 修剪后监控 `PageFaultCount`（来自 `GetProcessMemoryInfo`），如果 page fault 速率暴增 → 说明动了热页，回滚

```python
# 伪代码
cold_pages = identify_cold_pages(game_handle)  # 最难的部分
k32.OfferVirtualMemory(cold_region, size, OFFER_PRIORITY_NORMAL)

time.sleep(0.5)
new_faults = get_page_fault_count(game_handle)
if new_faults - old_faults > threshold:
    rollback()  # 动了热页，恢复
```

**难点：** 从用户态准确识别冷页。可行近似方案：
- 用 ETW (Event Tracing for Windows) 的 `Microsoft-Windows-Kernel-Memory` provider 监听页错误事件
- 或者分批次试探性释放 + 监控反馈

**预期效果：** 游戏中也能安全释放数百 MB ~ 1GB（取决于冷页比例）。

---

### Tier 4：I/O + CPU 优先级（不释放内存但减少争抢）

**原理：** 降级非关键进程的全局优先级。

```python
# Win8+ PROCESS_MODE_BACKGROUND_BEGIN 同时降低 CPU、I/O、内存优先级
k32.SetPriorityClass(other_handle, PROCESS_MODE_BACKGROUND_BEGIN)
```

当前代码只给自己设了 `IDLE_PRIORITY_CLASS`，没有动其他进程。

---

## 六、理论天花板

### 不可能的事

1. **8GB 物理内存跑出 16GB 的效果。** 游戏工作集 > 物理内存时，任何用户态工具都救不了。唯一方案是压缩（Win10+ 已自动做）或用 SSD swap——但这会降速
2. **释放内存而不产生任何 page fault 代价。** 物理约束：踢出去的页下次访问必须重建映射。只能选择"踢哪些页"让代价发生在不关键的时机
3. **100% 准确判断冷热页而不借助内核驱动。** 要精确追踪需要 WDM 驱动（hook `MmAccessFault`）或 Intel PT (Processor Trace)。用户态只能做到 70-80% 的近似

---

## 七、改进优先级排序

| 优先级 | 改动 | 预期效果 | 复杂度 | 风险 |
|--------|------|----------|--------|------|
| 🔴 立即做 | 去掉后台进程修剪的上限和 CPU 限制 | 多释放 2-5GB | 改 3 行 | 极低 |
| 🟡 应该做 | `SetProcessWorkingSetSize` 给游戏保留下限 | 内存压力下不掉帧 | 加 ~30 行 | 低 |
| 🟢 值得做 | `OfferVirtualMemory` + 页错误监控反馈循环 | 游戏中安全释放冷页 | 加 ~200 行，需仔细测试 | 中 |
| ⚪ 天花板 | 写内核驱动做精确冷热页追踪 | 理论上最优 | 换语言，签名驱动 | 高 |

---

## 八、总结

FreeRAM 是一个**写的挺认真的 PySide6 练手项目**——架构清晰、托盘+无边框窗口、双主题、配置系统、单实例检测、日志滚动。代码本身的工程素养比"清理内存"这件事更值得看。

但作为内存清理工具，它的实际效果受限于两个事实：
1. 清备用列表是改标签，不增加可用内存
2. 安全检测保证了它不在游戏活跃时动手——而这是用户最需要效果的时机

如果真要做出"游戏过程中效果显著"的工具，方向不是打磨现有逻辑，而是**切换轨道**：从"等游戏不活跃了再清理"变成"给游戏保留物理内存 + 压榨所有非游戏进程 + 冷页分析"。上述 Tier 1-3 可以在不改架构的前提下落地，因为当前代码的模块边界是干净的——`memory_cleaner.py` 替换核心逻辑，上层 GUI/托盘不动。
