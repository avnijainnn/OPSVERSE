"use client";

import { useEffect, useMemo, useState } from "react";
import { Pause, Play, RotateCcw, SkipForward } from "lucide-react";
import { liveApi } from "@/lib/live-api";

type Inventory = Record<"verified_sellable" | "disputed" | "quarantined" |
  "ordered_inbound" | "received_inbound" | "fulfilled" | "reserved" | "damaged", number>;
type SimEvent = {
  event_id: string; event: string; time_hours: number; demand_id?: string;
  requested?: number; fulfilled?: number; units?: number;
  state_before: Inventory; state_after: Inventory;
  metric_contributions: Record<string, number>;
};
type Branch = {
  label: string; policy_origin: string; events: SimEvent[]; horizon_hours: number;
  starting_sellable: number; fulfilled_units: number; unfulfilled_demand_units: number;
  stock_risk_units: number; additional_operational_cost_paise: number;
  recovery_completed_at: number | null;
};
type Replay = {
  id: string; kind: string; snapshot_id: string; source_digest: string; seed: number;
  branches: Branch[]; disruption: Record<string, string | number>;
  assumptions: string[];
  first_divergence: { demand_id: string; time_hours: number; events: SimEvent[]; reason: string } | null;
};

function atTime(branch: Branch, hour: number): SimEvent[] {
  return branch.events.filter(event => event.time_hours <= hour);
}

function shortEvent(event: string): string {
  return event.replace(/^Hypothetical/, "").replace(/([a-z])([A-Z])/g, "$1 $2");
}

function metricLabel(key: string): string {
  return key.replace(/_/g, " ").replace(/paise$/, "paise");
}

export function ThreeFuturesReplay({ base, scenarioId, onClose }: {
  base: string; scenarioId: string; onClose: () => void;
}) {
  const [replay, setReplay] = useState<Replay | null>(null);
  const [error, setError] = useState("");
  const [hour, setHour] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [selected, setSelected] = useState<SimEvent | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    liveApi<Replay>(`${base}/scenarios/${scenarioId}/replay`)
      .then(data => { if (active) setReplay(data); })
      .catch(reason => { if (active) setError(String(reason)); });
    return () => { active = false; };
  }, [base, scenarioId]);
  const horizon = useMemo(() => Math.max(0, ...(replay?.branches.map(branch => branch.horizon_hours) ?? [])), [replay]);
  useEffect(() => {
    if (!playing || !replay) return;
    const timer = window.setInterval(() => {
      setHour(current => {
        if (current >= horizon) { setPlaying(false); return current; }
        return current + 1;
      });
    }, Math.max(80, 700 / speed));
    return () => window.clearInterval(timer);
  }, [playing, speed, horizon, replay]);
  const jump = (target: number) => {
    setPlaying(false);
    setHour(target);
    setSelected(null);
  };
  if (error) return <section className="surface replay-shell" role="alert">Replay could not load: {error} <button onClick={onClose}>Close</button></section>;
  if (!replay) return <section className="surface replay-shell" role="status">Loading saved futures…</section>;
  if (replay.branches.some(branch => branch.events.some(event => event.time_hours === undefined || !event.state_after)))
    return <section className="surface replay-shell" role="status">
      This older scenario did not save hourly state transitions. Rerun from its snapshot to open synchronized replay.
      <button className="outline-btn" onClick={onClose}>Close replay</button>
    </section>;
  const firstRecovery = replay.branches.map(branch => branch.recovery_completed_at)
    .filter((value): value is number => value !== null).sort((a, b) => a - b)[0];

  return <section className="surface replay-shell" aria-label="Three-Futures Replay">
    <header className="replay-head">
      <div><div className="eyebrow">THREE FUTURES / SAVED HYPOTHETICAL LEDGER</div>
        <h2>Watch the decisions diverge</h2>
        <p>One observed snapshot · shared seed {replay.seed} · model clock {hour}h / {horizon}h</p>
      </div>
      <button className="outline-btn" onClick={onClose}>Close replay</button>
    </header>
    <div className="replay-context">
      <span>Snapshot {replay.snapshot_id.slice(0, 12)}</span>
      <span>Digest {replay.source_digest.slice(0, 12)}</span>
      <span>{String(replay.disruption.sku ?? "Inventory")} · {String(replay.disruption.demand ?? "—")} common demand attempts</span>
    </div>
    <details className="replay-assumptions"><summary>Scenario assumptions and units</summary>
      <ul>{replay.assumptions.map(item => <li key={item}>{item}</li>)}</ul>
    </details>
    <div className="replay-worlds">
      {replay.branches.map((branch, index) => {
        const elapsed = atTime(branch, hour);
        const last = elapsed.at(-1);
        const currentEvents = branch.events.filter(event => event.time_hours === hour);
        const inventory = last?.state_after;
        const arrival = currentEvents.find(event => event.demand_id && event.event !== "HypotheticalReservation");
        const activeNames = currentEvents.map(event => event.event);
        return <article className={`replay-world ${highlight && currentEvents.some(event => event.demand_id === highlight) ? "diverged" : ""}`} key={branch.label}>
          <div className="replay-world-head"><span className="replay-letter">{String.fromCharCode(65 + index)}</span>
            <div><h3>{branch.label}</h3><small>{branch.policy_origin === "deterministic" ? "Deterministic policy" : branch.policy_origin}</small></div>
          </div>
          <div className="replay-flow" aria-label={`Business flow for future ${String.fromCharCode(65 + index)}`}>
            <div className={`replay-node ${arrival ? "active" : ""}`}><small>Incoming orders</small><strong>{elapsed.filter(event => event.demand_id && event.event !== "HypotheticalReservation").length}</strong></div>
            <span aria-hidden="true">→</span>
            <div className={`replay-node ${activeNames.includes("HypotheticalStockReceipt") ? "active" : ""}`}><small>Verified stock</small><strong>{inventory?.verified_sellable ?? branch.starting_sellable}</strong></div>
            <span aria-hidden="true">→</span>
            <div className={`replay-node ${activeNames.some(event => event.includes("Inspection") || event.includes("Quarantine")) ? "active" : ""}`}><small>Inspection / hold</small><strong>{(inventory?.disputed ?? 0) + (inventory?.quarantined ?? 0)}</strong></div>
            <span aria-hidden="true">→</span>
            <div className={`replay-node ${arrival?.fulfilled ? "active" : ""}`}><small>Fulfilled units</small><strong>{inventory?.fulfilled ?? 0}</strong></div>
          </div>
          <div className="replay-buckets">
            <span>Inbound {inventory?.ordered_inbound ?? 0}</span>
            <span>Quarantined {inventory?.quarantined ?? 0}</span>
            <span>Risk used {elapsed.reduce((total, event) => total + (event.metric_contributions.stock_risk_units ?? 0), 0)}</span>
          </div>
          <div className="replay-now">{currentEvents.length
            ? currentEvents.map(event => <button key={event.event_id} onClick={() => setSelected(event)}
                className={selected?.event_id === event.event_id ? "selected" : ""}>
                <small>{event.event_id} · hour {event.time_hours}{event.demand_id ? ` · ${event.demand_id}` : ""}</small>
                {shortEvent(event.event)}
              </button>)
            : <span>No event at this hour</span>}</div>
          <footer>{branch.recovery_completed_at === null ? "Recovery unresolved in horizon" : `Recovery complete at hour ${branch.recovery_completed_at}`}</footer>
        </article>;
      })}
    </div>
    <div className="replay-controls">
      <label>Simulation hour <input aria-label="Simulation hour" type="range" min="0" max={horizon} value={hour}
        onChange={event => jump(Number(event.target.value))}/></label>
      <div className="replay-actions">
        <button onClick={() => setPlaying(value => !value)} aria-label={playing ? "Pause replay" : "Play replay"}>{playing ? <Pause size={16}/> : <Play size={16}/>} {playing ? "Pause" : "Play"}</button>
        <button onClick={() => jump(Math.min(horizon, hour + 1))} aria-label="Step replay"><SkipForward size={16}/> Step</button>
        <label>Speed <select aria-label="Replay speed" value={speed} onChange={event => setSpeed(Number(event.target.value))}>
          <option value={1}>1×</option><option value={5}>5×</option><option value={10}>10×</option>
        </select></label>
        <button onClick={() => { if (replay.first_divergence) { jump(replay.first_divergence.time_hours); setHighlight(replay.first_divergence.demand_id); } }}
          disabled={!replay.first_divergence}>First divergence</button>
        <button onClick={() => firstRecovery !== undefined && jump(firstRecovery)} disabled={firstRecovery === undefined}>Jump to recovery</button>
        <button onClick={() => { jump(0); setHighlight(null); }}><RotateCcw size={16}/> Reset</button>
      </div>
    </div>
    {highlight && replay.first_divergence && <div className="replay-divergence">
      <strong>{highlight} diverged at hour {replay.first_divergence.time_hours}</strong>
      <p>{replay.first_divergence.reason}</p>
      <div>{replay.first_divergence.events.map((event, index) => <button key={event.event_id} onClick={() => setSelected(event)}>
        Future {String.fromCharCode(65 + index)} · {event.event_id} · fulfilled {event.fulfilled}/{event.requested}
      </button>)}</div>
    </div>}
    {selected && <aside className="replay-inspector" aria-label="Selected simulation event">
      <div><strong>{selected.event_id} · {shortEvent(selected.event)}</strong><button onClick={() => setSelected(null)}>Close detail</button></div>
      <p>Hour {selected.time_hours}{selected.demand_id ? ` · shared ${selected.demand_id}` : ""}. Simulated event.</p>
      <div className="replay-inspector-grid"><div><small>State before</small><pre>{JSON.stringify(selected.state_before, null, 2)}</pre></div>
        <div><small>State after</small><pre>{JSON.stringify(selected.state_after, null, 2)}</pre></div>
        <div><small>Metric contributions</small><ul>{Object.entries(selected.metric_contributions).length
          ? Object.entries(selected.metric_contributions).map(([key, value]) => <li key={key}>{metricLabel(key)}: {value}</li>)
          : <li>No metric change</li>}</ul></div></div>
    </aside>}
  </section>;
}
