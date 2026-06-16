# FreeRAM 待添加功能

---

## ⭐⭐⭐ 高优先

### 1. 完整设置面板

当前 15 个配置项只暴露了 5 个（进程名、内存阈值、备用列表阈值、启停通知、后台修剪开关）。加一个"高级"折叠区，把其余 10 个参数放进去：

`game_cpu_idle_threshold`、`auto_clean_cooldown_sec`、`notify_cooldown_sec`、`poll_interval_sec`、`trim_bg_critical_only`、`trim_bg_min_working_mb`、`trim_bg_max_count`、`trim_bg_cooldown_sec`、`trim_bg_exclude`、`start_minimized`

---

## ⭐⭐ 中优先

### 2. 清理历史面板

主窗口底部或侧边加一个可展开的日志区，显示最近 10 条清理记录：

- 时间、释放量、触发原因（自动/手动/内存告急）
- 可选：用 QPainter 画一个简易内存使用率折线（最近 5 分钟，60 个采样点），不引入 matplotlib

---

### 3. 暗色模式

加一个切换开关。PySide6 的 Fusion 风格支持 `QPalette` 暗色，改动不大——主要在 STYLE 字符串里切换颜色变量。

---

## ⭐ 低优先

### 4. 托盘菜单显示最近释放

右键托盘图标的第一行显示"上次释放 120 MB（2 分钟前）"，菜单重建时动态更新。不用打开窗口就能看到状态。
