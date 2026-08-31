import sys
from pathlib import Path

# Fixtures are imported by bare name from the test modules.
sys.path.insert(0, str(Path(__file__).parent))


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: solves the full sample dataset")
