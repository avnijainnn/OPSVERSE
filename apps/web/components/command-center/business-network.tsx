"use client";

import { useEffect, useMemo, useState } from "react";
import { Pause, Play, SkipBack, SkipForward } from "lucide-react";

export type NetworkObservation = {
  id: string; kind: string; source_system: string; provider_object_id: string;
  event_type: string; ingested_at: string;
};
type Node = readonly [string, number];
const paths = [
  ["Customer refs", "Orders", "Warehouse", "Fulfillment", "Delivery"],
  ["Orders", "Payments", "Settlements", "Bank"],
  ["Delivery", "Returns", "Inspection", "Warehouse"],
];
const kindNode: Record<string, string> = {
  order: "Orders", order_item: "Orders", product: "Products", inventory: "Warehouse",
  return: "Returns", payment: "Payments", refund: "Payments",
  settlement: "Settlements", bank_transaction: "Bank",
};

export function SourceCoverageBadge({ count, lastObserved }: { count: number; lastObserved?: string }) {
  return <span className={`source-coverage ${count ? "covered" : "missing"}`}>
    {count ? `${count} observation versions shown · latest ingestion ${lastObserved ? new Date(lastObserved).toLocaleString() : "unknown"}` : "Source unavailable"}
  </span>;
}

export function BusinessNode({ label, count, active, selected, onSelect }: {
  label: string; count: number; active: boolean; selected: boolean; onSelect: () => void;
}) {
  return <button type="button" className={`business-node ${active ? "event-active" : ""} ${selected ? "selected" : ""}`}
    onClick={onSelect} aria-pressed={selected}>
    <small>{label}</small><strong>{count || "—"}</strong>
    <span>{label === "Customer refs" && count ? "Derived from orders" :
      label === "Incidents" && count ? "Derived detection" :
      count ? "Observed" : "No connected source"}</span>
  </button>;
}

export function BusinessEventFlow({ path, activeNode, nodes, selected, onSelect }: {
  path: string[]; activeNode?: string; nodes: Map<string, number>;
  selected: string; onSelect: (node: string) => void;
}) {
  return <div className="business-path">
    {path.map((label, index) => <div className="business-path-item" key={label}>
      <BusinessNode label={label} count={nodes.get(label) ?? 0}
        active={activeNode === label} selected={selected === label} onSelect={() => onSelect(label)}/>
      {index < path.length - 1 && <span className={activeNode === label ? "packet-active" : ""} aria-hidden="true">→</span>}
    </div>)}
  </div>;
}

export function EventTimeline({ events, index, onChange, playing, onPlay }: {
  events: NetworkObservation[]; index: number; onChange: (index: number) => void;
  playing: boolean; onPlay: () => void;
}) {
  const current = events[index];
  return <div className="business-timeline" aria-label="Recorded observation timeline">
    <div className="business-timeline-controls">
      <button onClick={() => onChange(Math.max(0, index - 1))} disabled={!events.length || index === 0} aria-label="Previous observed event"><SkipBack size={16}/></button>
      <button onClick={onPlay} disabled={!events.length} aria-label={playing ? "Pause observed history" : "Play observed history"}>{playing ? <Pause size={16}/> : <Play size={16}/>}</button>
      <button onClick={() => onChange(Math.min(events.length - 1, index + 1))} disabled={!events.length || index >= events.length - 1} aria-label="Next observed event"><SkipForward size={16}/></button>
      <input type="range" aria-label="Observed history position" min="0" max={Math.max(0, events.length - 1)}
        value={Math.max(0, index)} onChange={event => onChange(Number(event.target.value))} disabled={!events.length}/>
      <span>{events.length ? `${index + 1} / ${events.length}` : "No history"}</span>
    </div>
    <div className="business-timeline-event">{current
      ? <><strong>{current.event_type.replaceAll("_", " ")}</strong><span>{current.provider_object_id} · {current.source_system} · recorded {new Date(current.ingested_at).toLocaleString()}</span></>
      : <span>Import a source to replay recorded changes.</span>}</div>
  </div>;
}

export function BusinessNetwork({ nodes, observations, selected, onSelect }: {
  nodes: readonly Node[]; observations: NetworkObservation[];
  selected: string; onSelect: (node: string) => void;
}) {
  const ordered = useMemo(() => [...observations].sort((a, b) =>
    a.ingested_at.localeCompare(b.ingested_at) || a.id.localeCompare(b.id)), [observations]);
  const [index, setIndex] = useState(-1);
  const [playing, setPlaying] = useState(false);
  useEffect(() => { setIndex(ordered.length - 1); setPlaying(false); }, [ordered.length]);
  useEffect(() => {
    if (!playing || !ordered.length) return;
    const timer = window.setInterval(() => setIndex(current => {
      if (current >= ordered.length - 1) { setPlaying(false); return current; }
      return current + 1;
    }), 900);
    return () => window.clearInterval(timer);
  }, [playing, ordered.length]);
  const counts = new Map(nodes);
  const current = ordered[index];
  const activeNode = current ? kindNode[current.kind] : undefined;
  return <div className="business-network">
    <div className="business-network-status">
      <span>Recorded source history · no live feed implied</span>
      <SourceCoverageBadge count={ordered.length} lastObserved={ordered.at(-1)?.ingested_at}/>
    </div>
    <div className="business-paths">{paths.map((path, pathIndex) =>
      <BusinessEventFlow key={pathIndex} path={path} nodes={counts} activeNode={activeNode}
        selected={selected} onSelect={onSelect}/>)}</div>
    <div className="business-extra">{(["Products", "Incidents"] as const).map(label =>
      <BusinessNode key={label} label={label} count={counts.get(label) ?? 0}
        active={activeNode === label} selected={selected === label} onSelect={() => onSelect(label)}/>)}</div>
    <EventTimeline events={ordered} index={index} onChange={next => { setPlaying(false); setIndex(next); }}
      playing={playing} onPlay={() => { if (index >= ordered.length - 1) setIndex(0); setPlaying(value => !value); }}/>
  </div>;
}
