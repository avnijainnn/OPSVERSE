"""The saved ledger must explain every modeled quantity and cost."""
from copy import deepcopy

from hypothesis import given, settings, strategies as st

from app.live_simulation import (
    METRICS, explain_metric_difference, find_first_branch_divergence, simulate_branches,
)

STATE = {
    "inventory": [
        {"id": "wms", "sku": "SKU", "sellable": 8, "observed_at": "2026-01-01"},
        {"id": "erp", "sku": "SKU", "sellable": 4, "observed_at": "2026-01-01"},
    ],
    "products": [{"id": "product", "sku": "SKU", "price_paise": 12537}],
}


def test_zero_delay_requires_result_and_never_duplicates_stock():
    passed = simulate_branches(STATE, "SKU", 1, 5, inspection_result="passed",
                               inspection_delay=0, return_processing_time=0)[1]
    failed = simulate_branches(STATE, "SKU", 1, 5, inspection_result="failed",
                               inspection_delay=0, return_processing_time=0)[1]
    held = simulate_branches(STATE, "SKU", 1, 5, inspection_result="hold")[1]
    assert passed["inspection_state"] == "RELEASED"
    assert any(event["event"] == "HypotheticalInspectionRelease" for event in passed["events"])
    assert failed["inspection_state"] == "QUARANTINED"
    assert not any(event["event"] == "HypotheticalInspectionRelease" for event in failed["events"])
    assert held["recovery_completed_at"] is None
    assert passed["recovery_completed_at"] == 0
    assert passed["recovery_duration"] == 0
    assert passed["ending_sellable"] + passed["fulfilled_units"] == 8


def test_delivery_uses_hours_and_beyond_horizon_is_unresolved():
    branches = simulate_branches(STATE, "SKU", 4, 3, supplier_lead_time=100,
                                 restock_units=7, inspection_result="failed")
    conservative = branches[1]
    assert conservative["inventory"]["ordered_inbound"] == 7
    assert conservative["inventory"]["received_inbound"] == 0
    assert conservative["recovery_completed_at"] is None
    assert not any(event["event"] == "HypotheticalStockReceipt" for event in conservative["events"])
    alternative = branches[2]
    assert alternative["inventory"]["received_inbound"] == 0
    assert alternative["inventory"]["ordered_inbound"] == 7
    assert all(event["time_hours"] <= alternative["horizon_hours"] for event in alternative["events"])


def test_disruption_and_multiple_locations_keep_existing_reserved_stock_out_of_supply():
    state = deepcopy(STATE)
    for row in state["inventory"]:
        row["location"] = "Main"
        row["reserved"] = 2
        row["quarantined"] = 1
    state["inventory"].append({"id": "other", "sku": "SKU", "location": "Annex",
                               "sellable": 3, "reserved": 1, "quarantined": 0,
                               "observed_at": "2026-01-01"})
    branch = simulate_branches(state, "SKU", 3, 2, disruption_units=6)[0]
    assert branch["starting_sellable"] == 5
    assert branch["inventory"]["reserved"] == 3
    assert branch["inventory"]["quarantined"] == 1
    assert branch["fulfilled_units"] + branch["ending_sellable"] == 5


def test_unavailable_supplier_creates_no_inbound_stock_or_fake_recovery():
    branches = simulate_branches(STATE, "SKU", 5, 4, restock_units=12,
                                 supplier_lead_time=0, supplier_available=False)
    for branch in branches[1:]:
        assert branch["inventory"]["ordered_inbound"] == 0
        assert branch["inventory"]["received_inbound"] == 0
        assert branch["recovery_completed_at"] is None
        assert any(event["event"] == "HypotheticalSupplierUnavailable" for event in branch["events"])


def test_metric_attribution_and_divergence_use_saved_events():
    branches = simulate_branches(STATE, "SKU", 7, 12, restock_units=4,
                                 supplier_lead_time=8, procurement_unit_cost_paise=50,
                                 expedited_delivery_cost_paise=25,
                                 return_inspection_cost_paise=3, unfulfilled_cost_paise=10)
    assert find_first_branch_divergence(branches)["demand_id"].startswith("demand-")
    for metric in METRICS:
        difference = explain_metric_difference(branches, 0, 2, metric)
        assert sum(item["value"] for item in difference["left_contributions"]) == branches[0][metric]
        assert sum(item["value"] for item in difference["right_contributions"]) == branches[2][metric]
        assert difference["difference"] == branches[2][metric] - branches[0][metric]
    assert branches[2]["expedited_shipping_cost_paise"] == 25
    assert branches[2]["procurement_cost_paise"] == 200
    assert branches[2]["refund_exposure_paise"] == 0
    assert branches[0]["potential_refund_exposure_paise"] > 0


def test_holding_cost_integrates_stock_over_elapsed_hours():
    branch = simulate_branches(STATE, "SKU", 9, 4,
                               holding_cost_paise_per_unit_hour=7)[1]
    intervals = [event for event in branch["events"] if event["event"] == "HypotheticalHoldingCost"]
    assert intervals[0]["interval_start_hour"] == 0
    assert intervals[-1]["interval_end_hour"] == branch["horizon_hours"]
    assert all(left["interval_end_hour"] == right["interval_start_hour"]
               for left, right in zip(intervals, intervals[1:]))
    assert branch["holding_cost_paise"] == sum(
        event["state_before"]["verified_sellable"] *
        (event["interval_end_hour"] - event["interval_start_hour"]) * 7
        for event in intervals)


@settings(max_examples=35, deadline=None)
@given(seed=st.integers(min_value=0, max_value=100000),
       demand=st.integers(min_value=1, max_value=35),
       restock=st.integers(min_value=0, max_value=20),
       lead=st.integers(min_value=0, max_value=120),
       result=st.sampled_from(["passed", "failed", "hold"]))
def test_conservation_replay_and_exact_integer_money(seed, demand, restock, lead, result):
    snapshot = deepcopy(STATE)
    kwargs = {"restock_units": restock, "supplier_lead_time": lead,
              "inspection_result": result, "procurement_unit_cost_paise": 123,
              "fulfillment_cost_paise": 17, "unfulfilled_cost_paise": 19}
    branches = simulate_branches(snapshot, "SKU", seed, demand, **kwargs)
    assert branches == simulate_branches(snapshot, "SKU", seed, demand, **kwargs)
    assert snapshot == STATE
    for branch in branches:
        inv = branch["inventory"]
        physical = sum(inv[key] for key in (
            "verified_sellable", "disputed", "reserved", "quarantined", "damaged", "fulfilled"))
        assert physical == 8 + inv["received_inbound"]
        assert inv["reserved"] == 0
        assert branch["fulfilled_units"] + branch["unfulfilled_demand_units"] == sum(
            arrival["units"] for arrival in branch["demand_schedule"])
        assert branch["hypothetical_revenue_paise"] == branch["fulfilled_units"] * 12537
        assert all(isinstance(branch[metric], int) for metric in METRICS)
        assert all(event["time_hours"] <= branch["horizon_hours"] for event in branch["events"])
