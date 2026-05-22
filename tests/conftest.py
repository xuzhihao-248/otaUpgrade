"""pytest 共享 fixtures 与配置。"""
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
def temp_config_file(tmp_path, sample_config):
    """写入临时 config.json，返回路径。"""
    p = tmp_path / "config.json"
    p.write_text(json.dumps(sample_config, indent=4), encoding="utf-8")
    return str(p)


@pytest.fixture
def mock_ssh(mocker):
    """mock paramiko.SSHClient。"""
    mock_client = mocker.MagicMock()
    mock_transport = mocker.MagicMock()
    mock_transport.is_active.return_value = True
    mock_client.get_transport.return_value = mock_transport

    mock_stdout = mocker.MagicMock()
    mock_stdout.channel.exit_status_ready.return_value = True
    mock_stdout.channel.recv_exit_status.return_value = 0
    mock_stdout.channel.recv_ready.return_value = False
    mock_stdout.read.return_value = b""
    mock_stdout.channel.recv.return_value = b""

    mock_stderr = mocker.MagicMock()
    mock_stderr.channel.recv_stderr_ready.return_value = False
    mock_stderr.read.return_value = b""

    mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)
    return mock_client


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
