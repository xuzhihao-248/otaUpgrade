# OTA 烧录工具

基于 SSH 的 OTA 固件升级工具，支持 GUI 与 CLI 两种运行模式。

## 功能概述

| 升级类型 | 说明 |
|----------|------|
| 一键完整升级 | MCU + Switch + SoC 两面升级 + 版本校验 |
| 单独升级 MCU | MCU A/B 两面升级 |
| 单独升级 Switch | Switch 单面升级 |
| 单独升级 SoC | SoC（sail + UFS）两面升级 |

两种固件上传方式：**直接上传文件** 或 **ZIP 压缩包上传 + 远程解压**。

## 环境要求

- Python >= 3.12
- 依赖：paramiko（SSH）、tkinter（GUI，Python 标准库）

```bash
uv sync
```

## 测试

```bash
# 运行全部单元测试
uv run pytest tests/ -v

# 运行集成测试（需要真实设备连接）
uv run pytest tests/ -v --run-integration

# 覆盖率报告
uv run pytest tests/ --cov=ota_core --cov=ota_cli --cov-report=term-missing
```

## 快速开始

```bash
# GUI 模式（默认）
uv run python ota_tool.py

# CLI 模式
uv run python ota_tool.py --cli --help
uv run python ota_tool.py --cli --type full --files ./images/MCU.hex ./images/sail.bin ./images/switch.img ./images/ufs.bin
uv run python ota_tool.py --cli --test-connection
```

## CLI 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--host` | 设备 IP | config.json |
| `--user` | SSH 用户名 | config.json |
| `--pw` | SSH 密码 | config.json |
| `--type` | full / mcu / switch / soc | full |
| `--source` | direct / zip | direct |
| `--files F1 F2...` | direct 模式固件路径 | config.json |
| `--zip PATH` | zip 压缩包路径 | — |
| `--test-connection` | 仅测试 SSH 连接后退出 | — |

所有参数可选，未提供的使用 **config/config.json** 中的默认值。CLI 参数直接覆盖，无需交互确认。

## GUI 使用流程

1. **配置窗口** — 填写/修改设备连接、路径、预期版本 → 可选"测试连接"、"保存为默认配置" → 点"确定"
2. **主升级窗口** — 选择升级类型 → 选择固件来源（单个文件 / ZIP）→ 选择固件文件 → 点"开始升级"
3. 升级日志实时显示，进度条显示任务状态

## 配置文件

**config/config.json** — 所有配置的默认值，GUI 中"保存为默认配置"可写回：

```json
{
    "device": {"host": "IP", "user": "用户名", "pw": "密码"},
    "paths": {"bin_path": "…", "lib_paths": "…", "remote_ota_dir": "…", "local_image_dir": "…"},
    "expected_versions": {"mcu": "…", "ufs": "…", "switch": "…"},
    "firmware_files": { "full": {...}, "mcu": {...}, "switch": {...}, "soc": {...} },
    "zip_extracted_files": {"mcu": "…", "sail": "…", "switch": "…", "ufs": "…"}
}
```

## 项目结构

```
valeoPythonScript/
├── ota_tool.py         # 入口（路由 CLI/GUI）
├── ota_core.py         # 核心逻辑（SSH/上传/命令/升级流程）
├── ota_gui.py          # GUI 界面（tkinter）
├── ota_cli.py          # CLI 终端交互
├── config/config.json   # 默认配置
├── images/              # 本地固件目录
├── tests/               # pytest 测试
│   ├── conftest.py
│   ├── test_ota_core.py
│   ├── test_ota_cli.py
│   ├── test_ota_core_flows.py
│   └── test_integration.py
└── src/
    ├── requirements.md  # 需求文档
    ├── design.md        # 设计文档
    └── test_plan.md     # 测试计划
```

核心原则：**升级逻辑与 UI 分离** — ota_core 提供细粒度 API（`ssh_connect`、`upload_file`、`execute_command`、`verify_versions` 等），GUI 与 CLI 各自编排流程。
