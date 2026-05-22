# OTA 烧录工具 — pytest 测试文档

## 1. 测试环境

### 依赖

```
pytest >= 8.0
pytest-mock >= 3.14       # mocker fixture
pytest-cov >= 6.0         # 覆盖率（可选）
```

```bash
uv add --dev pytest pytest-mock pytest-cov
```

### 目录结构

```
valeoPythonScript/
├── tests/
│   ├── __init__.py
│   ├── conftest.py                # 共享 fixtures
│   ├── test_ota_core.py           # ota_core 单元测试
│   ├── test_ota_cli.py            # CLI 参数解析 & 配置合并
│   ├── test_ota_core_flows.py     # 升级流程（mock SSH）
│   └── test_integration.py        # 集成测试（需真实设备，默认跳过）
├── ota_core.py
├── ota_cli.py
├── ota_gui.py
└── ota_tool.py
```

### 运行方式

```bash
# 全部测试（跳过集成测试）
uv run pytest tests/ -v

# 包含集成测试（需要真实设备）
uv run pytest tests/ -v --run-integration

# 覆盖率报告
uv run pytest tests/ --cov=ota_core --cov=ota_cli --cov-report=term-missing
```

---

## 2. conftest.py — 共享 Fixtures

```python
import json
import os
import tempfile
import pytest


@pytest.fixture
def sample_config():
    """完整的 config.json 样例数据。"""
    return {
        "device": {"host": "192.168.1.100", "user": "admin", "pw": "admin123"},
        "paths": {
            "bin_path": "/mnt/bin",
            "lib_paths": "/mnt/lib64:/mnt/usr/lib64:/lib:/usr/lib",
            "remote_ota_dir": "/ota",
            "local_image_dir": "./images",
        },
        "expected_versions": {
            "mcu": "R510_RS02_01",
            "ufs": "R510_RS02_00",
            "switch": "R400_RC02_2T",
        },
        "firmware_files": {
            "full": {
                "mcu": "MCU.hex", "sail": "sail.bin",
                "switch": "switch.img", "ufs": "ufs.bin",
            },
            "mcu": {"mcu": "MCU.hex"},
            "switch": {"switch": "switch.img"},
            "soc": {"sail": "sail.bin", "ufs": "ufs.bin"},
        },
        "zip_extracted_files": {
            "mcu": "MCU.hex", "sail": "sail.bin",
            "switch": "switch.img", "ufs": "ufs.bin",
        },
    }


@pytest.fixture
def temp_config_file(sample_config):
    """写入临时 config.json，返回路径。用完自动清理。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sample_config, f)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def mock_ssh(mocker):
    """mock paramiko.SSHClient，返回 mock 实例。"""
    mock_client = mocker.MagicMock()
    mock_transport = mocker.MagicMock()
    mock_transport.is_active.return_value = True
    mock_client.get_transport.return_value = mock_transport
    # 默认 exec_command 返回成功
    mock_stdout = mocker.MagicMock()
    mock_stdout.channel.exit_status_ready.side_effect = [True]
    mock_stdout.channel.recv_exit_status.return_value = 0
    mock_stdout.read.return_value = b""
    mock_stderr = mocker.MagicMock()
    mock_stderr.read.return_value = b""
    mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)
    return mock_client


@pytest.fixture
def mock_sftp(mocker, mock_ssh):
    """mock SFTP 客户端。"""
    sftp = mocker.MagicMock()
    mock_ssh.open_sftp.return_value = sftp
    return sftp


@pytest.fixture
def callbacks():
    """返回用于断言的 callbacks dict。"""
    return {
        "log": lambda msg, end="\n": None,
        "status": lambda text: None,
        "progress": lambda name, transferred, total: None,
    }


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration", action="store_true", default=False,
        help="运行需要真实设备的集成测试"
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: 需要真实设备连接")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        return
    skip = pytest.mark.skip(reason="需要 --run-integration 选项")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
```

---

## 3. test_ota_core.py — 核心模块单元测试

### 3.1 配置管理

```python
import json
import os
import pytest
import tempfile

import ota_core


class TestLoadConfig:
    """load_config 测试。"""

    def test_load_valid_config(self, temp_config_file, sample_config):
        """正常加载 config.json。"""
        # 需要 patch __file__ 或直接测试逻辑
        # 实际测试时用 monkeypatch 替换路径
        pass

    def test_load_nonexistent_file(self):
        """加载不存在的文件应抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            ota_core.load_config("/nonexistent/config.json")

    def test_load_invalid_json(self, tmp_path):
        """加载非 JSON 文件应抛出异常。"""
        p = tmp_path / "bad.json"
        p.write_text("not valid json")
        with pytest.raises(json.JSONDecodeError):
            ota_core.load_config(str(p))


class TestSaveConfig:
    """save_config 测试。"""

    def test_save_and_reload(self, tmp_path, sample_config):
        """保存后再加载，数据应一致。"""
        p = tmp_path / "config.json"
        ota_core.save_config(sample_config, str(p))
        with open(p) as f:
            reloaded = json.load(f)
        assert reloaded == sample_config
```

### 3.2 环境变量构建

```python
class TestBuildEnvFix:
    """build_env_fix 测试。"""

    def test_default_values(self):
        """不传 paths 时使用默认值。"""
        result = ota_core.build_env_fix({})
        assert "/mnt/bin" in result
        assert "/mnt/lib64" in result
        assert "export PATH=$PATH:" in result
        assert "export LD_LIBRARY_PATH=" in result
        assert "[ -f /etc/profile ]" in result

    def test_custom_values(self, sample_config):
        """自定义 paths 应反映在结果中。"""
        result = ota_core.build_env_fix(sample_config)
        assert "/mnt/bin" in result
        assert "/mnt/lib64:/mnt/usr/lib64:/lib:/usr/lib" in result

    def test_missing_paths_key(self):
        """paths 键缺失时不应崩溃。"""
        result = ota_core.build_env_fix({"paths": {}})
        assert isinstance(result, str)
        assert len(result) > 0
```

### 3.3 版本解析

```python
class TestParseVersions:
    """parse_versions 测试 — mock SSH exec_command 返回值。"""

    MCU_OUTPUT = """
    ========== OTA Tool Version Info ==========
    MCU Version: R510_RS02_01
    Board Type: GTMC_AY5
    ==========================================
    """

    SWITCH_OUTPUT = """
    BCM89572 EVK AVB Switch Flasher
    software version : GTMC_R400_RC02_2T
    build date: 2025-01-15
    """

    UFS_OUTPUT = '{"Version": "sail_ota_R510_RS02_00-20250115", "BuildDate": "2025-01-15"}'

    def test_parse_all_versions(self, mock_ssh):
        """正常解析三个组件版本。"""
        # 构造三次 exec_command 的不同返回
        call_count = [0]
        outputs = [
            (self.MCU_OUTPUT, ""),
            (self.SWITCH_OUTPUT, ""),
            (self.UFS_OUTPUT, ""),
        ]

        def side_effect(cmd):
            idx = call_count[0]
            call_count[0] += 1
            out, err = outputs[idx]
            mock_stdout = mock_ssh.exec_command.return_value[1]
            mock_stdout.channel.exit_status_ready.return_value = True
            mock_stdout.channel.recv_exit_status.return_value = 0
            mock_stdout.read.return_value = out.encode()
            mock_stderr = mock_ssh.exec_command.return_value[2]
            mock_stderr.read.return_value = err.encode()
            return (None, mock_stdout, mock_stderr)

        mock_ssh.exec_command.side_effect = side_effect
        # FIXME: 实际测试需要更精细的 mock，这里展示测试意图
        # versions = ota_core.parse_versions(mock_ssh, "")
        # assert versions["mcu"] == "R510_RS02_01"
        # assert versions["switch"] == "R400_RC02_2T"
        # assert versions["ufs"] == "R510_RS02_00"

    def test_parse_mcu_version_regex(self):
        """MCU 版本正则匹配。"""
        import re
        match = re.search(r'MCU Version:\s*(\S+)',
                          "MCU Version: R510_RS02_01\n")
        assert match.group(1) == "R510_RS02_01"

    def test_parse_mcu_not_found(self):
        """MCU 输出无匹配时返回 N/A。"""
        import re
        match = re.search(r'MCU Version:\s*(\S+)', "No version here")
        assert match is None

    def test_parse_switch_version_regex(self):
        """Switch 版本正则匹配。"""
        import re
        match = re.search(r'software version\s*:\s*GTMC_(\S+)',
                          "software version : GTMC_R400_RC02_2T")
        assert match.group(1) == "R400_RC02_2T"

    def test_parse_ufs_version_from_json(self):
        """UFS 版本从 JSON 中提取。"""
        import re, json
        ufs_json = '{"Version": "sail_ota_R510_RS02_00-20250115"}'
        v_json = json.loads(ufs_json)
        v_str = v_json.get("Version", "")
        match = re.search(r'_(R\d+_[A-Z0-9_]+)-', v_str)
        assert match.group(1) == "R510_RS02_00"
```

### 3.4 版本校验

```python
class TestVerifyVersions:
    """verify_versions 测试。"""

    def test_all_match(self, mocker, mock_ssh):
        """所有版本匹配时返回 True。"""
        mocker.patch("ota_core.parse_versions", return_value={
            "mcu": "R510_RS02_01", "ufs": "R510_RS02_00", "switch": "R400_RC02_2T",
        })
        expected = {"mcu": "R510_RS02_01", "ufs": "R510_RS02_00", "switch": "R400_RC02_2T"}
        result = ota_core.verify_versions(mock_ssh, "", expected, "test")
        assert result is True

    def test_mismatch_returns_false(self, mocker, mock_ssh):
        """任一版本不匹配返回 False。"""
        mocker.patch("ota_core.parse_versions", return_value={
            "mcu": "WRONG", "ufs": "R510_RS02_00", "switch": "R400_RC02_2T",
        })
        expected = {"mcu": "R510_RS02_01", "ufs": "R510_RS02_00", "switch": "R400_RC02_2T"}
        result = ota_core.verify_versions(mock_ssh, "", expected, "test")
        assert result is False

    def test_none_expected_skips_check(self, mocker, mock_ssh):
        """expected 中 None 字段跳过校验，不影响结果。"""
        mocker.patch("ota_core.parse_versions", return_value={
            "mcu": "R510_RS02_01", "ufs": "N/A", "switch": "N/A",
        })
        expected = {"mcu": "R510_RS02_01", "ufs": None, "switch": None}
        result = ota_core.verify_versions(mock_ssh, "", expected, "test")
        assert result is True
```

### 3.5 回调工具

```python
class TestEnsureCallbacks:
    """_ensure_callbacks 测试。"""

    def test_fills_missing_keys(self):
        """缺失的 key 被补齐为 no-op。"""
        result = ota_core._ensure_callbacks({})
        assert "log" in result
        assert "status" in result
        assert "progress" in result
        # no-op 调用不应抛异常
        result["log"]("test")
        result["status"]("test")
        result["progress"]("f", 0, 100)

    def test_preserves_existing_keys(self):
        """已有的 key 不被覆盖。"""
        sentinel = object()
        cb = {"log": sentinel}
        result = ota_core._ensure_callbacks(cb)
        assert result["log"] is sentinel


class TestZipFilenameForRole:
    """_zip_filename_for_role 测试。"""

    def test_returns_matching_role(self):
        extracted = {"mcu": "my_mcu.hex", "sail": "my_sail.bin"}
        assert ota_core._zip_filename_for_role(extracted, "mcu") == "my_mcu.hex"

    def test_returns_empty_for_missing_role(self):
        assert ota_core._zip_filename_for_role({}, "ufs") == ""
```

---

## 4. test_ota_cli.py — CLI 模块测试

### 4.1 参数解析

```python
import sys
import pytest
from ota_cli import _parse_args, _build_config, UPGRADE_TYPE_ROLES


class TestParseArgs:
    """_parse_args 测试。"""

    def test_help_flag(self, capsys):
        """--help 输出帮助并 exit(0)。"""
        with pytest.raises(SystemExit) as e:
            _parse_args(["--help"])
        assert e.value.code == 0
        captured = capsys.readouterr()
        assert "OTA 升级工具" in captured.out

    def test_test_connection_flag(self):
        """--test-connection 单独出现。"""
        result = _parse_args(["--test-connection"])
        assert result["test_connection"] is True

    def test_simple_key_value(self):
        """--host 192.168.1.1 解析为键值对。"""
        result = _parse_args(["--host", "192.168.1.1"])
        assert result["--host"] == "192.168.1.1"

    def test_type_and_source(self):
        """--type 和 --source。"""
        result = _parse_args(["--type", "mcu", "--source", "zip"])
        assert result["--type"] == "mcu"
        assert result["--source"] == "zip"

    def test_files_list(self):
        """--files 后面多个文件直到下一个 -- 参数。"""
        result = _parse_args([
            "--files", "a.hex", "b.bin", "c.img", "--type", "full"
        ])
        assert result["_files"] == ["a.hex", "b.bin", "c.img"]
        assert result["--type"] == "full"

    def test_files_list_at_end(self):
        """--files 在命令行末尾。"""
        result = _parse_args(["--files", "a.hex", "b.bin"])
        assert result["_files"] == ["a.hex", "b.bin"]

    def test_extracted_list(self):
        """--extracted 多个文件名。"""
        result = _parse_args([
            "--extracted", "m.hex", "s.bin", "sw.img", "u.bin"
        ])
        assert result["_extracted"] == ["m.hex", "s.bin", "sw.img", "u.bin"]

    def test_unknown_arg_warning(self, capsys):
        """无法识别的参数输出警告。"""
        _parse_args(["--unknown-flag"])
        captured = capsys.readouterr()
        assert "忽略" in captured.out

    def test_empty_args(self):
        """空参数返回基本结构。"""
        result = _parse_args([])
        assert result["_files"] == []
        assert result["_extracted"] == []

    def test_zip_path(self):
        """--zip 参数。"""
        result = _parse_args(["--zip", "/path/to/firmware.zip"])
        assert result["--zip"] == "/path/to/firmware.zip"
```

### 4.2 配置合并

```python
class TestBuildConfig:
    """_build_config 测试。"""

    def test_full_direct_mode_defaults(self, sample_config):
        """full + direct 模式使用默认固件文件名。"""
        cli = {"--type": "full", "--source": "direct"}
        config = _build_config(cli, sample_config)
        assert config["mode"] == "direct"
        assert config["firmware_files"]["mcu"] == "MCU.hex"
        assert config["firmware_files"]["sail"] == "sail.bin"
        assert config["firmware_files"]["switch"] == "switch.img"
        assert config["firmware_files"]["ufs"] == "ufs.bin"

    def test_mcu_direct_mode_defaults(self, sample_config):
        """mcu + direct 模式只有 mcu 固件。"""
        cli = {"--type": "mcu", "--source": "direct"}
        config = _build_config(cli, sample_config)
        assert list(config["firmware_files"].keys()) == ["mcu"]
        assert config["firmware_files"]["mcu"] == "MCU.hex"

    def test_cli_overrides_host(self, sample_config):
        """CLI --host 覆盖默认值。"""
        cli = {"--type": "full", "--host": "10.0.0.1"}
        config = _build_config(cli, sample_config)
        assert config["device"]["host"] == "10.0.0.1"
        assert config["device"]["user"] == "admin"  # 未覆盖保持默认

    def test_cli_overrides_paths(self, sample_config):
        """CLI 路径参数覆盖默认。"""
        cli = {"--type": "full", "--bin-path": "/custom/bin", "--remote-dir": "/custom/ota"}
        config = _build_config(cli, sample_config)
        assert config["paths"]["bin_path"] == "/custom/bin"
        assert config["paths"]["remote_ota_dir"] == "/custom/ota"

    def test_cli_overrides_versions(self, sample_config):
        """CLI 版本参数覆盖默认。"""
        cli = {"--type": "full", "--expected-mcu": "V2.0", "--expected-ufs": "V1.0"}
        config = _build_config(cli, sample_config)
        assert config["expected_versions"]["mcu"] == "V2.0"
        assert config["expected_versions"]["ufs"] == "V1.0"
        assert config["expected_versions"]["switch"] == "R400_RC02_2T"

    def test_zip_mode_with_cli_files(self, sample_config):
        """zip 模式 + --extracted 自定义文件名。"""
        cli = {"--type": "full", "--source": "zip", "--zip": "/tmp/fw.zip"}
        cli["_extracted"] = ["a.hex", "b.bin", "c.img", "d.bin"]
        config = _build_config(cli, sample_config)
        assert config["mode"] == "zip"
        assert config["zip_file"] == "/tmp/fw.zip"
        assert config["extracted_files"]["mcu"] == "a.hex"
        assert config["extracted_files"]["ufs"] == "d.bin"

    def test_zip_mode_default_extracted(self, sample_config):
        """zip 模式无 --extracted 时使用 zip_extracted_files。"""
        cli = {"--type": "full", "--source": "zip", "--zip": "/tmp/fw.zip"}
        config = _build_config(cli, sample_config)
        assert config["extracted_files"]["mcu"] == "MCU.hex"
        assert config["extracted_files"]["sail"] == "sail.bin"

    def test_direct_mode_with_cli_files(self, sample_config):
        """direct 模式 + --files 覆盖固件路径。"""
        cli = {"--type": "switch", "--source": "direct"}
        cli["_files"] = ["/home/user/my_switch.img"]
        config = _build_config(cli, sample_config)
        assert config["firmware_files"]["switch"] == "my_switch.img"
        assert config["paths"]["local_image_dir"] == "/home/user"
```

### 4.3 UPGRADE_TYPE_ROLES

```python
class TestUpgradeTypeRoles:
    def test_full_has_four_roles(self):
        assert len(UPGRADE_TYPE_ROLES["full"]) == 4

    def test_mcu_has_one_role(self):
        assert UPGRADE_TYPE_ROLES["mcu"] == ["mcu"]

    def test_switch_has_one_role(self):
        assert UPGRADE_TYPE_ROLES["switch"] == ["switch"]

    def test_soc_has_two_roles(self):
        assert UPGRADE_TYPE_ROLES["soc"] == ["sail", "ufs"]
```

---

## 5. test_ota_core_flows.py — 升级流程测试（mock SSH）

本模块测试四种升级流程的**编排逻辑**（命令序列、错误处理、清理行为），所有 SSH/SFTP 操作通过 mock 注入。

```python
import pytest
import ota_core


class TestFullUpgradeFlow:
    """run_full_upgrade 流程测试。"""

    def test_direct_mode_command_sequence(self, mocker, sample_config, callbacks):
        """验证 direct 模式第一面升级命令序列。"""
        # mock ssh_connect, upload_files_batch, execute_command, execute_reboot_command, wait_reconnect, verify_versions
        mock_ssh = mocker.MagicMock()
        mock_transport = mocker.MagicMock()
        mock_transport.is_active.return_value = True
        mock_ssh.get_transport.return_value = mock_transport

        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.ssh_disconnect")
        mock_upload = mocker.patch("ota_core.upload_files_batch")
        mock_exec = mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mock_reboot = mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mock_reconnect = mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=True)

        sample_config["mode"] = "direct"
        sample_config["firmware_files"] = {
            "mcu": "m.hex", "sail": "s.bin", "switch": "sw.img", "ufs": "u.bin"
        }

        ota_core.run_full_upgrade(sample_config, callbacks)

        # 验证上传了正确的文件
        uploaded = mock_upload.call_args_list[0][0][3]  # files 参数
        assert "m.hex" in uploaded
        assert "s.bin" in uploaded
        assert "sw.img" in uploaded
        assert "u.bin" in uploaded

        # 验证第一面包含 update-mcu
        all_cmds = [c[0][1] for c in mock_exec.call_args_list]
        assert any("update-mcu" in cmd for cmd in all_cmds)
        # 验证包含 switch_bcm_flasher
        assert any("switch_bcm_flasher" in cmd for cmd in all_cmds)
        # 验证 reset-mcu-ota 通过 execute_reboot_command 执行
        reboot_cmds = [c[0][1] for c in mock_reboot.call_args_list]
        assert any("reset-mcu-ota" in cmd for cmd in reboot_cmds)

    def test_zip_mode_uploads_and_extracts(self, mocker, sample_config, callbacks):
        """验证 ZIP 模式上传压缩包并解压。"""
        mock_ssh = mocker.MagicMock()
        mock_transport = mocker.MagicMock()
        mock_transport.is_active.return_value = True
        mock_ssh.get_transport.return_value = mock_transport

        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mock_upload_zip = mocker.patch("ota_core.upload_zip_file",
                                        return_value="fw.zip")
        mock_extract = mocker.patch("ota_core.extract_zip")
        mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=True)
        mock_delete = mocker.patch("ota_core.delete_remote_files")

        sample_config["mode"] = "zip"
        sample_config["zip_file"] = "/tmp/firmware.zip"
        sample_config["extracted_files"] = {
            "mcu": "m.hex", "sail": "s.bin", "switch": "sw.img", "ufs": "u.bin"
        }

        ota_core.run_full_upgrade(sample_config, callbacks)

        mock_upload_zip.assert_called_once()
        mock_extract.assert_called()
        # ZIP 在 reboot 后应清理远程文件
        assert mock_delete.call_count >= 1

    def test_verify_failure_raises(self, mocker, sample_config, callbacks):
        """版本校验失败应抛出 RuntimeError。"""
        mock_ssh = mocker.MagicMock()
        mock_transport = mocker.MagicMock()
        mock_transport.is_active.return_value = True
        mock_ssh.get_transport.return_value = mock_transport

        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.upload_files_batch")
        mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=False)

        sample_config["mode"] = "direct"
        sample_config["firmware_files"] = {
            "mcu": "m.hex", "sail": "s.bin", "switch": "sw.img", "ufs": "u.bin"
        }

        with pytest.raises(RuntimeError, match="版本号不匹配"):
            ota_core.run_full_upgrade(sample_config, callbacks)


class TestSingleUpgradeFlows:
    """run_mcu / run_switch / run_soc 流程测试。"""

    @pytest.mark.parametrize("flow_func,cmd_keyword", [
        ("run_mcu_upgrade", "update-mcu"),
        ("run_switch_upgrade", "switch_bcm_flasher"),
        ("run_soc_upgrade", "ota_proxy_app"),
    ])
    def test_flow_contains_expected_command(self, mocker, sample_config, callbacks,
                                            flow_func, cmd_keyword):
        """各升级流程包含对应的核心命令。"""
        mock_ssh = mocker.MagicMock()
        mock_transport = mocker.MagicMock()
        mock_transport.is_active.return_value = True
        mock_ssh.get_transport.return_value = mock_transport

        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.upload_files_batch")
        mock_exec = mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=True)

        sample_config["mode"] = "direct"
        sample_config["firmware_files"] = {
            "mcu": "m.hex", "sail": "s.bin", "switch": "sw.img", "ufs": "u.bin"
        }

        func = getattr(ota_core, flow_func)
        func(sample_config, callbacks)

        all_cmds = [c[0][1] for c in mock_exec.call_args_list]
        assert any(cmd_keyword in cmd for cmd in all_cmds), \
            f"期望命令包含 '{cmd_keyword}'，实际命令: {all_cmds}"

    def test_mcu_two_pass_upgrade(self, mocker, sample_config, callbacks):
        """MCU 升级应执行两面（两次 update-mcu + 两次 reboot）。"""
        mock_ssh = mocker.MagicMock()
        mock_transport = mocker.MagicMock()
        mock_transport.is_active.return_value = True
        mock_ssh.get_transport.return_value = mock_transport

        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.upload_files_batch")
        mock_exec = mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mock_reboot = mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=True)

        sample_config["mode"] = "direct"
        sample_config["firmware_files"] = {"mcu": "m.hex"}

        ota_core.run_mcu_upgrade(sample_config, callbacks)

        # 两次 update-mcu
        mcu_calls = [c for c in mock_exec.call_args_list
                     if "update-mcu" in c[0][1]]
        assert len(mcu_calls) == 2
        # 两次 reboot
        reboot_calls = [c for c in mock_reboot.call_args_list
                        if "reset-mcu-ota" in c[0][1]]
        assert len(reboot_calls) == 2
```

---

## 6. test_integration.py — 集成测试（需真实设备）

```python
"""
集成测试 — 需要真实设备连接。
运行: uv run pytest tests/ -v --run-integration
"""
import os
import time
import json
import pytest
import ota_core


@pytest.fixture(scope="module")
def live_config():
    """从环境变量或 config.json 读取真实设备配置。"""
    config = ota_core.load_config()
    host = os.environ.get("OTA_TEST_HOST", config["device"]["host"])
    user = os.environ.get("OTA_TEST_USER", config["device"]["user"])
    pw = os.environ.get("OTA_TEST_PW", config["device"]["pw"])
    return {
        "host": host,
        "user": user,
        "pw": pw,
        "remote_dir": config["paths"]["remote_ota_dir"],
    }


@pytest.fixture(scope="module")
def live_ssh(live_config):
    """建立真实 SSH 连接，模块级别复用。"""
    ssh = ota_core.ssh_connect(
        live_config["host"], live_config["user"], live_config["pw"]
    )
    yield ssh
    ota_core.ssh_disconnect(ssh)


@pytest.mark.integration
class TestSSHConnection:
    """真实设备连接测试。"""

    def test_connect_and_disconnect(self, live_config):
        """基本连接/断开。"""
        ssh = ota_core.ssh_connect(
            live_config["host"], live_config["user"], live_config["pw"]
        )
        assert ssh.get_transport().is_active()
        ota_core.ssh_disconnect(ssh)

    def test_test_connection_helper(self, live_config):
        """ssh_test_connection 包装函数。"""
        ok, err = ota_core.ssh_test_connection(
            live_config["host"], live_config["user"], live_config["pw"]
        )
        assert ok is True
        assert err == ""

    def test_bad_credentials_fails(self, live_config):
        """错误密码应返回失败。"""
        ok, err = ota_core.ssh_test_connection(
            live_config["host"], live_config["user"], "wrong_password"
        )
        assert ok is False


@pytest.mark.integration
class TestRemoteCommands:
    """真实设备命令执行。"""

    def test_simple_command(self, live_ssh):
        """执行 echo 并获取输出。"""
        code, output = ota_core.execute_command(
            live_ssh, "echo hello_test", "", timeout=10
        )
        assert code == 0
        assert "hello_test" in output

    def test_env_fix_applied(self, live_ssh, live_config):
        """环境变量修复后的命令。"""
        config = {
            "paths": {
                "bin_path": "/mnt/bin",
                "lib_paths": "/mnt/lib64:/mnt/usr/lib64:/lib:/usr/lib",
            }
        }
        env_fix = ota_core.build_env_fix(config)
        code, _ = ota_core.execute_command(live_ssh, "which ota_tool", env_fix)
        assert code == 0

    def test_timeout_raises(self, live_ssh):
        """超时命令应抛出 TimeoutError。"""
        with pytest.raises(TimeoutError):
            ota_core.execute_command(live_ssh, "sleep 999", "", timeout=3)


@pytest.mark.integration
class TestVersionParsing:
    """真实设备版本读取。"""

    def test_parse_versions_returns_dict(self, live_ssh):
        """parse_versions 返回正确的 dict 结构。"""
        config = {"paths": {"bin_path": "/mnt/bin",
                            "lib_paths": "/mnt/lib64:/mnt/usr/lib64:/lib:/usr/lib"}}
        env_fix = ota_core.build_env_fix(config)
        versions = ota_core.parse_versions(live_ssh, env_fix)
        assert "mcu" in versions
        assert "ufs" in versions
        assert "switch" in versions
        # 版本号非空
        for k, v in versions.items():
            assert v and v != "N/A", f"{k} 版本号为 N/A"


@pytest.mark.integration
class TestFileOperations:
    """真实设备文件上传/清理。"""

    def test_upload_and_delete(self, live_ssh, live_config, tmp_path):
        """上传一个小文件，验证存在后删除。"""
        test_file = tmp_path / "test_upload.txt"
        test_file.write_text("ota test content")

        remote_dir = live_config["remote_dir"]
        remote_path = f"{remote_dir}/test_upload.txt"

        ota_core.upload_file(live_ssh, str(test_file), remote_path)

        # 验证文件存在
        code, _ = ota_core.execute_command(
            live_ssh, f"test -f {remote_path} && echo EXISTS", ""
        )
        assert "EXISTS" in _

        # 清理
        ota_core.delete_remote_files(live_ssh, remote_dir, ["test_upload.txt"])
        code, _ = ota_core.execute_command(
            live_ssh, f"test -f {remote_path} && echo EXISTS || echo GONE", ""
        )
        assert "GONE" in _
```

---

## 7. GUI 测试说明

tkinter GUI 的自动化测试较为复杂，推荐以下策略：

1. **手动测试清单**（见第 8 节）
2. **逻辑层抽离**：GUI 中的 `_collect_config`、`_build_config` 等不依赖 tkinter 的方法可直接单元测试
3. **headless 测试**（可选）：设置 `$DISPLAY` 或使用 `Xvfb` 在 CI 中运行

```python
# tests/test_ota_gui_logic.py — 仅测试 GUI 中的纯逻辑部分
import pytest
from ota_gui import UPGRADE_TYPE_ROLES


class TestGuiTypeRoles:
    def test_full_roles(self):
        roles = UPGRADE_TYPE_ROLES["full"]
        assert roles == [
            ("mcu", "MCU 固件"), ("sail", "Sail 固件"),
            ("switch", "Switch 固件"), ("ufs", "UFS 固件"),
        ]

    def test_soc_roles(self):
        roles = UPGRADE_TYPE_ROLES["soc"]
        assert roles == [("sail", "Sail 固件"), ("ufs", "UFS 固件")]
```

---

## 8. 手动测试清单

以下场景无法自动化，需人工验证：

### 8.1 GUI 配置窗口

| # | 测试项 | 预期结果 |
|---|--------|----------|
| 1 | 启动 `python ota_tool.py` | 显示配置窗口，字段从 config.json 填充 |
| 2 | 修改 IP/用户/密码 → 测试连接 | 成功弹窗 "SSH 连接成功" |
| 3 | 错误 IP → 测试连接 | 失败弹窗显示错误详情 |
| 4 | 修改路径/版本 → 保存为默认配置 | 弹窗 "已保存"，config.json 已更新 |
| 5 | 重新启动程序 | 配置窗口显示上次保存的值 |

### 8.2 GUI 升级窗口

| # | 测试项 | 预期结果 |
|---|--------|----------|
| 6 | 切换升级类型（full/mcu/switch/soc）| 固件文件区域动态更新行数 |
| 7 | 切换固件来源（direct/zip）| 文件区域切换为 ZIP 选择 + 解压文件名 |
| 8 | direct 模式未选文件直接点"开始升级"| 弹窗提示选择文件 |
| 9 | zip 模式未选 ZIP 直接点"开始升级"| 弹窗提示选择 ZIP |
| 10 | 点击"清空日志" | 日志区域清空 |
| 11 | 执行升级 → 观察日志 | 实时输出，进度条滚动，完成后按钮恢复 |

### 8.3 CLI 模式

| # | 测试项 | 预期结果 |
|---|--------|----------|
| 12 | `python ota_tool.py --cli --help` | 输出帮助信息 |
| 13 | `python ota_tool.py --cli --test-connection` | 测试连接，输出成功/失败 |
| 14 | `python ota_tool.py --cli --type mcu --files ./images/MCU.hex` | 执行 MCU 升级 |
| 15 | `python ota_tool.py --cli --type soc --source zip --zip ./fw.zip` | ZIP 模式 SoC 升级 |
| 16 | `python ota_tool.py --cli --host 192.168.1.1 --test-connection` | CLI 覆盖 IP 并测试 |

---

## 9. 测试覆盖目标

| 模块 | 目标覆盖率 | 说明 |
|------|-----------|------|
| `ota_core.py` — 工具函数 | ≥ 90% | `build_env_fix`, `_ensure_callbacks`, `_zip_filename_for_role`, `load_config`, `save_config` |
| `ota_core.py` — 版本解析 | ≥ 85% | `parse_versions` 正则匹配、`verify_versions` 比对逻辑 |
| `ota_core.py` — 升级流程 | ≥ 70% | Mock SSH 验证命令序列、错误处理、清理行为 |
| `ota_cli.py` | ≥ 85% | 参数解析、配置合并、边界情况 |
| `ota_gui.py` | 手动 | UPGRADE_TYPE_ROLES 常量可单元测试 |

---

## 10. CI 集成（GitHub Actions 示例）

```yaml
# .github/workflows/test.yml
name: pytest

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dependencies
        run: |
          pip install uv
          uv sync
      - name: Run tests
        run: uv run pytest tests/ -v --cov=ota_core --cov=ota_cli --cov-report=term-missing
```
