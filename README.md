# FreeRAM

<div align="center">

**不只是清备用列表——给游戏锁定物理内存，压榨后台进程，探试释放冷页**

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-lightgrey.svg)]()
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## 简介

**FreeRAM 是一个 Windows 内存管理工具，在你打游戏时自动释放被后台进程占用的物理内存，同时保护游戏不被系统回收。** 跟市面上清"备用列表"改数字的工具不同——它直接操作内核的工作集策略，消除的是随机卡顿，不是改个好看的数字。

后台运行，托盘常驻，安全判断保证不在游戏激烈时动手。如果你打游戏时习惯挂着 Chrome、Discord、Steam、OBS——它就是为你做的。

---

## 这跟其他内存清理工具有什么区别？

市面上几乎所有的 Windows 内存清理工具做的都是同一件事：清空**备用列表（Standby List）**——把系统缓存的内存标记为"空闲"，让任务管理器里的数字好看。但备用列表本来就算在可用内存里，清了只是改标签，不增加实际可用量。Superfetch 缓存在你真正需要内存的瞬间，Windows 自己就会回收。

**FreeRAM 不一样。** 它是一个四层内存管理引擎：

| 层 | 做什么 | 为什么有用 |
|---|--------|-----------|
| 🛡️ **游戏工作集保留** | 命令内核"这个进程至少保留 N GB 物理页，不准踢" | 内存紧张时 Windows 不会抢游戏的页，帧率更稳 |
| ⚡ **激进后台修剪** | 遍历所有非游戏进程，按占用降序踢出工作集 | 后台 Chrome 吃 3GB？压到几百 MB，腾给游戏 |
| 🎯 **反馈式游戏修剪** | 试探性释放游戏冷页，监控 PageFaultCount 回退 | 加载界面/大厅时的残留页被安全释放 |
| 📉 **优先级降级** | 后台进程全局降为后台模式（CPU/I/O/内存） | Discord/Steam 更新不跟游戏抢资源 |

**简单说：旧工具改数字，FreeRAM 改分配策略。**

---

## 效果如何？

### 什么人用了有感觉

| 配置 | 效果 |
|------|------|
| 32GB 内存，只打游戏 | 几乎感觉不到。内存根本用不完。 |
| **16GB + 游戏吃 10GB + Chrome 几十个标签** | **能感觉到。** 后台被压，游戏页被保护，消除随机卡顿。 |
| 8GB + 3A 大作 | 没用。物理内存绝对值不够，再压也挤不出来。 |

### 消除的不是"全程帧率 +20"，而是不该发生的卡顿

- 加载新地图需要大块连续物理内存 → 从 Chrome 抢，不从游戏抢 → 不卡
- 游戏中切出去看攻略再切回来 → 后台进程已被压缩，游戏页没被踢 → 秒切
- 游戏在大厅/加载界面 CPU 低 → 安全释放冷页 → 进游戏后物理页更充裕
- Discord/Steam 后台更新 → 降级后不抢资源 → 不掉帧

**注意：切回 Chrome 时前几秒可能有轻微迟钝**——因为它的工作集被踢了，需要重新加载。打游戏时不切过去就无所谓。

---

## 使用

### 下载运行

1. 从 [Releases](https://github.com/Ynahaa/FreeRAM/releases) 下载 `FreeRAM.exe`
2. 双击运行，无需安装

首次运行会在 exe 同目录自动生成 `config.json`。

### 修改目标游戏

> 默认目标进程是 `DeltaForceClient-Win64-Shipping.exe`（从 WeGame 启动的《三角洲行动》）

点击主界面 **设置** 按钮修改进程名，多个用逗号分隔。获取进程名：

1. 启动游戏
2. 任务管理器 → 进程 → 找到游戏 → 右键「转到详细信息」
3. 光标所在即为进程名

关闭窗口即隐藏到系统托盘继续后台。右键托盘图标退出。

### 命令行

```bash
FreeRAM.exe --once          # 执行一次清理后退出（打印释放量）
FreeRAM.exe --info          # 显示当前内存状态
FreeRAM.exe --scan          # 扫描运行中的进程，帮你找游戏进程名
FreeRAM.exe --minimized     # 启动后直接最小化到托盘
FreeRAM.exe --process "MyGame.exe,Another.exe"  # 指定目标进程
```

---

## 特性

- 🛡️ **游戏工作集保留** — `SetProcessWorkingSetSize` 内核强制执行，物理页不被回收
- ⚡ **激进后台修剪** — 无上限、按工作集降序，大内存进程优先处理
- 🎯 **反馈式游戏修剪** — `EmptyWorkingSet` + `PageFaultCount` 监控，触碰热页自动冷却 2 分钟
- 📉 **全局优先级降级** — `PROCESS_MODE_BACKGROUND_BEGIN` 同时降 CPU/I/O/内存
- 🧠 **智能安全检查** — 冷却期 → 内存阈值 → 前台窗口 → CPU 空闲，四级判断
- 🔒 **系统进程保护** — 可执行文件路径判断 + 白名单，双重保险不碰系统关键进程
- 🖥️ **无边框 GUI** — PySide6，浅色/深色双主题，自定义标题栏
- 📌 **系统托盘** — 关闭即最小化，右键菜单快捷操作
- 🔔 **智能通知** — 自动清理结果、游戏状态变化弹窗提示（可关闭）
- 🧵 **IDLE 优先级** — 工具自身不跟游戏抢 CPU
- 🔒 **单实例** — Windows Mutex，崩溃安全，重复启动呼出已有窗口
- 📋 **清理历史** — 主界面最近 10 条记录

---

## 配置说明

`config.json` 完整参数：

### 目标

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `process_name` | `DeltaForceClient-Win64-Shipping.exe` | 目标游戏进程名，多个逗号分隔 |

### 自动清理触发

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `mem_available_threshold_pct` | `15.0` | 可用内存低于此百分比触发 |
| `standby_threshold_mb` | `1024` | 备用列表需大于此值（配合阈值判断） |
| `game_cpu_idle_threshold` | `40.0` | 游戏 CPU 低于此值视为空闲，可安全清理 |
| `auto_clean_cooldown_sec` | `120` | 两次自动清理最小间隔 |
| `poll_interval_sec` | `5` | 后台轮询间隔 |

### 后台进程管理

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `trim_background` | `true` | 总开关——关闭后 Tiers 1-4 全部停用 |
| `trim_bg_critical_only` | `true` | 仅内存 < 8% 时才触发后台修剪 |
| `trim_bg_min_working_mb` | `50` | 工作集低于此值的进程不处理 |
| `trim_bg_cooldown_sec` | `300` | 两次后台修剪最小间隔 |
| `trim_bg_exclude` | `[]` | 额外排除的进程名列表，如 `["obs64.exe"]` |

### 工作集策略（Tier 2）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `game_reserve_ws_mb` | `4096` | 游戏进程保留的最小工作集（内核强制） |
| `non_game_cap_ws_mb` | `1024` | 非游戏进程工作集上限 |

### 通知与界面

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `notify_cooldown_sec` | `600` | 通知弹窗最小间隔 |
| `notify_game_state` | `true` | 游戏状态变化时通知 |
| `start_minimized` | `false` | 启动后自动最小化 |
| `auto_start` | `false` | 开机自启（在 Startup 创建快捷方式） |
| `dark_mode` | `false` | 深色模式 |

---

## 原理

### 四层内存管理

```
┌──────────────────────────────────────────────────┐
│ Tier 4  优先级降级                                │
│ PROCESS_MODE_BACKGROUND_BEGIN                     │
│ 非游戏进程 → CPU / I/O / 内存优先级全面降级        │
├──────────────────────────────────────────────────┤
│ Tier 3  反馈式游戏修剪                            │
│ EmptyWorkingSet → 等 0.5s → 读 PageFaultCount     │
│ delta > 500 → 触碰热页，延长冷却 2 分钟            │
│ delta ≤ 500 → 安全，冷页被释放                    │
├──────────────────────────────────────────────────┤
│ Tier 2  工作集策略                                │
│ 游戏：SetProcessWorkingSetSize(min=4GB) 保留下限   │
│ 非游戏：当前 WS > 1GB → 设上限 1GB（每个 PID 只一次）│
├──────────────────────────────────────────────────┤
│ Tier 1  激进后台修剪                              │
│ 全系统扫描 → 按工作集降序 → 安全检查 → EmptyWorkingSet│
│ 无数量上限，无 CPU 闲置限制                       │
└──────────────────────────────────────────────────┘
```

### 安全检测流程

```
冷却期检查 → 可用内存阈值 → 备用列表阈值
     ↓
  前台窗口检测
     ├── 游戏不在前台 → ✅ 安全清理
     ├── 游戏在前台 + CPU < 40% → ✅ 安全清理
     └── 游戏在前台 + CPU > 40% → ❌ 跳过
```

### 系统进程保护（双重保险）

1. **进程名白名单** — 覆盖 Windows 10/11 已知系统进程，命中直接跳过
2. **路径判断** — `psutil.Process(pid).exe()` 读取可执行文件路径，在 `C:\Windows\` 下的一律放过；读不到路径的保守跳过

---

## 项目结构

```
FreeRAM/
├── main.py                # 入口：单实例、IDLE 优先级、日志、配置加载
├── memory_cleaner.py      # 核心引擎：四层内存管理 + 备用列表清理
├── safe_detector.py       # 安全判断：四级检测 + 冷却 + 共享缓存
├── gui.py                 # PySide6 界面：无边框、双主题、数据面板、设置
├── tray_icon.py           # 系统托盘：图标、菜单、通知、后台轮询
├── build.bat / 构建.bat    # PyInstaller 构建脚本
├── requirements.txt       # Python 依赖
├── FreeRAM.ico            # 应用图标
├── ANALYSIS.md            # 深度分析文档
└── RELEASE_CHECKLIST.md   # 发布评估
```

---

## 开发者

### 从源码运行

```bash
git clone https://github.com/Ynahaa/FreeRAM.git
cd FreeRAM
pip install -r requirements.txt
python main.py
```

### 依赖

- [PySide6](https://pypi.org/project/PySide6/) ≥ 6.5.0 — GUI
- [psutil](https://pypi.org/project/psutil/) ≥ 5.9.0 — 进程/内存信息
- [Pillow](https://pypi.org/project/Pillow/) ≥ 10.0.0 — 图标处理

### 构建 exe

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --icon=FreeRAM.ico --name FreeRAM main.py
```

或双击 `build.bat` / `构建.bat`，exe 输出到 `dist/` 目录。

---

## 常见问题

**Q: 会伤游戏性能吗？**
不会。工具以 IDLE 优先级运行，安全检测保证不在游戏活跃（前台 + CPU 高）时动手。Tier 3 反馈修剪有 page fault 监控和自动冷却。

**Q: 为什么切回 Chrome 有点慢？**
Chrome 的工作集被踢到备用列表了，切回来时需要重新加载物理页。这是释放内存的必然代价。如果你经常在游戏和浏览器之间来回切，可以把 Chrome 加入 `trim_bg_exclude`。

**Q: 开了之后可用内存数字没变？**
正常。清备用列表不会增加可用内存（备用页本来就计入可用）。真正释放的是物理页的分配权——你的游戏有更多的物理页可以用了，但任务管理器里的数字不一定显著变化。看帧率稳定性，别看数字。

**Q: 跟 ISLC / MemReduct / Process Lasso 比？**
ISLC 只清备用列表；MemReduct 只修剪工作集；Process Lasso 有优先级管理但无反馈式修剪。FreeRAM 是四个功能的组合，且反馈修剪（Tier 3）是独有的。

---

## 许可

MIT License

## 免责声明

本工具通过 Windows 官方 API 操作内存管理，不修改游戏文件、不注入进程、不绕过反作弊。被修剪的进程在下次访问内存时会有页面错误开销——这是物理内存管理的本质代价，不是 bug。Tier 3 的反馈机制能在触碰热页时自动冷却回退。建议首次使用时观察 10-15 分钟确认无异常。
