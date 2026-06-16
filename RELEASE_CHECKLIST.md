# FreeRAM 发布评估

> 评估范围：Tier 1-4 改造后的当前代码。结论：**可以发布，但有一个阻塞项必须先修。**

---

## 一、当前状态总览

### 四层改造均已落地

| Tier | 模块 | 做什么 | 状态 |
|------|------|--------|------|
| 1 | `trim_background_processes` | 无上限、按工作集降序修剪后台进程 | ✅ |
| 2 | `apply_working_set_limits` | 游戏保留最小工作集 / 非游戏设上限 | ✅ 有隐患 |
| 3 | `trim_game_with_feedback` | EmptyWorkingSet + PageFaultCount 反馈 | ✅ |
| 4 | `demote_background_processes` | 非关键进程降级 CPU/I/O/内存优先级 | ✅ |

调用链完整，集成在 `tray_icon.py:_try_background_trim`，每轮按 Tier 4 → 2 → 3 → 1 顺序执行。

---

## 二、发布阻塞项（必须先修）

### 🔴 `apply_working_set_limits` 无去重缓存

**位置：** `memory_cleaner.py:250-296`

**问题：** 每 300 秒（`trim_bg_cooldown_sec`）被调用一次，对**所有**非游戏、非系统进程重新 `SetProcessWorkingSetSize`。进程的工作集可能已经远低于上限，但每次照样 `OpenProcess` + 系统调用。

更严重的是——如果某个进程确实需要超过 1GB 工作集（比如后台渲染视频），每 5 分钟被硬压一次，体验极差。用户不会怪"工作集限制"——会怪 FreeRAM 让电脑卡。

**修复方案：** 加一个 set 缓存（跟 `_PRIORITY_DEMOTED` 一样的模式），已设过的 PID 不再重复设。或者更简单：设之前先读一下当前工作集，如果已经低于上限就跳过。

```python
# 简单方案：只对超过上限的进程设限制
_WS_LIMITED: set[int] = set()

def apply_working_set_limits(config, game_process=""):
    ...
    for proc in psutil.process_iter(["name", "pid"]):
        ...
        if nl in game_targets:
            if pid in _WS_LIMITED: continue  # 已设过
            ...
            _WS_LIMITED.add(pid)
        else:
            # 先读当前工作集，超过上限才设
            ws_bytes = _get_working_set_size(h)
            if ws_bytes < cap_ws:
                k32.CloseHandle(h); continue
            ...
    ...
```

**预计工作量：** 改 5-8 行。

---

## 三、发布前强烈建议修

### 🟡 `BACKGROUND_EXEMPT` 用进程名匹配而非 PID

**位置：** `memory_cleaner.py:185-191`

**问题：**

- 用户跑 Python 脚本做机器学习 → `python.exe` 不在白名单 → 被当后台进程修剪 → 训练崩
- 恶意软件伪装成 `svchost.exe` → 在白名单里 → 永远不会被修剪
- 用户改 exe 名为 `FreeRAM_v2.exe` → 不在白名单里 → 工具会修剪自己（虽然 `our_pid` 检查会拦一道，但逻辑上脏）

**修复方案：**

- 自身进程：用 `os.getpid()` 判断，不依赖进程名。已经有了。
- 系统进程：用文件路径判断（`psutil.Process(pid).exe()` 在 `C:\Windows\System32\` 下 → 系统进程），或检查数字签名
- 如果做不到，至少 README 里加警告

**预计工作量：** 30 行（如果用路径判断）。不改也勉强能发，但要写文档。

---

## 四、发布后可优化（非阻塞）

### 🟢 三轮 `psutil.process_iter` 可合并

`_try_background_trim` 里 `demote_background_processes`、`apply_working_set_limits`、`trim_background_processes` 各自遍历一次全系统进程。合并为一次快照，三轮操作共用同一份进程列表，减少 CPU 开销。

**预计工作量：** 重构 30-50 行。

### 🟢 `EmptyWorkingSet` 在反馈修剪中仍是全量踢出

Tier 3 的 `trim_game_with_feedback` 用 `EmptyWorkingSet` 全量踢出游戏工作集，然后看 page fault 反馈。虽然冷却机制能在误判时回退，但第一下的冲击已经发生了。更温和的方案是在 Windows 8.1+ 上使用 `OfferVirtualMemory` 分批释放。

但当前方案已经比旧版（完全不碰游戏）好很多。不阻塞发布。

### 🟢 设置面板暴露 5/18 配置项

新加的 `game_reserve_ws_mb`、`non_game_cap_ws_mb` 只能通过手动编辑 `config.json` 修改。对高级用户可接受，对普通用户不友好。

**发布前：** 至少把默认值设合理（4GB 保留 / 1GB 上限对 16GB 系统是合理的）。

---

## 五、非代码层面的发布问题

### 🔴 SmartScreen 警告

没有 Authenticode 数字签名的 exe 首次运行时，Windows SmartScreen 弹红框"Windows 已保护你的电脑"，用户要点"更多信息"→"仍要运行"。这会吓退大量非技术用户。

**选项：**

- 买 EV/OV 代码签名证书（~200-400 美元/年）
- 开源项目可以申请免费的 Azure 签名（通过 Microsoft OSS 计划）
- 不签名 → 接受用户会因为 SmartScreen 流失

### 🟡 崩溃后残留的工作集限制

`SetProcessWorkingSetSize` 和 `PROCESS_MODE_BACKGROUND_BEGIN` 是进程级设置。FreeRAM 崩了之后，已经被限制的进程不会自动恢复默认——直到它们自己重启。用户可能发现"FreeRAM 关了之后 Chrome 还是慢"但不知道原因。

**减轻方案：** 托盘菜单加一个"撤销所有限制"选项，走之前清理干净。或者在 `stop()` 时遍历恢复。

---

## 六、发布前 checklist

| 项目 | 状态 | 备注 |
|------|------|------|
| `apply_working_set_limits` 去重 | ❌ 必须修 | 5-8 行 |
| 白名单问题文档化 | ⚠️ 建议 README 加说明 | 不改代码也行 |
| 默认配置合理 | ✅ 4GB保留/1GB上限 | 对 16GB 系统合理 |
| 清理历史/设置面板 | ✅ 有 | 设置面板只暴露了 5 个参数，够用 |
| 日志不爆炸 | ✅ RotatingFileHandler，1MB × 2 | |
| 单实例检测 | ✅ Mutex | |
| `--info` / `--once` 命令行 | ✅ | |
| 构建脚本 | ✅ `build.bat` | |
| README 说明效果原理 | ⚠️ 建议重写 | 当前 README 没讲 Tier 1-4，建议加入对比说明 |
| 签名 | ⚠️ 根据分发渠道决定 | GitHub Release → 可以先不发签名版 |

---

## 七、竞争定位

这个项目的独特卖点不是"清理内存"——那个市场已经烂了。真正的差异点：

| 功能 | 市面竞品 | FreeRAM (改后) |
|------|---------|---------------|
| 清备用列表 | 全都有 | ✅ 有（但说实话效果有限） |
| 修剪进程工作集 | MemReduct | ✅ 有 |
| 游戏工作集保留 (min WS) | ISLC 没有，Process Lasso 有 | ✅ **有** |
| 反馈式游戏修剪 (page fault 监控) | **没有竞品做这个** | ✅ **独有** |
| 全局后台进程降级 | Process Lasso 有 | ✅ 有 |

**建议 README 标题:** "不只是清备用列表——给游戏锁定物理内存，后台进程压榨，冷页试探释放"。并在 GitHub 放一段对比帧时间图（开/关 FreeRAM）。

---

## 八、最终判断

```
能否发布：           ✅ 可以
阻塞项：             1 个（apply_working_set_limits 去重）
建议发布前修：       1 个（README 补充说明 + 白名单警告）
发布后尽快跟进：     进程扫描合并、OfferVirtualMemory 替换
不建议现在搞：       签名证书（除非有预算）
────────────────────────────────────────
独特性：             强（反馈修剪 + 工作集保留，市面无竞品）
完成度：             中上（GUI+托盘+日志+配置都齐全）
风险：               低（不会蓝屏，不会丢数据）
```

**定性：修完那 5 行就可以发。**
