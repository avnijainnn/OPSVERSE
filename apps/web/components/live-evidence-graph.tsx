"use client";

import { useMemo, useState } from "react";
import { Background, Controls, MarkerType, Position, ReactFlow, type Edge, type Node } from "@xyflow/react";

export type EvidenceGraph = {
  incident_id: string;
  nodes: { id: string; kind: string; record_id?: string; label: string; classification: string; source_system?: string; ingested_at?: string; external_at?: string; schema_version?: number; import_batch_id?: string; webhook_delivery_id?: string }[];
  edges: { id: string; source: string; target: string; relation: string; classification: string; basis: string }[];
  timeline: { id: string; kind: string; event_type: string; source_system: string; external_at?: string; ingested_at: string; record_id: string }[];
  missing_evidence: { kind: string; id: string; reason: string }[];
  coverage: { source_references: number; resolved_sources: number; observation_versions: number; complete: boolean };
  limitations: string[];
};

export function LiveEvidenceGraph({ graph, onSource, onObservation }: { graph: EvidenceGraph; onSource: (kind: string, id: string) => void; onObservation: (id: string) => void }) {
  const [filter, setFilter] = useState("all");
  const { nodes, edges } = useMemo(() => {
    const byId = new Map(graph.nodes.map(item => [item.id, item]));
    const nodes: Node[] = graph.nodes.map((item, index) => {
      const column = item.kind === "incident" ? 2 : item.kind === "observation" ? 0 : 1;
      const row = graph.nodes.slice(0, index).filter(previous => (previous.kind === "incident" ? 2 : previous.kind === "observation" ? 0 : 1) === column).length;
      const color = item.classification === "observed" ? "#6fdacb" : item.classification === "inferred" ? "#efbd82" :
        item.classification === "hypothetical" ? "#d69cf5" : "#a6b8ff";
      return { id: item.id, position: { x: column * 330, y: row * 145 }, sourcePosition: Position.Right,
        targetPosition: Position.Left, draggable: false,
        style: { background: "#132939", border: `1px solid ${color}`, color: "#eaf5f4", borderRadius: 10, width: 238, padding: 8 },
        data: { label: <span className="evidence-node"><small style={{ color }}>{item.classification.toUpperCase()} · {item.kind}</small><strong>{item.label}</strong><em>{item.source_system ?? item.record_id?.slice(0, 8) ?? "Detection"}</em></span> } };
    });
    const edges: Edge[] = graph.edges.filter(item => byId.has(item.source) && byId.has(item.target)).map(item => ({
      id: item.id, source: item.source, target: item.target, type: "smoothstep",
      label: `${item.relation.replaceAll("_", " ")} · ${item.classification}`, markerEnd: { type: MarkerType.ArrowClosed },
      style: { stroke: item.classification === "inferred" ? "#efbd82" : item.classification === "hypothetical" ? "#d69cf5" :
        item.classification === "derived" ? "#a6b8ff" : "#6fdacb",
        strokeWidth: 1.5, strokeDasharray: item.classification === "inferred" ? "5 4" :
          item.classification === "hypothetical" ? "2 4" : undefined },
      labelStyle: { fill: "#d3e3e5", fontSize: 11 },
      labelBgStyle: { fill: "#102531", fillOpacity: 0.96 },
      labelBgPadding: [5, 3],
    }));
    return { nodes, edges };
  }, [graph]);
  const events = graph.timeline.filter(item => filter === "all" || item.kind === filter);
  return <section className="surface live-evidence-workspace">
    <div className="panel-head"><div><div className="eyebrow">INCIDENT MICROSCOPE / SOURCE PROVENANCE</div><h2>Evidence graph</h2></div><span className={`pill ${graph.coverage.complete ? "teal" : "amber"}`}>{graph.coverage.resolved_sources}/{graph.coverage.source_references} sources resolved</span></div>
    <p className="live-muted">Click source nodes or timeline events to inspect records. Teal means observed, blue means derived by the detector, and dashed amber means inferred rather than proven physical causality.</p>
    <div className="live-evidence-canvas" aria-label="Incident evidence graph"><ReactFlow nodes={nodes} edges={edges} fitView fitViewOptions={{ padding: 0.2 }} nodesDraggable={false} nodesConnectable={false} zoomOnScroll={false} colorMode="dark" proOptions={{ hideAttribution: true }} onNodeClick={(_, node) => {
      const item = graph.nodes.find(candidate => candidate.id === node.id);
      if (!item?.record_id) return;
      if (item.kind === "observation") onObservation(item.record_id);
      else onSource(item.kind, item.record_id);
    }}><Background gap={18} size={1} color="#2b4652"/><Controls showInteractive={false}/></ReactFlow></div>
    <div className="live-evidence-footer"><div><div className="section-label">OBSERVATION TIMELINE</div><label>Filter source type <select value={filter} onChange={event => setFilter(event.target.value)}><option value="all">All sources</option>{[...new Set(graph.timeline.map(item => item.kind))].map(kind => <option key={kind} value={kind}>{kind}</option>)}</select></label><div className="live-evidence-timeline">{events.map(item => <button className="live-record" key={item.id} onClick={() => onObservation(item.id)}><span><b>{item.event_type}</b><small>{item.kind} · {item.source_system} · ingested {new Date(item.ingested_at).toLocaleString()}</small></span><span className="pill teal">Observed</span></button>)}{!events.length && <p className="live-muted">No observation versions match this filter.</p>}</div></div><div><div className="section-label">COVERAGE & OPEN QUESTIONS</div><p className="live-muted">{graph.coverage.observation_versions} observation versions. History starts at first ingestion.</p>{graph.missing_evidence.length ? graph.missing_evidence.map((item, index) => <p className="live-evidence-missing" key={`${item.id}-${index}`}>{item.kind}: {item.reason}</p>) : <p className="live-muted">All referenced source rows have linked observation versions.</p>}{graph.limitations.map(item => <p className="live-muted" key={item}>{item}</p>)}</div></div>
  </section>;
}
