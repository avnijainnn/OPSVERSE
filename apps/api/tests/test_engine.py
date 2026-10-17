import pytest
from pydantic import ValidationError

from app import store
from app.agents import RelatedEventsArgs
from app.agents import _model_explanation
from app.domain import PolicyError, apply, metrics, project
from app.engine import compare, decide, inject, seed_world, step


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init()
    with store.connection() as connection:
        yield connection


def test_same_seed_and_replay(db):
    left = seed_world(db, 42)
    right = seed_world(db, 42)
    a, b = store.events(db, left), store.events(db, right)
    assert [(e["event_type"], e["payload"]) for e in a] == [(e["event_type"], e["payload"]) for e in b]
    assert project(a) == project(b)
    assert [e["sequence_number"] for e in a] == list(range(1, len(a) + 1))
    assert project(a[:10]) != project(a)


def test_live_reader_can_read_while_a_writer_holds_a_branch_transaction(db):
    run_id = seed_world(db)
    db.commit()
    with store.connection() as writer:
        store.append(writer, run_id, "DemandSurgeObserved", {"sku": "SKU-001"}, "product", "SKU-001")
        with store.connection(write=False) as reader:
            ledger = store.events(reader, run_id)
            assert ledger
            assert ledger[-1]["event_type"] == "OperationalExpenseRecorded"


def test_invalid_external_stock_is_evidence_only(db):
    run_id = seed_world(db)
    before = project(store.events(db, run_id))
    data = inject(db, run_id, "damaged_return")
    after = project(store.events(db, run_id))
    sku = data["affected_entities"]["sku"]
    assert after["stock"][sku]["sellable"] == before["stock"][sku]["sellable"]
    assert after["stock"][sku]["inspection_pending"] == 1
    assert after["rejected_observations"] == 1
    assert data["evidence_event_ids"]
    assert len(data["traces"][0]["tool_calls"]) == 2
    assert {call["tool_name"] for call in data["traces"][0]["tool_calls"]} == {"get_related_events", "get_business_state"}
    assert data["traces"][0]["mode"] == "deterministic_demo"
    with pytest.raises(PolicyError):
        store.append(db, run_id, "RecoveryVerified", {"incident_id": data["incident_id"],
            "return_id": data["affected_entities"]["return_id"], "passed": True}, "incident", data["incident_id"])


def test_malformed_tool_arguments_rejected():
    with pytest.raises(ValidationError):
        RelatedEventsArgs.model_validate({"run_id": "run", "event_ids": []})


def test_model_cannot_cite_unsupported_evidence(monkeypatch):
    import json
    from app import agents

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return json.dumps({"choices": [{"message": {"content": json.dumps({
                "summary": "A plausible but unsupported explanation.",
                "evidence_event_ids": ["invented-event"]})}}]}).encode()

    monkeypatch.setenv("LLM_API_KEY", "fake-test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(agents.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(ValueError, match="unsupported evidence"):
        _model_explanation("Supported deterministic finding", ["real-event"])


def test_branch_isolation_and_approval(db):
    parent = seed_world(db)
    data = inject(db, parent, "damaged_return")
    original = store.events(db, parent)
    result = compare(db, data["incident_id"])
    assert store.events(db, parent) == original
    assert result["baseline_metrics"]["open_incidents"] == 1
    assert result["recovery_metrics"]["open_incidents"] == 0
    assert result["recovery_metrics"]["active_returns"] == 0
    resolved = decide(db, data["incident_id"], True, len(original))
    assert resolved["status"] == "resolved"
    assert resolved["verification"][0]["passed"] == 1
    assert project(store.events(db, parent))["returns"][data["affected_entities"]["return_id"]]["status"] == "quarantined"


def test_settlement_cross_period_arithmetic(db):
    run_id = seed_world(db)
    data = inject(db, run_id, "settlement_mismatch")
    state = project(store.events(db, run_id))
    settlement = state["settlements"][data["affected_entities"]["settlement_id"]]
    assert settlement["expected_paise"] == settlement["received_paise"] == 87640
    assert settlement["reconciled"]
    assert metrics(state)["unreconciled_paise"] == 0


def test_missing_payout_stays_open_after_dispute(db):
    run_id = seed_world(db)
    data = inject(db, run_id, "missing_payout")
    state = project(store.events(db, run_id))
    assert metrics(state)["unreconciled_paise"] == 11000
    result = compare(db, data["incident_id"])
    assert result["baseline_metrics"]["payout_disputes"] == 0
    assert result["recovery_metrics"]["payout_disputes"] == 1
    result = decide(db, data["incident_id"], True, len(store.events(db, run_id)))
    assert result["status"] == "investigating"
    assert result["verification"][0]["passed"] == 1
    assert metrics(project(store.events(db, run_id)))["unreconciled_paise"] == 11000


def test_partial_refund_reduces_expected_settlement(db):
    run_id = seed_world(db)
    payment_id = "PAY-0004"
    payment = project(store.events(db, run_id))["payments"][payment_id]
    partial = payment["captured_paise"] // 2
    store.append(db, run_id, "RefundIssued", {"payment_id": payment_id,
        "amount_paise": partial}, "payment", payment_id)
    adjustment = store.append(db, run_id, "PriorPeriodRefundRecorded", {"adjustment_id": "ADJ-PARTIAL",
        "amount_paise": 100, "previous_settlement_id": "SET-OLD"}, "refund", "ADJ-PARTIAL")
    expected = payment["captured_paise"] - partial - payment["fee_paise"] - payment["fee_tax_paise"] - 100
    store.append(db, run_id, "SettlementGenerated", {"settlement_id": "SET-PARTIAL",
        "payment_id": payment_id, "prior_refund_paise": 100,
        "adjustment_id": "ADJ-PARTIAL", "expected_paise": expected},
        "settlement", "SET-PARTIAL", adjustment["event_id"])
    store.append(db, run_id, "SettlementReceived", {"settlement_id": "SET-PARTIAL",
        "amount_paise": expected}, "settlement", "SET-PARTIAL")
    assert project(store.events(db, run_id))["settlements"]["SET-PARTIAL"]["reconciled"]


def test_supplier_comparison_uses_isolated_matched_future(db):
    run_id = seed_world(db)
    data = inject(db, run_id, "supplier_delay")
    result = compare(db, data["incident_id"])
    assert result["recovery_metrics"]["expenses_paise"] - result["baseline_metrics"]["expenses_paise"] == 1500000
    assert result["recovery_metrics"]["available_stock"] > result["baseline_metrics"]["available_stock"]
    assert result["recovery_metrics"]["unfilled_demand"] < result["baseline_metrics"]["unfilled_demand"]
    assert len(store.events(db, run_id)) < len(store.events(db, result["recovery_run_id"]))


def test_idempotency_and_rejected_action(db):
    run_id = seed_world(db)
    order = store.events(db, run_id)[-1]
    event = store.append(db, run_id, "DemandSurgeObserved", {"sku": "SKU-001"}, "product", "SKU-001", idempotency_key="webhook-1")
    duplicate = store.append(db, run_id, "DemandSurgeObserved", {"sku": "SKU-001"}, "product", "SKU-001", idempotency_key="webhook-1")
    assert event == duplicate
    data = inject(db, run_id, "damaged_return")
    before = project(store.events(db, run_id))
    rejected = decide(db, data["incident_id"], False, len(store.events(db, run_id)))
    after = project(store.events(db, run_id))
    assert rejected["status"] == "rejected"
    assert after["stock"] == before["stock"]
    assert after["returns"] == before["returns"]
    assert order["event_id"] != event["event_id"]


def test_refund_cannot_exceed_capture(db):
    run_id = seed_world(db)
    payment_id = next(iter(project(store.events(db, run_id))["payments"]))
    with pytest.raises(PolicyError):
        store.append(db, run_id, "RefundIssued", {"payment_id": payment_id, "amount_paise": 10**9}, "payment", payment_id)


def test_step_is_deterministic(db):
    left = seed_world(db, 11)
    right = seed_world(db, 11)
    step(db, left)
    step(db, right)
    assert project(store.events(db, left)) == project(store.events(db, right))
