"""These are protocol clients, not Claude Code or Codex sessions."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('a13', Path(__file__).resolve().parents[2]/'tools/check_memory_v2.py')
a13 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a13)


@pytest.mark.parametrize('fmt', ['sha1', 'sha256'])
def test_fresh_protocol_receiver(fmt):
    evidence = a13.check(fmt)
    assert evidence['R'] != evidence['M'] and len(evidence['retrieved_refs']) == 4
