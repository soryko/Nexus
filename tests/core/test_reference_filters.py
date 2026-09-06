"""B2b acceptance tests 1-14 and 17-21: reference filters on ``search``.

Each test calls the scenario the mutation controls in ``test_b2b_mutations.py`` run
against, so a control and the test it validates exercise the same code.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from b2b_scenarios import SCENARIOS


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario(tmp_path: Path, name: str) -> None:
    SCENARIOS[name](tmp_path)
