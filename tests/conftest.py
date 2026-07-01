"""
Pytest 配置文件

配置测试环境和共享 fixtures
"""

import pytest
import sys
import os

# 添加项目根目录到 Python 路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def pytest_configure(config):
    """Pytest 配置钩子"""
    config.addinivalue_line(
        "markers", "unit: 单元测试"
    )
    config.addinivalue_line(
        "markers", "integration: 集成测试"
    )
    config.addinivalue_line(
        "markers", "effectiveness: 效果验证测试"
    )
    config.addinivalue_line(
        "markers", "slow: 慢速测试"
    )


@pytest.fixture(scope="session")
def project_root_path():
    """项目根目录路径"""
    return project_root


@pytest.fixture(scope="session")
def test_data_dir(project_root_path):
    """测试数据目录"""
    return os.path.join(project_root_path, "tests", "data")
