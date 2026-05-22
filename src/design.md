# OTA 烧录脚本 — 设计文档

> 版本: v0.1 | 日期: 2026-05-22 | 基于需求文档 v0.2

---

## 1. 概述

将现有的 `ota_alone.py`（GUI）和 `all.py`（CLI）合并为统一脚本。核心原则：**升级逻辑与 UI 分离**，ota_core 提供细粒度 API，GUI/CLI 各自编排流程。

---

## 2. 架构总览

```
┌──────────────────────────────────────────┐
│              ota_tool.py                 │  入口：解析 --cli/--gui，分发到对应模块
└─────────────┬────────────────────────────┘
              │
     ┌────────┴────────┐
     │                 │
┌────▼──────┐   ┌──────▼──────┐
│ ota_gui.py│   │ ota_cli.py  │            UI 层：配置窗口 + 升级窗口 / 终端交互
└────┬───────┘   └──────┬──────┘
     │                  │
     └────────┬─────────┘
              │  调用细粒度 API
     ┌────────▼─────────┐
     │   ota_core.py    │                  逻辑层：SSH / 上传 / 命令执行 / 版本校验 / 升级流程
     └──────────────────┘
              │
     ┌────────▼─────────┐
     │ config/config.json│                  数据层：默认配置
     └──────────────────┘
```

**文件职责：**

| 文件 | 职责 |
|------|------|
| `ota_tool.py` | 入口，解析 `sys.argv`，路由到 GUI 或 CLI 模块 |
| `ota_core.py` | 细粒度 SSH 操作 + 四种升级流程函数（参数化回调） |
| `ota_gui.py` | tkinter GUI：配置窗口 + 主升级窗口 |
| `ota_cli.py` | 终端交互：参数解析 + 逐项确认 + 升级执行 |

---

## 3. 模块设计

### 3.1 ota_tool.py — 入口

```
python ota_tool.py              → 启动 GUI
python ota_tool.py --gui        → 启动 GUI
python ota_tool.py --cli [参数]  → 启动 CLI
```

**处理逻辑：**
1. 检查 `sys.argv`，如果有 `--cli` 则调用 `ota_cli.run(argv)`
2. 否则调用 `ota_gui.run()`
3. 不在此文件中包含任何业务逻辑

---

### 3.2 ota_core.py — 核心逻辑

#### 3.2.1 细粒度 API（公有函数）

| 函数签名 | 说明 |
|----------|------|
| `load_config(path="config/config.json") -> dict` | 读取 JSON 配置 |
| `save_config(config, path="config/config.json")` | 写回 JSON 配置 |
| `ssh_connect(host, user, pw, timeout=15) -> SSHClient` | 建立 SSH 连接 |
| `ssh_disconnect(ssh)` | 断开 SSH 连接 |
| `ssh_test_connection(host, user, pw) -> (bool, str)` | 测试 SSH 连通性，成功返回 (True,"")，失败返回 (False, 错误信息) |
| `upload_file(ssh, local_path, remote_path, progress_callback=None)` | 上传单个文件，progress_callback(filename, transferred, total) |
| `upload_zip(ssh, remote_dir, local_zip_path, progress_callback=None)` | 上传 zip 压缩包 |
| `extract_zip(ssh, remote_dir, zip_filename, timeout=60) -> (int, str)` | 远程解压 zip |
| `delete_files(ssh, remote_dir, filenames)` | 批量删除远程文件 |
| `execute_command(ssh, cmd, env_fix, timeout=300, verbose=True, log_callback=None, status_callback=None) -> (exit_code, output)` | 执行普通命令 |
| `execute_reboot_command(ssh, cmd, env_fix, log_callback=None) -> (exit_code, output)` | 执行重启命令（忽略断连异常） |
| `wait_reconnect(host, user, pw, wait_seconds=30, retries=5, log_callback=None) -> SSHClient` | 等待设备重启并重连 |
| `parse_versions(ssh, env_fix) -> dict` | 获取当前 MCU/UFS/Switch 版本 |
| `verify_versions(ssh, env_fix, expected, stage_name, log_callback=None) -> bool` | 打印版本校验表格，返回是否全部通过 |
| `build_env_fix(config) -> str` | 根据 config 构建环境变量前缀字符串 |

#### 3.2.2 升级流程函数（供 GUI/CLI 调用）

每个升级流程函数接受 **callbacks dict** 解耦 UI：

```python
Callbacks = {
    "log": Callable[[str, str], None],         # log(message, end='\n')
    "status": Callable[[str], None],           # status_update(text)
    "progress": Callable[[str, int, int], None] # progress(filename, transferred, total)
}
```

| 函数签名 | 说明 | 对应 ota_alone 中流程 |
|----------|------|----------------------|
| `run_full_upgrade(config, callbacks)` | 一键完整升级（两面） | `run_full_upgrade()` |
| `run_mcu_upgrade(config, callbacks)` | 单独升级 MCU（两面） | `run_mcu_upgrade()` |
| `run_switch_upgrade(config, callbacks)` | 单独升级 Switch（一面） | `run_switch_upgrade()` |
| `run_soc_upgrade(config, callbacks)` | 单独升级 SoC（两面） | `run_soc_upgrade()` |

**重要：升级流程严格遵循 ota_alone.py 原有逻辑，包括命令序列、超时、verbose 设置、错误处理方式。**

---

### 3.3 ota_gui.py — GUI 模块

#### 3.3.1 窗口流程

```
启动 → 配置窗口 → [用户填写/修改配置] → [可选: 测试连接] → 确定
                                                              ↓
                                                       主升级窗口
                                                       ├─ 选择升级类型
                                                       ├─ 选择固件来源 (文件/zip)
                                                       ├─ 选择固件文件
                                                       ├─ 点击升级按钮
                                                       └─ 日志/进度实时显示
```

#### 3.3.2 配置窗口（ConfigWindow）

独立于主窗口，使用 `tk.Toplevel` 或先创建配置窗口再创建主窗口。

| 区域 | 控件 | config.json 对应字段 |
|------|------|---------------------|
| 设备连接 | `host` 输入框, `user` 输入框, `pw` 密码框 | `device.host`, `device.user`, `device.pw` |
| 路径 | `bin_path`, `lib_paths`, `remote_ota_dir`, `local_image_dir` 输入框 | `paths.*` |
| 版本 | `mcu_ver`, `ufs_ver`, `switch_ver` 输入框 | `expected_versions.*` |
| 按钮 | "测试连接", "保存为默认配置", "确定" | — |
| 固件文件 | **不在配置窗口显示** | — |

- "测试连接"：调用 `ota_core.ssh_test_connection`，弹出"连接成功"/"连接失败: xxx"
- "保存为默认配置"：调用 `ota_core.save_config` 写回 JSON
- "确定"：关闭配置窗口，打开主升级窗口

#### 3.3.3 主升级窗口（UpgradeWindow）

| 区域 | 控件 | 说明 |
|------|------|------|
| 升级类型 | 4 个 RadioButton：`一键完整升级` / `单独升级 MCU` / `单独升级 Switch` / `单独升级 SoC` | 默认：一键完整升级 |
| 固件来源 | 2 个 RadioButton：`单个文件` / `ZIP 压缩包` | 切换时动态刷新文件选择区域 |
| 文件选择区 | **direct 模式**：根据升级类型显示 N 个 `[输入框 + 浏览按钮]` 行 | 输入框预填 JSON 默认值 |
| | **zip 模式**：1 个 `[输入框 + 浏览按钮]` + `extracted_files` 列表编辑器 | zip 输入框过滤 `.zip`；列表编辑器显示默认值，支持增删改行 |
| 操作按钮 | "开始升级" / "清空日志" | 开始升级 → 在后台线程执行 |
| 日志区 | ScrolledText，只读 | 实时输出 |
| 状态栏 | 单行 Label | 显示当前进度/状态 |
| 进度条 | 不确定模式 Progressbar | 任务进行中滚动 |

**升级类型与 direct 模式文件选择器对应关系：**

| 升级类型 | 文件选择器数量 | 默认文件名 |
|----------|:---:|------|
| 一键完整升级 | 4 | `MCU_GTMC_AY5_T1_Merge_Boot_App.hex`, `sail_ota.bin`, `bcm89572_evk_avb_switch_rev1.img`, `ufs_ota.bin` |
| 单独升级 MCU | 1 | `MCU_GTMC_AY5_T1_Merge_Boot_App.hex` |
| 单独升级 Switch | 1 | `bcm89572_evk_avb_switch_rev1.img` |
| 单独升级 SoC | 2 | `sail_ota.bin`, `ufs_ota.bin` |

**zip 模式 extracted_files 默认值：** 与 direct 模式对应升级类型的文件列表一致（来自 ota_alone.py），用户可增删改。例如一键完整升级的 extracted_files 默认为 4 个文件名。

---

### 3.4 ota_cli.py — CLI 模块

#### 3.4.1 参数设计

```
python ota_tool.py --cli [选项]

选项：
  --host HOST           设备 IP（默认: config.json 中值）
  --user USER           SSH 用户名
  --pw PW               SSH 密码
  --bin-path PATH       远程 bin 路径
  --lib-paths PATHS     远程 lib 路径
  --remote-dir DIR      远程 OTA 目录
  --local-dir DIR       本地固件目录
  --expected-mcu VER    预期 MCU 版本
  --expected-ufs VER    预期 UFS 版本
  --expected-switch VER 预期 Switch 版本
  --type TYPE           升级类型: full|mcu|switch|soc（默认: full）
  --source SOURCE       固件来源: direct|zip（默认: direct）
  --files F1 F2 ...     direct 模式固件文件路径列表
  --zip ZIP_PATH        zip 压缩包路径
  --extracted F1 F2 ...  zip 解压后固件文件名列表
  --test-connection     仅测试 SSH 连接后退出
```

所有选项可选，未提供的使用 config.json 默认值。

#### 3.4.2 执行流程

```
1. 解析命令行参数
2. 加载 config.json 作为默认值
3. 命令行参数覆盖默认值
4. 如果指定 --test-connection → 测试连接 → 打印结果 → 退出
5. 打印最终生效的配置供用户确认
6. 根据 --type 和 --source 执行对应升级流程
7. 实时打印日志和进度
```

---

## 4. 配置结构（config.json 扩展）

```json
{
    "device": {
        "host": "172.31.27.6",
        "user": "root",
        "pw": "root"
    },
    "paths": {
        "bin_path": "/mnt/bin",
        "lib_paths": "/mnt/lib64:/mnt/usr/lib64:/lib:/usr/lib",
        "remote_ota_dir": "/ota",
        "local_image_dir": "./images"
    },
    "expected_versions": {
        "mcu": "R510_RS02_01",
        "ufs": "R510_RS02_00",
        "switch": "R400_RC02_2T"
    },
    "firmware_files": {
        "full": {
            "mcu": "MCU_GTMC_AY5_T1_Merge_Boot_App.hex",
            "sail": "sail_ota.bin",
            "switch": "bcm89572_evk_avb_switch_rev1.img",
            "ufs": "ufs_ota.bin"
        },
        "mcu": "MCU_GTMC_AY5_T1_Merge_Boot_App.hex",
        "switch": "bcm89572_evk_avb_switch_rev1.img",
        "soc": {
            "sail": "sail_ota.bin",
            "ufs": "ufs_ota.bin"
        }
    },
    "zip_extracted_files": [
        "MCU_GTMC_AY5_T1_Merge_Boot_App.hex",
        "sail_ota.bin",
        "bcm89572_evk_avb_switch_rev1.img",
        "ufs_ota.bin",
        "ota_firmware_info.xml"
    ]
}
```

**说明：**
- `firmware_files` 包含各升级类型的默认固件文件名，均为 `ota_alone.py` 中的命名
- `zip_extracted_files` 为 zip 解压后的预期文件列表，默认值与 ota_alone.py 命名一致
- `zip_file` 不保存在 JSON 中（zip 路径每次运行时选择，不持久化）

---

## 5. 升级流程对照（严格遵循 ota_alone.py）

### 5.1 一键完整升级 (run_full_upgrade)

```
文件: MCU_GTMC_AY5_T1_Merge_Boot_App.hex, sail_ota.bin, bcm89572_evk_avb_switch_rev1.img, ufs_ota.bin

1. SSH 连接
2. direct: 上传4个文件
   zip:    上传zip → 解压
3. 第一面升级序列:
   ota_tool show-version
   switch_bcm_flasher -v
   cat /firmware/verinfo/ver_info.txt
   ota_tool update-mcu {remote}/MCU_GTMC_AY5_T1_Merge_Boot_App.hex   [verbose=False]
   ota_proxy_app b
   ota_proxy_app j {remote}/ufs_ota.bin
   ota_proxy_app s
   ota_proxy_app k {remote}/sail_ota.bin
   switch_bcm_flasher -f {remote}/bcm89572_evk_avb_switch_rev1.img
   ota_tool reset-mcu-ota                                             [reboot]
4. [zip] 删除解压文件
5. 等待30s + 重连
6. ota_proxy_app m                     [timeout=120]
7. sleep 5s
8. 版本校验 (第一面后)
9. direct: 重新上传4个文件
   zip:    再次解压
10. 第二面升级序列:
    ota_tool update-mcu {remote}/MCU_GTMC_AY5_T1_Merge_Boot_App.hex  [verbose=False]
    ota_proxy_app b
    ota_proxy_app j {remote}/ufs_ota.bin
    ota_proxy_app s
    ota_proxy_app k {remote}/sail_ota.bin
    ota_tool reset-mcu-ota                                            [reboot]
11. [zip] 删除解压文件
12. 等待30s + 重连
13. ota_proxy_app m                    [timeout=120]
14. sleep 5s
15. 版本校验 (第二面后)
16. 清理远程文件（direct: 删除4个固件 / zip: 删除压缩包）
17. 断开SSH
```

### 5.2 单独升级 MCU (run_mcu_upgrade)

```
文件: MCU_GTMC_AY5_T1_Merge_Boot_App.hex

1. SSH 连接
2. direct: 上传1个文件
   zip:    上传zip → 解压
3. 第一面:
   ota_tool update-mcu {remote}/MCU_GTMC_AY5_T1_Merge_Boot_App.hex   [verbose=False]
   ota_tool reset-mcu-ota                                             [reboot]
4. [zip] 删除解压文件
5. 重连 + 版本校验
6. 第二面（文件复用，不重新上传，zip模式需再次解压）:
   [zip] 再次解压
   ota_tool update-mcu {remote}/MCU_GTMC_AY5_T1_Merge_Boot_App.hex   [verbose=False]
   ota_tool reset-mcu-ota                                             [reboot]
7. [zip] 删除解压文件
8. 重连 + 版本校验
9. 清理 + 断开SSH
```

### 5.3 单独升级 Switch (run_switch_upgrade)

```
文件: bcm89572_evk_avb_switch_rev1.img

1. SSH 连接
2. direct: 上传1个文件
   zip:    上传zip → 解压
3. switch_bcm_flasher -f {remote}/bcm89572_evk_avb_switch_rev1.img
4. ota_tool reset-mcu-ota                                             [reboot]
5. [zip] 删除解压文件
6. 重连 + 版本校验
7. 清理 + 断开SSH
```

### 5.4 单独升级 SoC (run_soc_upgrade)

```
文件: sail_ota.bin, ufs_ota.bin

1. SSH 连接
2. direct: 上传2个文件
   zip:    上传zip → 解压
3. 第一遍:
   ota_proxy_app b
   ota_proxy_app j {remote}/ufs_ota.bin
   ota_proxy_app s
   ota_proxy_app k {remote}/sail_ota.bin
   ota_tool reset-mcu-ota                                             [reboot]
4. [zip] 删除解压文件
5. 重连 + 版本校验
6. ota_proxy_app m                     [timeout=120]
7. 第二遍:
   direct: 重新上传2个文件
   zip:    再次解压
   ota_proxy_app b
   ota_proxy_app j {remote}/ufs_ota.bin
   ota_proxy_app s
   ota_proxy_app k {remote}/sail_ota.bin
   ota_tool reset-mcu-ota                                             [reboot]
8. [zip] 删除解压文件
9. 重连 + 版本校验
10. ota_proxy_app m                    [timeout=120]
11. 清理 + 断开SSH
```

---

## 6. 核心约束

| 约束 | 详情 |
|------|------|
| **命令序列不可变** | 升级命令的顺序、参数格式与 `ota_alone.py` 完全一致 |
| **verbose 规则** | 包含 `update-mcu` 或 `switch-partition` 的命令在 GUI 中不打印实时输出（仅更新状态栏进度） |
| **zip + 升级类型组合** | 不限制。用户选什么文件就用什么，zip 模式下 extracted_files 中对应类型的文件名参与命令拼接 |
| **文件复用** | MCU 单独升级第二面不重复上传（第一面上传的文件保留在远程）；SoC 和完整升级第二面需要重新上传/解压 |
| **cleanup** | 无论成功失败，finally 块中清理远程固件文件 |
| **线程** | GUI 升级任务在 `threading.Thread(daemon=True)` 中执行，UI 更新通过 `root.after()` |

---

## 7. 关键设计决策

| 决策点 | 决定 |
|--------|------|
| 窗口结构 | 配置窗口（modal 弹窗） → 主升级窗口（独立窗口） |
| 固件来源切换 | RadioButton `单个文件` / `ZIP 压缩包`，选中后动态渲染文件选择区 |
| 配置持久化 | "保存为默认配置" 按钮直接写回 `config.json` |
| 默认值来源 | `ota_alone.py` 中的命名和流程为准 |
| 旧脚本处理 | `ota_alone.py` 和 `all.py` 保留不动，新脚本为新建文件 |
| CLI 参数 | 命令行选项直接覆盖，无需交互确认（如 `--host`、`--type full`） |
