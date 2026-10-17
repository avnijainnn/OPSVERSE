"""Private, reproducible decision receipt rendering without raw source payloads."""
from __future__ import annotations

from html import escape
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .live_ai import structured_investigation
from .live_evidence import evidence_graph
from .live_models import AuditRecord, LiveIncident, LiveInvestigation, LiveScenario, LiveSnapshot, Workspace
from .live_twin import utc


def receipt_data(db: Session, workspace_id: str, scenario: LiveScenario,
                 incident_id: str | None = None) -> dict:
    snapshot = db.get(LiveSnapshot, scenario.snapshot_id)
    workspace = db.get(Workspace, workspace_id)
    incident = db.get(LiveIncident, incident_id) if incident_id else None
    if incident_id and (incident is None or incident.workspace_id != workspace_id):
        raise ValueError("Incident is unavailable in this workspace")
    investigation = db.scalar(select(LiveInvestigation).where(
        LiveInvestigation.workspace_id == workspace_id,
        LiveInvestigation.incident_id == incident_id).order_by(LiveInvestigation.created_at.desc()).limit(1)) if incident else None
    graph = evidence_graph(db, incident) if incident else None
    audits = db.scalars(select(AuditRecord).where(
        AuditRecord.workspace_id == workspace_id,
        AuditRecord.target_id.in_([scenario.id, incident_id or scenario.id]))
        .order_by(AuditRecord.created_at).limit(100)).all()
    backtest = db.scalar(select(AuditRecord).where(
        AuditRecord.workspace_id == workspace_id, AuditRecord.action == "backtest_run")
        .order_by(AuditRecord.created_at.desc()).limit(1))
    branches = scenario.branches or [scenario.baseline, scenario.recovery]
    return {"receipt_version": 1, "workspace": {"id": workspace_id, "name": workspace.name},
            "incident": {"id": incident.id, "kind": incident.kind, "summary": incident.summary,
                         "classification": incident.classification,
                         "source_references": [{"kind": ref["kind"], "id": ref["id"]}
                                               for ref in incident.evidence]}
                        if incident else None,
            "investigation": structured_investigation(incident, investigation) if investigation else None,
            "observations": graph["timeline"] if graph else [],
            "missing_evidence": graph["missing_evidence"] if graph else [],
            "scenario": {"id": scenario.id, "kind": scenario.kind,
                         "created_at": utc(scenario.created_at).isoformat(),
                         "seed": scenario.seed, "inputs": scenario.disruption,
                         "assumptions": scenario.assumptions,
                         "branches": [{"label": item["label"],
                                       "fulfilled_units": item["fulfilled_units"],
                                       "unfilled_attempts": item["unfilled_attempts"],
                                       "stock_risk_units": item["stock_risk_units"],
                                       "additional_operational_cost_paise": item.get("additional_operational_cost_paise"),
                                       "refund_exposure_paise": item.get("refund_exposure_paise"),
                                       "event_count": len(item["events"])} for item in branches]},
            "snapshot": {"id": snapshot.id, "source_digest": snapshot.source_digest,
                         "observed_cutoff": utc(snapshot.observed_cutoff).isoformat() if snapshot.observed_cutoff else None,
                         "observation_count": len(snapshot.source_state.get("source_refs", [])),
                         "limitations": snapshot.limitations},
            "backtest": {"run_id": backtest.id, "metric": backtest.detail.get("metric"),
                         "samples": backtest.detail.get("evaluated_samples"),
                         "errors": backtest.detail.get("errors")}
                        if backtest else None,
            "audit": [{"id": item.id, "action": item.action,
                       "created_at": utc(item.created_at).isoformat(),
                       "status": item.detail.get("status"),
                       "branch_index": item.detail.get("branch_index"),
                       "verified": item.detail.get("verified")}
                      for item in audits],
            "manifest": {"model_version": branches[0].get("model_version", "inventory-v1"),
                         "scenario_run_id": scenario.id, "snapshot_id": snapshot.id,
                         "source_digest": snapshot.source_digest, "seed": scenario.seed,
                         "inputs": scenario.disruption,
                         "branch_event_digests": [__import__("hashlib").sha256(
                             json.dumps(item["events"], sort_keys=True).encode()).hexdigest()
                                                  for item in branches]},
            "boundary": "Sandbox predictions and verification are not real-world execution."}


def render_receipt(receipt: dict) -> str:
    def h(value) -> str:
        return escape(str(value if value is not None else "—"))
    scenario = receipt["scenario"]
    incident = receipt["incident"]
    rows = "".join(f"<tr><td>{h(item['label'])}</td><td>{item['fulfilled_units']}</td>"
                   f"<td>{item['unfilled_attempts']}</td><td>{item['stock_risk_units']}</td>"
                   f"<td>{h(item['additional_operational_cost_paise'])}</td></tr>"
                   for item in scenario["branches"])
    claims = receipt["investigation"] or {}
    facts = "".join(f"<li>{h(item['text'])}</li>" for item in claims.get("claims", []))
    hypotheses = "".join(f"<li>{h(item['text'])}</li>" for item in claims.get("root_cause_hypotheses", []))
    missing = "".join(f"<li>{h(item)}</li>" for item in claims.get("unresolved_questions", []))
    audit = "".join(f"<li>{h(item['action'])} · {h(item['created_at'])} · {h(item['status'] or item['verified'])}</li>"
                    for item in receipt["audit"])
    observations = "".join(f"<li>{h(item['kind'])} · {h(item['id'])} · ingested {h(item['ingested_at'])}</li>"
                           for item in receipt["observations"])
    manifest = h(json.dumps(receipt["manifest"], indent=2, sort_keys=True))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>OpsVerse Decision Receipt {h(scenario['id'][:8])}</title>
<style>body{{font:15px/1.55 system-ui,sans-serif;background:#f7f8f7;color:#17222b;margin:0}}main{{max-width:900px;margin:auto;padding:42px}}header{{background:#112a34;color:#e9f8f4;padding:36px;border-radius:12px}}h1{{font-size:34px;margin:5px 0}}h2{{font-size:18px;margin-top:32px;border-bottom:1px solid #ccd9db;padding-bottom:7px}}.eyebrow{{color:#3dbfae;letter-spacing:.18em;font-size:11px;text-transform:uppercase}}.meta{{color:#a5c7c7}}.card{{background:white;border:1px solid #d9e3e4;border-radius:8px;padding:18px;margin-top:14px}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;border-bottom:1px solid #dbe3e4;padding:10px}}th{{font-size:11px;color:#536f75;text-transform:uppercase}}pre{{overflow:auto;background:#eaf1f0;padding:15px;border-radius:6px;font-size:11px}}.boundary{{border-left:4px solid #d49b43;padding:10px 15px;background:#fff4df}}button{{background:#115d57;color:white;border:0;border-radius:6px;padding:10px 18px;cursor:pointer}}@media print{{body{{background:white}}main{{padding:0}}button{{display:none}}header{{print-color-adjust:exact}}}}</style></head><body><main><header><div class="eyebrow">OpsVerse / Decision Receipt</div><h1>Incident to decision</h1><div class="meta">{h(receipt['workspace']['name'])} · Scenario {h(scenario['id'])}<br>Generated from saved evidence and simulation</div></header>
<p class="boundary">{h(receipt['boundary'])}</p><button onclick="window.print()">Print / save PDF</button>
<h2>Incident and source evidence</h2><div class="card"><strong>{h(incident['kind'] if incident else 'No incident selected')}</strong><p>{h(incident['summary'] if incident else 'This scenario was not linked to an incident.')}</p><small>Incident ID: {h(incident['id'] if incident else None)}</small><p>{h(len(incident['source_references']) if incident else 0)} source references · Snapshot {h(receipt['snapshot']['id'])} · {h(receipt['snapshot']['observation_count'])} observation versions</p></div>
<h2>Observation versions</h2><div class="card"><ul>{observations or '<li>No linked observation versions</li>'}</ul></div>
<h2>Findings and uncertainty</h2><div class="card"><strong>Source-derived claims</strong><ul>{facts or '<li>No investigation attached</li>'}</ul><strong>Inferred hypotheses</strong><ul>{hypotheses or '<li>None confirmed</li>'}</ul><strong>Open questions</strong><ul>{missing or '<li>Not recorded</li>'}</ul><p>{h(len(receipt['missing_evidence']))} unresolved evidence references</p></div>
<h2>Compared futures</h2><div class="card"><table><thead><tr><th>Strategy</th><th>Fulfilled units</th><th>Unfilled attempts</th><th>Stock risk units</th><th>Cost · paise</th></tr></thead><tbody>{rows}</tbody></table><p>Seed {h(scenario['seed'])} · Observed cutoff {h(receipt['snapshot']['observed_cutoff'])} · Model {h(receipt['manifest']['model_version'])}</p></div>
<h2>Reliability and limits</h2><div class="card"><p>{h('Latest workspace backtest: ' + str(receipt['backtest']['samples']) + ' order-volume origins' if receipt['backtest'] else 'No valid historical backtest attached')}</p><ul>{''.join('<li>' + h(item) + '</li>' for item in receipt['snapshot']['limitations'])}</ul></div>
<h2>Human review and sandbox verification</h2><div class="card"><ul>{audit or '<li>No review or verification recorded</li>'}</ul></div>
<h2>Reproducibility manifest</h2><pre>{manifest}</pre></main></body></html>"""
