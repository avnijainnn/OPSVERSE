"use client";

import { useState } from "react";

export type Contribution = {
  event_id: string; event: string; time_hours: number;
  demand_id?: string | null; value: number;
  state_before: Record<string, number>; state_after: Record<string, number>;
};
export type DifferenceDetail = {
  metric: string; left_value: number; right_value: number; difference: number;
  left_contributions: Contribution[]; right_contributions: Contribution[];
};

export function MetricContributions({ difference }: { difference: DifferenceDetail }) {
  const [selected, setSelected] = useState<Contribution | null>(null);
  const sides = [
    { label: "Future A", total: difference.left_value, rows: difference.left_contributions },
    { label: "Future B", total: difference.right_value, rows: difference.right_contributions },
  ];
  return <section className="surface metric-contributions" aria-label="Saved metric contributions">
    <div className="panel-head"><div><div className="eyebrow">LEDGER RECONCILIATION</div>
      <h2>Events behind {difference.metric.replaceAll("_", " ")}</h2></div>
      <span className="pill teal">Difference {difference.difference}</span></div>
    <div className="metric-contribution-sides">{sides.map(side => <div key={side.label}>
      <h3>{side.label} · {side.total}</h3>
      {side.rows.length ? side.rows.map(row => <button key={row.event_id} onClick={() => setSelected(row)}>
        <span>{row.event_id} · hour {row.time_hours}{row.demand_id ? ` · ${row.demand_id}` : ""}</span>
        <strong>{row.event.replace(/^Hypothetical/, "")}</strong><b>+{row.value}</b>
      </button>) : <p>No contributing event; total is zero.</p>}
    </div>)}</div>
    {selected && <div className="metric-contribution-detail">
      <button onClick={() => setSelected(null)}>Close event</button>
      <h3>{selected.event_id} · +{selected.value}</h3>
      <div><pre>Before: {JSON.stringify(selected.state_before, null, 2)}</pre>
        <pre>After: {JSON.stringify(selected.state_after, null, 2)}</pre></div>
    </div>}
  </section>;
}
