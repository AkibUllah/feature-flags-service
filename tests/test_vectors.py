"""Shared evaluation vectors (tests/vectors/evaluation.json).

The same file is replayed by the Python SDK tests and the TypeScript SDK tests, so every
place that evaluates or serves a flag agrees on variation, value and reason.
"""

import hashlib
import json
from pathlib import Path

import pytest

from feature_flags.core.bucketing import bucket
from feature_flags.core.clauses import Context
from feature_flags.core.evaluate import evaluate
from feature_flags.services.payloads import client_payload

VECTORS = json.loads((Path(__file__).parent / "vectors" / "evaluation.json").read_text())
CASES = VECTORS["cases"]


def context_of(case) -> Context:
    return Context(case["context"]["key"], case["context"]["attributes"])


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_core_evaluator_matches_vectors(case):
    flags = VECTORS["flags"]
    result = evaluate(flags[case["flag"]], context_of(case), VECTORS["segments"], flags)
    as_dict = result.as_dict()
    assert {k: as_dict[k] for k in ("variation", "value", "reason")} == case["expected"]


def test_client_payload_serves_vector_results():
    payload = {"environment": "prod", "version": 1, **VECTORS}
    for case in CASES:
        served = client_payload(payload, context_of(case))["flags"][case["flag"]]
        assert {k: served[k] for k in ("variation", "value", "reason")} == case["expected"]


@pytest.mark.parametrize("entry", VECTORS["hashes"], ids=lambda e: e["input"])
def test_bucketing_hash_is_pinned(entry):
    digest = hashlib.sha256(entry["input"].encode()).hexdigest()
    assert int(digest[:15], 16) / 16**15 == entry["point"]
    assert bucket(entry["flag_key"], entry["salt"], Context(entry["value"])) == entry["point"]


def test_vectors_cover_every_new_reason():
    reasons = [c["expected"]["reason"] for c in CASES]
    assert {r["kind"] for r in reasons} >= {"PREREQUISITE_FAILED", "ERROR", "RULE_MATCH"}
    assert any(r.get("in_holdout") for r in reasons)
    assert any(r.get("in_rollout") and r.get("experiment") for r in reasons)
