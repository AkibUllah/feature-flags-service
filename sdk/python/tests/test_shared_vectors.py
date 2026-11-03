"""Replay the shared evaluation vectors through FlagClient's local evaluation."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from flagclient import FlagClient

VECTORS = json.loads(
    (Path(__file__).resolve().parents[3] / "tests" / "vectors" / "evaluation.json").read_text()
)
CASES = VECTORS["cases"]


@pytest.fixture(scope="module")
def client():
    payload = {"environment": "prod", "version": 1, **VECTORS}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    client = FlagClient("srv-test", "http://flags.local", http=httpx.Client(transport=transport))
    assert client.refresh()
    yield client
    client.close()


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_local_evaluation_matches_vectors(client, case):
    context = {"key": case["context"]["key"], **case["context"]["attributes"]}
    result = client.evaluate(case["flag"], context).as_dict()
    assert {k: result[k] for k in ("variation", "value", "reason")} == case["expected"]
    expected = case["expected"]
    default = "fallback"
    served = client.variation(case["flag"], context, default)
    assert served == (default if expected["reason"]["kind"] == "ERROR" else expected["value"])
