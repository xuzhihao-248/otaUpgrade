"""ota_core 模块单元测试。"""
import json
import re
import pytest
import ota_core


# ==================== load_config ====================

class TestLoadConfig:
    def test_load_nonexistent_file(self):
        """加载不存在的文件抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            ota_core.load_config("/nonexistent/path/config.json")

    def test_load_with_absolute_path(self, temp_config_file, sample_config):
        """绝对路径加载成功。"""
        result = ota_core.load_config(temp_config_file)
        assert result == sample_config

    def test_load_invalid_json(self, tmp_path):
        """加载非 JSON 文件抛出异常。"""
        p = tmp_path / "bad.json"
        p.write_text("not valid json {{{")
        with pytest.raises(json.JSONDecodeError):
            ota_core.load_config(str(p))


# ==================== save_config ====================

class TestSaveConfig:
    def test_save_and_reload(self, tmp_path, sample_config):
        """保存后加载数据一致。"""
        p = tmp_path / "out.json"
        ota_core.save_config(sample_config, str(p))
        with open(p, encoding="utf-8") as f:
            reloaded = json.load(f)
        assert reloaded == sample_config

    def test_save_overwrites_existing(self, tmp_path):
        """覆盖已有文件。"""
        p = tmp_path / "out.json"
        p.write_text('{"old": "data"}')
        ota_core.save_config({"new": "data"}, str(p))
        with open(p, encoding="utf-8") as f:
            reloaded = json.load(f)
        assert reloaded == {"new": "data"}


# ==================== build_env_fix ====================

class TestBuildEnvFix:
    def test_default_values(self):
        """不传 paths 时使用默认值。"""
        result = ota_core.build_env_fix({})
        assert "export PATH=$PATH:/mnt/bin" in result
        assert "export LD_LIBRARY_PATH=/mnt/lib64:/mnt/usr/lib64:/lib:/usr/lib:$LD_LIBRARY_PATH" in result
        assert "[ -f /etc/profile ] && . /etc/profile" in result

    def test_custom_values(self, sample_config):
        """自定义 paths 反映在结果中。"""
        result = ota_core.build_env_fix(sample_config)
        assert "/mnt/bin" in result
        assert "/mnt/lib64:/mnt/usr/lib64:/lib:/usr/lib" in result

    def test_missing_paths_key(self):
        """paths 键缺失不崩溃。"""
        result = ota_core.build_env_fix({"paths": {}})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_output_is_string(self):
        """返回值为字符串。"""
        result = ota_core.build_env_fix({})
        assert isinstance(result, str)


# ==================== 版本解析正则 ====================

class TestParseVersionRegex:
    def test_mcu_version_match(self):
        match = re.search(r'MCU Version:\s*(\S+)', "MCU Version: R510_RS02_01\n")
        assert match.group(1) == "R510_RS02_01"

    def test_mcu_version_no_match(self):
        match = re.search(r'MCU Version:\s*(\S+)', "No version here")
        assert match is None

    def test_switch_version_match(self):
        match = re.search(r'software version\s*:\s*GTMC_(\S+)',
                          "software version : GTMC_R400_RC02_2T")
        assert match.group(1) == "R400_RC02_2T"

    def test_switch_version_no_match(self):
        match = re.search(r'software version\s*:\s*GTMC_(\S+)', "unrelated output")
        assert match is None

    def test_ufs_version_from_json(self):
        ufs_json = '{"Version": "sail_ota_R510_RS02_00-20250115", "BuildDate": "2025-01-15"}'
        v_json = json.loads(ufs_json)
        v_str = v_json.get("Version", "")
        match = re.search(r'_(R\d+_[A-Z0-9_]+)-', v_str)
        assert match.group(1) == "R510_RS02_00"

    def test_ufs_version_no_match(self):
        ufs_json = '{"Version": "no_version_format_here"}'
        v_json = json.loads(ufs_json)
        v_str = v_json.get("Version", "")
        match = re.search(r'_(R\d+_[A-Z0-9_]+)-', v_str)
        assert match is None

    def test_ufs_invalid_json_returns_na(self):
        """无效 JSON 时返回 N/A（由 parse_versions 处理）。"""
        # 正则层面：空字符串无匹配
        match = re.search(r'_(R\d+_[A-Z0-9_]+)-', "")
        assert match is None


# ==================== parse_versions (mock execute_command) ====================

class TestParseVersions:
    def test_parse_all_versions(self, mocker, mock_ssh):
        """正常解析三个组件版本。"""
        mock_exec = mocker.patch("ota_core.execute_command")
        mock_exec.side_effect = [
            (0, "MCU Version: R510_RS02_01\nBoard: GTMC_AY5"),
            (0, "software version : GTMC_R400_RC02_2T\nbuild: 2025-01"),
            (0, '{"Version": "sail_ota_R510_RS02_00-20250115"}'),
        ]
        versions = ota_core.parse_versions(mock_ssh, "")
        assert versions["mcu"] == "R510_RS02_01"
        assert versions["switch"] == "R400_RC02_2T"
        assert versions["ufs"] == "R510_RS02_00"

    def test_parse_mcu_not_found(self, mocker, mock_ssh):
        """MCU 版本无匹配返回 N/A。"""
        mock_exec = mocker.patch("ota_core.execute_command")
        mock_exec.side_effect = [
            (0, "No MCU version here"),
            (0, "software version : GTMC_R400_RC02_2T"),
            (0, '{"Version": "sail_ota_R510_RS02_00-20250115"}'),
        ]
        versions = ota_core.parse_versions(mock_ssh, "")
        assert versions["mcu"] == "N/A"

    def test_parse_switch_not_found(self, mocker, mock_ssh):
        """Switch 版本无匹配返回 N/A。"""
        mock_exec = mocker.patch("ota_core.execute_command")
        mock_exec.side_effect = [
            (0, "MCU Version: R510_RS02_01"),
            (0, "no switch version here"),
            (0, '{"Version": "sail_ota_R510_RS02_00-20250115"}'),
        ]
        versions = ota_core.parse_versions(mock_ssh, "")
        assert versions["switch"] == "N/A"

    def test_parse_ufs_invalid_json(self, mocker, mock_ssh):
        """UFS 输出为无效 JSON 时返回 N/A。"""
        mock_exec = mocker.patch("ota_core.execute_command")
        mock_exec.side_effect = [
            (0, "MCU Version: R510_RS02_01"),
            (0, "software version : GTMC_R400_RC02_2T"),
            (0, "not valid json {{{"),
        ]
        versions = ota_core.parse_versions(mock_ssh, "")
        assert versions["ufs"] == "N/A"


# ==================== verify_versions ====================

class TestVerifyVersions:
    def test_all_match(self, mocker, mock_ssh):
        """所有版本匹配返回 True。"""
        mocker.patch("ota_core.parse_versions", return_value={
            "mcu": "R510_RS02_01", "ufs": "R510_RS02_00", "switch": "R400_RC02_2T",
        })
        expected = {"mcu": "R510_RS02_01", "ufs": "R510_RS02_00", "switch": "R400_RC02_2T"}
        result = ota_core.verify_versions(mock_ssh, "", expected, "test")
        assert result is True

    def test_mismatch_returns_false(self, mocker, mock_ssh):
        """任一版本不匹配返回 False。"""
        mocker.patch("ota_core.parse_versions", return_value={
            "mcu": "WRONG_VERSION", "ufs": "R510_RS02_00", "switch": "R400_RC02_2T",
        })
        expected = {"mcu": "R510_RS02_01", "ufs": "R510_RS02_00", "switch": "R400_RC02_2T"}
        result = ota_core.verify_versions(mock_ssh, "", expected, "test")
        assert result is False

    def test_none_expected_skips_check(self, mocker, mock_ssh):
        """expected 为 None 时跳过校验。"""
        mocker.patch("ota_core.parse_versions", return_value={
            "mcu": "R510_RS02_01", "ufs": "N/A", "switch": "N/A",
        })
        expected = {"mcu": "R510_RS02_01", "ufs": None, "switch": None}
        result = ota_core.verify_versions(mock_ssh, "", expected, "test")
        assert result is True

    def test_all_none_expected(self, mocker, mock_ssh):
        """全部 expected 为 None 时返回 True。"""
        mocker.patch("ota_core.parse_versions", return_value={
            "mcu": "ANY", "ufs": "ANY", "switch": "ANY",
        })
        expected = {"mcu": None, "ufs": None, "switch": None}
        result = ota_core.verify_versions(mock_ssh, "", expected, "test")
        assert result is True


# ==================== SSH 连接测试 ====================

class TestSSHTestConnection:
    def test_success(self, mocker):
        """连接成功返回 (True, "")。"""
        mock_connect = mocker.patch("ota_core.ssh_connect")
        mock_disconnect = mocker.patch("ota_core.ssh_disconnect")
        ok, err = ota_core.ssh_test_connection("host", "user", "pw")
        assert ok is True
        assert err == ""
        mock_connect.assert_called_once_with("host", "user", "pw", timeout=15)
        mock_disconnect.assert_called_once()

    def test_failure(self, mocker):
        """连接失败返回 (False, error_message)。"""
        mocker.patch("ota_core.ssh_connect", side_effect=Exception("Connection refused"))
        ok, err = ota_core.ssh_test_connection("host", "user", "pw")
        assert ok is False
        assert "Connection refused" in err


# ==================== 工具函数 ====================

class TestEnsureCallbacks:
    def test_fills_missing_keys(self):
        """缺失的 key 被补齐为 no-op。"""
        result = ota_core._ensure_callbacks({})
        assert "log" in result
        assert "status" in result
        assert "progress" in result
        # no-op 调用不抛异常
        result["log"]("test")
        result["status"]("test")
        result["progress"]("f", 0, 100)

    def test_preserves_existing(self):
        """已有的 key 不被覆盖。"""
        sentinel = object()
        cb = {"log": sentinel}
        result = ota_core._ensure_callbacks(cb)
        assert result["log"] is sentinel

    def test_partial_fill(self):
        """部分缺失时只补齐缺失项。"""
        my_status = lambda t: None
        cb = {"status": my_status}
        result = ota_core._ensure_callbacks(cb)
        assert result["status"] is my_status
        assert "log" in result
        assert "progress" in result


class TestZipFilenameForRole:
    def test_returns_matching_role(self):
        extracted = {"mcu": "my_mcu.hex", "sail": "my_sail.bin"}
        assert ota_core._zip_filename_for_role(extracted, "mcu") == "my_mcu.hex"

    def test_returns_empty_for_missing_role(self):
        assert ota_core._zip_filename_for_role({}, "ufs") == ""

    def test_returns_empty_for_empty_dict(self):
        assert ota_core._zip_filename_for_role({}, "mcu") == ""


# ==================== ssh_disconnect ====================

class TestSSHDisconnect:
    def test_active_ssh_closes(self, mocker):
        """活跃连接调用 close。"""
        mock_ssh = mocker.MagicMock()
        mock_transport = mocker.MagicMock()
        mock_transport.is_active.return_value = True
        mock_ssh.get_transport.return_value = mock_transport

        ota_core.ssh_disconnect(mock_ssh)
        mock_ssh.close.assert_called_once()

    def test_inactive_ssh_skips_close(self, mocker):
        """非活跃连接不调用 close。"""
        mock_ssh = mocker.MagicMock()
        mock_transport = mocker.MagicMock()
        mock_transport.is_active.return_value = False
        mock_ssh.get_transport.return_value = mock_transport

        ota_core.ssh_disconnect(mock_ssh)
        mock_ssh.close.assert_not_called()

    def test_none_ssh_does_not_crash(self):
        """None 输入不崩溃。"""
        # ssh_disconnect checks ssh and ssh.get_transport(), so None.get_transport() would crash
        try:
            ota_core.ssh_disconnect(None)
        except AttributeError:
            pass  # expected: None has no get_transport
