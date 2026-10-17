"use client";

import { useMemo } from "react";
import { Background, Controls, MarkerType, Position, ReactFlow, type Edge, type Node } from "@xyflow/react";
import type { Event } from "@/lib/api";

export function CausalGraph({ events, onEvent }: { events: Event[]; onEvent: (event: Event) => void }) {
  const { nodes, edges } = useMemo(() => {
    const ids = new Set(events.map(event => event.event_id));
    const nodes: Node[] = events.map((event, index) => ({
      id: event.event_id,
      type: "default",
      position: { x: index * 190, y: index % 2 ? 72 : 15 },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      draggable: false,
      data: { label: <span className="causal-node-label"><small>#{event.sequence_number} · VERIFIED EVENT</small><strong>{event.event_type}</strong><em>{event.aggregate_id}</em></span> },
    }));
    const edges: Edge[] = events.filter(event => event.causation_id && ids.has(event.causation_id)).map(event => ({
      id: `cause-${event.event_id}`,
      source: event.causation_id!,
      target: event.event_id,
      type: "smoothstep",
      animated: false,
      markerEnd: { type: MarkerType.ArrowClosed, color: "#42bba9" },
      style: { stroke: "#42bba9", strokeWidth: 1.5 },
    }));
    return { nodes, edges };
  }, [events]);

  if (!events.length) return <p className="muted-copy">No source events are available for this incident.</p>;
  return <div className="causal-graph" aria-label="Verified causal event graph">
    <ReactFlow nodes={nodes} edges={edges} onNodeClick={(_, node) => {
      const event = events.find(item => item.event_id === node.id);
      if (event) onEvent(event);
    }} fitView fitViewOptions={{ padding: 0.25 }} nodesDraggable={false} nodesConnectable={false} elementsSelectable={true} panOnDrag={true} zoomOnScroll={false} colorMode="dark" proOptions={{ hideAttribution: true }}>
      <Background gap={18} size={1} color="#2b4652" />
      <Controls showInteractive={false} />
    </ReactFlow>
  </div>;
}
