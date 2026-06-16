# FreeRAM

<div align="center">

**Windows 智能内存清理工具 · 专为游戏玩家设计**

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-lightgrey.svg)]()
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## 简介

FreeRAM 是一个轻量级 Windows 内存清理工具，在**不干扰前台游戏**的前提下自动释放内存。它会：

- 🧹 清空系统**备用列表（Standby List）**——被缓存占用但实际空闲的内存
- ✂️ 修剪目标进程**工作集（Working Set）**——游戏退出后残留的物理内存占用
- 🛡️ **安全检测**——只在游戏不在前台或 CPU 空闲时才清理，避免卡顿
- 📊 后台进程修剪——自动压缩非关键后台进程的内存

## 特性

- ⚙️ **双轨清理** — 系统备用列表 + 进程工作集修剪
- 🛡️ **三级安全判断** — 冷却期 → 内存阈值 → 前台窗口检测，绝不跟游戏抢资源
- 🖥️ **无边框现代化界面** — PySide6 打造，浅色/深色双主题
- 📌 **系统托盘运行** — 关闭窗口即最小化到托盘，右键菜单快捷操作
- 🔔 **智能通知** — 游戏状态变化、自动清理结果弹窗提示
- 🧵 **IDLE 优先级** — 进程只在系统完全空闲时占用 CPU
- 🔒 **单实例检测** — 重复启动自动呼出已有窗口
- 🎮 **多游戏支持** — 逗号分隔多个进程名，同时监控
- 📋 **清理历史** — 主界面显示最近清理记录（时间、释放量、触发原因）


## 使用

1. 从 [Releases](https://github.com/Ynahaa/FreeRAM/releases) 下载 `FreeRAM.exe`
2. 双击运行即可，无需安装任何东西

首次运行会在 exe 同目录自动生成 `config.json`。默认目标进程是《三角洲行动》，如需修改：

- 点击主界面 **设置** 按钮（齿轮图标），修改目标进程名
- 或者用 `--scan` 扫描你正在运行的游戏进程

关闭窗口即隐藏到系统托盘继续后台监控，右键托盘图标可退出。


## 配置说明

`config.json` 中的主要参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `process_name` | `DeltaForceClient-Win64-Shipping.exe` | 目标游戏进程名，多个用逗号分隔 |
| `mem_available_threshold_pct` | `15.0` | 可用内存低于此百分比触发自动清理 |
| `standby_threshold_mb` | `1024` | 备用列表大于此值才清理 |
| `game_cpu_idle_threshold` | `40.0` | 游戏 CPU 低于此百分比视为空闲，可安全清理 |
| `auto_clean_cooldown_sec` | `120` | 两次自动清理最小间隔（秒） |
| `notify_cooldown_sec` | `600` | 通知弹窗最小间隔（秒） |
| `trim_background` | `true` | 是否修剪后台进程工作集 |
| `trim_bg_critical_only` | `true` | 仅修剪非关键进程 |
| `trim_bg_max_count` | `20` | 每次最多修剪的后台进程数 |
| `start_minimized` | `false` | 启动后自动最小化到托盘 |
| `auto_start` | `false` | 开机自启 |
| `dark_mode` | `false` | 深色模式 |

## 项目结构

```
FreeRAM/
├── main.py             # 入口：单实例、优先级、日志、配置加载
├── memory_cleaner.py   # 核心：备用列表清理 + 进程工作集修剪
├── safe_detector.py    # 安全判断：三级检测 + 冷却机制
├── gui.py              # PySide6 界面：无边框窗口、主题、数据面板
├── tray_icon.py        # 系统托盘：图标、菜单、通知
├── build.bat / 构建.bat # PyInstaller 构建脚本
├── requirements.txt    # Python 依赖
├── FreeRAM.ico         # 应用图标
└── REVIEW.md           # 待开发功能
```

## 原理

### 双轨清理

| 轨道 | 目标 | 方法 |
|------|------|------|
| **A. 系统备用列表** | Windows 超级缓存（Superfetch）占用的 Standby 内存 | `NtQuerySystemInformation` + `EmptyWorkingSet` |
| **B. 进程工作集** | 目标游戏进程的物理内存占用 | `SetProcessWorkingSetSize` / `EmptyWorkingSet` |

### 安全检测流程

```
冷却期检查 → 内存阈值判断 → 前台窗口检测
                                  ↓
                         游戏不在前台 → 安全清理
                         游戏在前台 + CPU 低 → 也安全
                         游戏在前台 + CPU 高 → 跳过
```


## 开发者介绍

### 从源码运行

```bash
git clone https://github.com/Ynahaa/FreeRAM.git
cd FreeRAM
pip install -r requirements.txt
python main.py
```

### 依赖

- [PySide6](https://pypi.org/project/PySide6/) ≥ 6.5.0
- [psutil](https://pypi.org/project/psutil/) ≥ 5.9.0
- [Pillow](https://pypi.org/project/Pillow/) ≥ 10.0.0

### 构建 exe

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --icon=FreeRAM.ico --name FreeRAM main.py
```

或双击运行 `构建.bat` / `build.bat`，exe 输出到 `dist/` 目录。

## 许可

MIT License

## 免责声明

本工具通过 Windows 官方 API 操作内存管理，不会修改游戏文件或注入进程。但请理解内存清理的本质是让 OS 重新按需分配物理页——被修剪的进程在下次访问内存时会有微小的页错误开销。建议在游戏加载/大厅阶段或退出游戏后使用。
