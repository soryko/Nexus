"""B2a acceptance tests 3, 5, 6, 7, 8, 9, 10 and 11 against the real git adapter."""
from __future__ import annotations

from pathlib import Path

import pytest
from b2a_scenarios import SCENARIOS


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario(tmp_path: Path, name: str) -> None:
    SCENARIOS[name](tmp_path)
