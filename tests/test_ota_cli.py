"""ota_cli 模块单元测试。"""
import pytest
from ota_cli import _parse_args, _build_config, UPGRADE_TYPE_ROLES


# ==================== _parse_args ====================

class TestParseArgs:
    def test_help_flag(self, capsys):
        """--help 输出帮助并 exit(0)。"""
        with pytest.raises(SystemExit) as e:
            _parse_args(["--help"])
        assert e.value.code == 0
        captured = capsys.readouterr()
        assert "OTA 升级工具" in captured.out

    def test_test_connection_flag(self):
        result = _parse_args(["--test-connection"])
        assert result["test_connection"] is True

    def test_simple_key_value(self):
        result = _parse_args(["--host", "192.168.1.1"])
        assert result["--host"] == "192.168.1.1"

    def test_user_and_pw(self):
        result = _parse_args(["--user", "admin", "--pw", "secret123"])
        assert result["--user"] == "admin"
        assert result["--pw"] == "secret123"

    def test_type_and_source(self):
        result = _parse_args(["--type", "mcu", "--source", "zip"])
        assert result["--type"] == "mcu"
        assert result["--source"] == "zip"

    def test_files_list(self):
        """--files 后多个文件直到下一个 -- 参数。"""
        result = _parse_args([
            "--files", "a.hex", "b.bin", "c.img", "--type", "full"
        ])
        assert result["_files"] == ["a.hex", "b.bin", "c.img"]
        assert result["--type"] == "full"

    def test_files_list_at_end(self):
        result = _parse_args(["--files", "a.hex", "b.bin"])
        assert result["_files"] == ["a.hex", "b.bin"]

    def test_files_single(self):
        result = _parse_args(["--files", "only_one.hex"])
        assert result["_files"] == ["only_one.hex"]

    def test_extracted_list(self):
        result = _parse_args([
            "--extracted", "m.hex", "s.bin", "sw.img", "u.bin"
        ])
        assert result["_extracted"] == ["m.hex", "s.bin", "sw.img", "u.bin"]

    def test_empty_args(self):
        result = _parse_args([])
        assert result["_files"] == []
        assert result["_extracted"] == []

    def test_zip_path(self):
        result = _parse_args(["--zip", "/path/to/firmware.zip"])
        assert result["--zip"] == "/path/to/firmware.zip"

    def test_unknown_arg_warning(self, capsys):
        _parse_args(["--unknown-flag"])
        captured = capsys.readouterr()
        assert "忽略" in captured.out

    def test_all_path_and_version_args(self):
        """所有路径和版本 CLI 参数。"""
        result = _parse_args([
            "--bin-path", "/custom/bin",
            "--lib-paths", "/custom/lib",
            "--remote-dir", "/custom/ota",
            "--local-dir", "/custom/images",
            "--expected-mcu", "V1.0",
            "--expected-ufs", "V2.0",
            "--expected-switch", "V3.0",
        ])
        assert result["--bin-path"] == "/custom/bin"
        assert result["--lib-paths"] == "/custom/lib"
        assert result["--remote-dir"] == "/custom/ota"
        assert result["--local-dir"] == "/custom/images"
        assert result["--expected-mcu"] == "V1.0"
        assert result["--expected-ufs"] == "V2.0"
        assert result["--expected-switch"] == "V3.0"


# ==================== _build_config ====================

class TestBuildConfig:
    def test_full_direct_defaults(self, sample_config):
        """full + direct 模式使用默认固件文件名。"""
        cli = {"--type": "full", "--source": "direct"}
        config = _build_config(cli, sample_config)
        assert config["mode"] == "direct"
        assert config["firmware_files"]["mcu"] == "MCU.hex"
        assert config["firmware_files"]["sail"] == "sail.bin"
        assert config["firmware_files"]["switch"] == "switch.img"
        assert config["firmware_files"]["ufs"] == "ufs.bin"

    def test_mcu_direct_defaults(self, sample_config):
        """mcu + direct 模式只有 mcu 固件。"""
        cli = {"--type": "mcu", "--source": "direct"}
        config = _build_config(cli, sample_config)
        assert list(config["firmware_files"].keys()) == ["mcu"]
        assert config["firmware_files"]["mcu"] == "MCU.hex"

    def test_switch_direct_defaults(self, sample_config):
        """switch + direct 模式只有 switch 固件。"""
        cli = {"--type": "switch", "--source": "direct"}
        config = _build_config(cli, sample_config)
        assert list(config["firmware_files"].keys()) == ["switch"]

    def test_soc_direct_defaults(self, sample_config):
        """soc + direct 模式有 sail 和 ufs。"""
        cli = {"--type": "soc", "--source": "direct"}
        config = _build_config(cli, sample_config)
        assert set(config["firmware_files"].keys()) == {"sail", "ufs"}

    def test_default_type_is_full(self, sample_config):
        """不指定 --type 默认 full。"""
        cli = {"--source": "direct"}
        config = _build_config(cli, sample_config)
        assert len(config["firmware_files"]) == 4

    def test_cli_overrides_host(self, sample_config):
        cli = {"--type": "full", "--host": "10.0.0.1"}
        config = _build_config(cli, sample_config)
        assert config["device"]["host"] == "10.0.0.1"
        assert config["device"]["user"] == "admin"

    def test_cli_overrides_paths(self, sample_config):
        cli = {"--type": "full", "--bin-path": "/custom/bin", "--remote-dir": "/custom/ota"}
        config = _build_config(cli, sample_config)
        assert config["paths"]["bin_path"] == "/custom/bin"
        assert config["paths"]["remote_ota_dir"] == "/custom/ota"

    def test_cli_overrides_versions(self, sample_config):
        cli = {"--type": "full", "--expected-mcu": "V2.0", "--expected-ufs": "V1.0"}
        config = _build_config(cli, sample_config)
        assert config["expected_versions"]["mcu"] == "V2.0"
        assert config["expected_versions"]["ufs"] == "V1.0"
        assert config["expected_versions"]["switch"] == "R400_RC02_2T"

    def test_zip_mode_with_cli_extracted(self, sample_config):
        """zip 模式 + --extracted 自定义文件名。"""
        cli = {"--type": "full", "--source": "zip", "--zip": "/tmp/fw.zip"}
        cli["_extracted"] = ["a.hex", "b.bin", "c.img", "d.bin"]
        config = _build_config(cli, sample_config)
        assert config["mode"] == "zip"
        assert config["zip_file"] == "/tmp/fw.zip"
        assert config["extracted_files"]["mcu"] == "a.hex"
        assert config["extracted_files"]["sail"] == "b.bin"
        assert config["extracted_files"]["switch"] == "c.img"
        assert config["extracted_files"]["ufs"] == "d.bin"

    def test_zip_mode_default_extracted(self, sample_config):
        """zip 模式无 --extracted 时使用 zip_extracted_files 默认值。"""
        cli = {"--type": "full", "--source": "zip", "--zip": "/tmp/fw.zip"}
        config = _build_config(cli, sample_config)
        assert config["extracted_files"]["mcu"] == "MCU.hex"
        assert config["extracted_files"]["sail"] == "sail.bin"

    def test_direct_mode_cli_files_sets_dirname(self, sample_config):
        """direct 模式 + --files 时提取 dirname 为 local_image_dir。"""
        cli = {"--type": "switch", "--source": "direct"}
        cli["_files"] = ["/home/user/my_switch.img"]
        config = _build_config(cli, sample_config)
        assert config["firmware_files"]["switch"] == "my_switch.img"
        assert config["paths"]["local_image_dir"] == "/home/user"

    def test_direct_mode_partial_cli_files(self, sample_config):
        """direct 模式 --files 只提供部分文件时，仅设置提供的 role。"""
        cli = {"--type": "full", "--source": "direct"}
        cli["_files"] = ["/tmp/custom_mcu.hex", "/tmp/custom_sail.bin"]
        config = _build_config(cli, sample_config)
        assert config["firmware_files"]["mcu"] == "custom_mcu.hex"
        assert config["firmware_files"]["sail"] == "custom_sail.bin"
        # 只有两个文件，switch 和 ufs 不在 firmware_files 中
        assert "switch" not in config["firmware_files"]
        assert "ufs" not in config["firmware_files"]


# ==================== UPGRADE_TYPE_ROLES ====================

class TestUpgradeTypeRoles:
    def test_full_has_four_roles(self):
        assert len(UPGRADE_TYPE_ROLES["full"]) == 4
        assert "mcu" in UPGRADE_TYPE_ROLES["full"]
        assert "sail" in UPGRADE_TYPE_ROLES["full"]
        assert "switch" in UPGRADE_TYPE_ROLES["full"]
        assert "ufs" in UPGRADE_TYPE_ROLES["full"]

    def test_mcu_has_one_role(self):
        assert UPGRADE_TYPE_ROLES["mcu"] == ["mcu"]

    def test_switch_has_one_role(self):
        assert UPGRADE_TYPE_ROLES["switch"] == ["switch"]

    def test_soc_has_two_roles(self):
        assert UPGRADE_TYPE_ROLES["soc"] == ["sail", "ufs"]

    def test_all_keys_present(self):
        """所有四种升级类型都存在。"""
        assert set(UPGRADE_TYPE_ROLES.keys()) == {"full", "mcu", "switch", "soc"}
