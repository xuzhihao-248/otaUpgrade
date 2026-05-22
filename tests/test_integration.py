"""集成测试 — 需要真实设备连接。

运行: uv run pytest tests/test_integration.py -v --run-integration
"""
import os
import pytest
import ota_core


def _get_live_config():
    """从环境变量或 config.json 读取真实设备配置。"""
    config = ota_core.load_config()
    return {
        "host": os.environ.get("OTA_TEST_HOST", config["device"]["host"]),
        "user": os.environ.get("OTA_TEST_USER", config["device"]["user"]),
        "pw": os.environ.get("OTA_TEST_PW", config["device"]["pw"]),
        "remote_dir": config["paths"]["remote_ota_dir"],
    }


# ==================== SSH 连接 ====================

@pytest.mark.integration
class TestSSHConnection:
    def test_connect_and_disconnect(self):
        """基本连接/断开。"""
        cfg = _get_live_config()
        ssh = ota_core.ssh_connect(cfg["host"], cfg["user"], cfg["pw"])
        assert ssh.get_transport().is_active()
        ota_core.ssh_disconnect(ssh)

    def test_test_connection_helper(self):
        """ssh_test_connection 包装函数。"""
        cfg = _get_live_config()
        ok, err = ota_core.ssh_test_connection(cfg["host"], cfg["user"], cfg["pw"])
        assert ok is True
        assert err == ""

    def test_bad_credentials_fails(self):
        """错误密码返回失败。"""
        cfg = _get_live_config()
        ok, err = ota_core.ssh_test_connection(cfg["host"], cfg["user"], "wrong_password_xyz")
        assert ok is False


# ==================== 远程命令 ====================

@pytest.mark.integration
class TestRemoteCommands:
    @pytest.fixture(scope="class")
    def live_ssh(self):
        cfg = _get_live_config()
        ssh = ota_core.ssh_connect(cfg["host"], cfg["user"], cfg["pw"])
        yield ssh
        ota_core.ssh_disconnect(ssh)

    def test_simple_echo(self, live_ssh):
        """执行 echo 并获取输出。"""
        code, output = ota_core.execute_command(live_ssh, "echo hello_test_12345", "", timeout=10)
        assert code == 0
        assert "hello_test_12345" in output

    def test_env_fix_ota_tool(self, live_ssh):
        """环境变量后可找到 ota_tool。"""
        config = {
            "paths": {
                "bin_path": "/mnt/bin",
                "lib_paths": "/mnt/lib64:/mnt/usr/lib64:/lib:/usr/lib",
            }
        }
        env_fix = ota_core.build_env_fix(config)
        code, _ = ota_core.execute_command(live_ssh, "which ota_tool", env_fix, timeout=10)
        assert code == 0

    def test_timeout_raises(self, live_ssh):
        """超时命令抛出 TimeoutError。"""
        with pytest.raises(TimeoutError):
            ota_core.execute_command(live_ssh, "sleep 999", "", timeout=3)


# ==================== 版本读取 ====================

@pytest.mark.integration
class TestVersionParsing:
    @pytest.fixture(scope="class")
    def live_ssh(self):
        cfg = _get_live_config()
        ssh = ota_core.ssh_connect(cfg["host"], cfg["user"], cfg["pw"])
        yield ssh
        ota_core.ssh_disconnect(ssh)

    def test_parse_versions_returns_dict(self, live_ssh):
        """parse_versions 返回正确的 dict 结构。"""
        config = {
            "paths": {
                "bin_path": "/mnt/bin",
                "lib_paths": "/mnt/lib64:/mnt/usr/lib64:/lib:/usr/lib",
            }
        }
        env_fix = ota_core.build_env_fix(config)
        versions = ota_core.parse_versions(live_ssh, env_fix)
        assert "mcu" in versions
        assert "ufs" in versions
        assert "switch" in versions
        for k, v in versions.items():
            assert v, f"{k} 版本号为空"


# ==================== 文件操作 ====================

@pytest.mark.integration
class TestFileOperations:
    @pytest.fixture(scope="class")
    def live_ssh(self):
        cfg = _get_live_config()
        ssh = ota_core.ssh_connect(cfg["host"], cfg["user"], cfg["pw"])
        yield ssh
        ota_core.ssh_disconnect(ssh)

    def test_upload_and_delete(self, live_ssh, tmp_path):
        """上传文件，验证存在，然后删除。"""
        test_file = tmp_path / "test_upload.txt"
        test_file.write_text("ota_test_content_12345")

        cfg = _get_live_config()
        remote_dir = cfg["remote_dir"]
        remote_path = f"{remote_dir}/test_upload.txt"

        ota_core.upload_file(live_ssh, str(test_file), remote_path)

        # 验证文件存在
        code, out = ota_core.execute_command(
            live_ssh, f"test -f {remote_path} && echo EXISTS", "", timeout=10
        )
        assert "EXISTS" in out, f"文件未上传成功: {out}"

        # 清理
        ota_core.delete_remote_files(live_ssh, remote_dir, ["test_upload.txt"])
        code, out = ota_core.execute_command(
            live_ssh, f"test -f {remote_path} && echo EXISTS || echo GONE", "", timeout=10
        )
        assert "GONE" in out, f"文件未删除: {out}"
