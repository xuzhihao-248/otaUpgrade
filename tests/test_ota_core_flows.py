"""升级流程测试 — 通过 mock SSH 验证命令序列与编排逻辑。"""
import pytest
import ota_core


def _make_mock_ssh(mocker):
    """构造一个带活跃 transport 的 mock SSH client。"""
    mock_ssh = mocker.MagicMock()
    mock_transport = mocker.MagicMock()
    mock_transport.is_active.return_value = True
    mock_ssh.get_transport.return_value = mock_transport
    return mock_ssh


# ==================== run_full_upgrade ====================

class TestFullUpgradeFlow:
    def test_direct_mode_first_pass_commands(self, mocker, sample_config, callbacks):
        """direct 模式第一面升级包含 update-mcu 和 switch_bcm_flasher。"""
        mock_ssh = _make_mock_ssh(mocker)
        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.ssh_disconnect")
        mocker.patch("ota_core.upload_files_batch")
        mock_exec = mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=True)

        sample_config["mode"] = "direct"
        sample_config["firmware_files"] = {
            "mcu": "m.hex", "sail": "s.bin", "switch": "sw.img", "ufs": "u.bin",
        }

        ota_core.run_full_upgrade(sample_config, callbacks)

        all_cmds = [c[0][1] for c in mock_exec.call_args_list]
        assert any("update-mcu" in cmd for cmd in all_cmds)
        assert any("switch_bcm_flasher" in cmd for cmd in all_cmds)
        assert any("ota_proxy_app j" in cmd for cmd in all_cmds)
        assert any("ota_proxy_app k" in cmd for cmd in all_cmds)

    def test_direct_mode_two_passes(self, mocker, sample_config, callbacks):
        """direct 模式有两面升级。"""
        mock_ssh = _make_mock_ssh(mocker)
        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.ssh_disconnect")
        mocker.patch("ota_core.upload_files_batch")
        mock_exec = mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=True)

        sample_config["mode"] = "direct"
        sample_config["firmware_files"] = {
            "mcu": "m.hex", "sail": "s.bin", "switch": "sw.img", "ufs": "u.bin",
        }

        ota_core.run_full_upgrade(sample_config, callbacks)

        # update-mcu 出现两次（第一面 + 第二面）
        mcu_calls = [c[0][1] for c in mock_exec.call_args_list if "update-mcu" in c[0][1]]
        assert len(mcu_calls) == 2

    def test_zip_mode_uploads_and_extracts(self, mocker, sample_config, callbacks):
        """ZIP 模式上传压缩包并解压。"""
        mock_ssh = _make_mock_ssh(mocker)
        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.ssh_disconnect")
        mock_upload_zip = mocker.patch("ota_core.upload_zip_file", return_value="fw.zip")
        mock_extract = mocker.patch("ota_core.extract_zip")
        mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=True)
        mock_delete = mocker.patch("ota_core.delete_remote_files")

        sample_config["mode"] = "zip"
        sample_config["zip_file"] = "/tmp/firmware.zip"
        sample_config["extracted_files"] = {
            "mcu": "m.hex", "sail": "s.bin", "switch": "sw.img", "ufs": "u.bin",
        }

        mocker.patch("os.path.exists", return_value=True)
        ota_core.run_full_upgrade(sample_config, callbacks)

        assert mock_upload_zip.call_count >= 2  # 两面各上传一次
        assert mock_extract.call_count >= 2
        assert mock_delete.call_count >= 2  # 两面各清理一次

    def test_direct_mode_no_zip_cleanup(self, mocker, sample_config, callbacks):
        """direct 模式不调用 delete_remote_files（在 _do_cleanup_step 中用 execute_command）。"""
        mock_ssh = _make_mock_ssh(mocker)
        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.ssh_disconnect")
        mocker.patch("ota_core.upload_files_batch")
        mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=True)
        mock_delete = mocker.patch("ota_core.delete_remote_files")

        sample_config["mode"] = "direct"
        sample_config["firmware_files"] = {
            "mcu": "m.hex", "sail": "s.bin", "switch": "sw.img", "ufs": "u.bin",
        }

        ota_core.run_full_upgrade(sample_config, callbacks)

        mock_delete.assert_not_called()

    def test_verify_failure_raises(self, mocker, sample_config, callbacks):
        """版本校验失败抛出 RuntimeError。"""
        mock_ssh = _make_mock_ssh(mocker)
        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.ssh_disconnect")
        mocker.patch("ota_core.upload_files_batch")
        mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=False)

        sample_config["mode"] = "direct"
        sample_config["firmware_files"] = {
            "mcu": "m.hex", "sail": "s.bin", "switch": "sw.img", "ufs": "u.bin",
        }

        with pytest.raises(RuntimeError, match="版本号不匹配"):
            ota_core.run_full_upgrade(sample_config, callbacks)

    def test_upload_step_uploads_correct_files(self, mocker, sample_config, callbacks):
        """direct 模式上传了正确的文件列表。"""
        mock_ssh = _make_mock_ssh(mocker)
        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.ssh_disconnect")
        mock_upload = mocker.patch("ota_core.upload_files_batch")
        mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=True)

        sample_config["mode"] = "direct"
        sample_config["firmware_files"] = {
            "mcu": "m.hex", "sail": "s.bin", "switch": "sw.img", "ufs": "u.bin",
        }

        ota_core.run_full_upgrade(sample_config, callbacks)

        # 第一次上传（第一面）
        uploaded = mock_upload.call_args_list[0][0][3]
        assert "m.hex" in uploaded
        assert "s.bin" in uploaded
        assert "sw.img" in uploaded
        assert "u.bin" in uploaded


# ==================== run_mcu_upgrade ====================

class TestMCUUpgradeFlow:
    def test_mcu_two_pass_reboot(self, mocker, sample_config, callbacks):
        """MCU 升级有两面：两次 update-mcu + 两次 reboot。"""
        mock_ssh = _make_mock_ssh(mocker)
        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.ssh_disconnect")
        mocker.patch("ota_core.upload_files_batch")
        mock_exec = mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mock_reboot = mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=True)

        sample_config["mode"] = "direct"
        sample_config["firmware_files"] = {"mcu": "m.hex"}

        ota_core.run_mcu_upgrade(sample_config, callbacks)

        mcu_calls = [c[0][1] for c in mock_exec.call_args_list if "update-mcu" in c[0][1]]
        assert len(mcu_calls) == 2
        reboot_calls = [c[0][1] for c in mock_reboot.call_args_list if "reset-mcu-ota" in c[0][1]]
        assert len(reboot_calls) == 2

    def test_mcu_verbose_false(self, mocker, sample_config, callbacks):
        """MCU 升级命令 verbose=False。"""
        mock_ssh = _make_mock_ssh(mocker)
        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.ssh_disconnect")
        mocker.patch("ota_core.upload_files_batch")
        mock_exec = mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=True)

        sample_config["mode"] = "direct"
        sample_config["firmware_files"] = {"mcu": "m.hex"}

        ota_core.run_mcu_upgrade(sample_config, callbacks)

        # 检查 update-mcu 调用使用了 verbose=False
        mcu_calls = [c for c in mock_exec.call_args_list if "update-mcu" in c[0][1]]
        for call in mcu_calls:
            assert call[1]["verbose"] is False


# ==================== run_switch_upgrade ====================

class TestSwitchUpgradeFlow:
    def test_switch_flasher_command(self, mocker, sample_config, callbacks):
        """Switch 升级包含 switch_bcm_flasher 命令。"""
        mock_ssh = _make_mock_ssh(mocker)
        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.ssh_disconnect")
        mocker.patch("ota_core.upload_files_batch")
        mock_exec = mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mock_reboot = mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=True)

        sample_config["mode"] = "direct"
        sample_config["firmware_files"] = {"switch": "sw.img"}

        ota_core.run_switch_upgrade(sample_config, callbacks)

        all_cmds = [c[0][1] for c in mock_exec.call_args_list]
        assert any("switch_bcm_flasher -f" in cmd for cmd in all_cmds)
        # Switch 只有一面升级
        reboot_calls = [c[0][1] for c in mock_reboot.call_args_list]
        assert len(reboot_calls) == 1

    def test_switch_single_pass_only(self, mocker, sample_config, callbacks):
        """Switch 只有一面升级（一次 reboot）。"""
        mock_ssh = _make_mock_ssh(mocker)
        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.ssh_disconnect")
        mocker.patch("ota_core.upload_files_batch")
        mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mock_reboot = mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=True)

        sample_config["mode"] = "direct"
        sample_config["firmware_files"] = {"switch": "sw.img"}

        ota_core.run_switch_upgrade(sample_config, callbacks)

        assert mock_reboot.call_count == 1


# ==================== run_soc_upgrade ====================

class TestSoCUpgradeFlow:
    def test_soc_commands(self, mocker, sample_config, callbacks):
        """SoC 升级包含 ota_proxy_app 相关命令。"""
        mock_ssh = _make_mock_ssh(mocker)
        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.ssh_disconnect")
        mocker.patch("ota_core.upload_files_batch")
        mock_exec = mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=True)

        sample_config["mode"] = "direct"
        sample_config["firmware_files"] = {"sail": "s.bin", "ufs": "u.bin"}

        ota_core.run_soc_upgrade(sample_config, callbacks)

        all_cmds = [c[0][1] for c in mock_exec.call_args_list]
        assert any("ota_proxy_app b" in cmd for cmd in all_cmds)
        assert any("ota_proxy_app j" in cmd for cmd in all_cmds)
        assert any("ota_proxy_app s" in cmd for cmd in all_cmds)
        assert any("ota_proxy_app k" in cmd for cmd in all_cmds)
        assert any("ota_proxy_app m" in cmd for cmd in all_cmds)

    def test_soc_two_passes(self, mocker, sample_config, callbacks):
        """SoC 升级有两面。"""
        mock_ssh = _make_mock_ssh(mocker)
        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.ssh_disconnect")
        mocker.patch("ota_core.upload_files_batch")
        mock_exec = mocker.patch("ota_core.execute_command", return_value=(0, ""))
        mock_reboot = mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=True)

        sample_config["mode"] = "direct"
        sample_config["firmware_files"] = {"sail": "s.bin", "ufs": "u.bin"}

        ota_core.run_soc_upgrade(sample_config, callbacks)

        # 两面各一次 reboot
        reboot_calls = [c[0][1] for c in mock_reboot.call_args_list if "reset-mcu-ota" in c[0][1]]
        assert len(reboot_calls) == 2
        # ota_proxy_app m 在每面后各调用一次
        m_calls = [c[0][1] for c in mock_exec.call_args_list if "ota_proxy_app m" == c[0][1]]
        assert len(m_calls) == 2


# ==================== 错误处理 ====================

class TestCommandFailure:
    def test_execute_command_failure_raises(self, mocker, sample_config, callbacks):
        """execute_command 返回非零时抛出 RuntimeError。"""
        mock_ssh = _make_mock_ssh(mocker)
        mocker.patch("ota_core.ssh_connect", return_value=mock_ssh)
        mocker.patch("ota_core.ssh_disconnect")
        mocker.patch("ota_core.upload_files_batch")
        mocker.patch("ota_core.execute_command", return_value=(1, "some error"))
        mocker.patch("ota_core.execute_reboot_command", return_value=(0, ""))
        mocker.patch("ota_core.wait_reconnect", return_value=mock_ssh)
        mocker.patch("ota_core.verify_versions", return_value=True)

        sample_config["mode"] = "direct"
        sample_config["firmware_files"] = {
            "mcu": "m.hex", "sail": "s.bin", "switch": "sw.img", "ufs": "u.bin",
        }

        with pytest.raises(RuntimeError, match="执行失败"):
            ota_core.run_full_upgrade(sample_config, callbacks)
