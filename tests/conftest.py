import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def catalog_items():
    return json.loads((FIXTURES / "catalog_items.json").read_text())["items"]
