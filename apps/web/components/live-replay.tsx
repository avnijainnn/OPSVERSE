"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Pause, Play, SkipBack, SkipForward } from "lucide-react";

export type ReplayObservation = {
  id: string; kind: string; source_system: string; provider_object_id: string;
  event_type: string; ingested_at: string; previous_observation_id?: string | null;
};

function title(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, character => character.toUpperCase());
}

export function LiveReplay({ observations, workspaceId, onInspect, hasOlder, onLoadOlder, loadingOlder }: {
  observations: ReplayObservation[]; workspaceId: string; onInspect: (id: string) => void;
  hasOlder: boolean; onLoadOlder: () => void; loadingOlder: boolean;
}) {
  const events = useMemo(() => [...observations].sort((a, b) =>
    a.ingested_at.localeCompare(b.ingested_at) || a.id.localeCompare(b.id)), [observations]);
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  useEffect(() => { setPosition(0); setPlaying(false); }, [workspaceId]);
  useEffect(() => { setPosition(current => Math.min(current, Math.max(0, events.length - 1))); }, [events.length]);
  useEffect(() => {
    if (!playing || events.length < 2) return;
    const timer = window.setInterval(() => setPosition(current => {
      if (current >= events.length - 1) { setPlaying(false); return current; }
      return current + 1;
    }), 900);
    return () => window.clearInterval(timer);
  }, [playing, events.length]);

  const current = events[position];
  const seen = events.slice(0, position + 1);
  const kinds = [...new Set(seen.map(event => event.kind))];
  const change = (next: number) => { setPlaying(false); setPosition(next); };

  return <div className="live-replay-page">
    <section className="surface live-replay-console">
      <div className="panel-head"><div><div className="eyebrow">RECORDED SOURCE HISTORY</div><h2>Observation {events.length ? position + 1 : 0} of {events.length}</h2></div><span className="pill teal">Workspace scoped</span></div>
      <div className="live-replay-body">
        <p>This sequence replays ingested source observations. Counts show records encountered through the selected observation; they are not a reconstructed inventory or financial balance.</p>
        <div className="live-replay-controls">
          <button className="outline-btn" onClick={() => change(Math.max(0, position - 1))} disabled={position === 0} aria-label="Step back"><SkipBack size={15}/> Step back</button>
          <button className="outline-btn" onClick={() => { if (position >= events.length - 1) setPosition(0); setPlaying(value => !value); }} disabled={events.length < 2} aria-label={playing ? "Pause replay" : "Play replay"}>{playing ? <Pause size={15}/> : <Play size={15}/>} {playing ? "Pause" : "Play"}</button>
          <button className="outline-btn" onClick={() => change(Math.min(events.length - 1, position + 1))} disabled={!events.length || position >= events.length - 1} aria-label="Step forward">Step forward <SkipForward size={15}/></button>
          <button className="outline-btn" onClick={() => change(events.length - 1)} disabled={!events.length || position >= events.length - 1}>Latest</button>
        </div>
        <input type="range" min="0" max={Math.max(0, events.length - 1)} value={position} onChange={event => change(Number(event.target.value))} disabled={!events.length} aria-label="Replay observation position"/>
      </div>
    </section>
    <div className="live-replay-stats"><div><span>Observation versions</span><strong>{seen.length}</strong></div><div><span>Source systems</span><strong>{new Set(seen.map(event => event.source_system)).size}</strong></div><div><span>Record types</span><strong>{kinds.length}</strong></div><div><span>Latest ingestion</span><strong>{current ? new Date(current.ingested_at).toLocaleString() : "—"}</strong></div></div>
    <div className="live-replay-grid"><section className="surface"><div className="panel-head"><div><div className="eyebrow">SELECTED OBSERVATION</div><h2>{current ? title(current.event_type) : "No recorded history"}</h2></div></div>
      {current ? <div className="live-replay-current"><div className="settings-line"><span>Source</span><strong>{title(current.source_system)}</strong></div><div className="settings-line"><span>Record type</span><strong>{title(current.kind)}</strong></div><div className="settings-line"><span>Provider ID</span><code>{current.provider_object_id}</code></div><div className="settings-line"><span>Ingested</span><strong>{new Date(current.ingested_at).toLocaleString()}</strong></div><div className="settings-line"><span>Version</span><strong>{current.previous_observation_id ? "Supersedes prior observation" : "First observed version"}</strong></div><button className="primary-btn" onClick={() => onInspect(current.id)}>Inspect recorded payload <ArrowRight size={14}/></button></div> : <div className="empty"><h3>No observations yet</h3><p>Import a CSV or synchronize a provider to record source history.</p></div>}
      </section><section className="surface"><div className="panel-head"><div><div className="eyebrow">EVENT SEQUENCE</div><h2>Recorded observations</h2></div></div><div className="live-replay-list">{events.map((event, index) => <button key={event.id} className={`live-replay-event ${index === position ? "selected" : ""}`} onClick={() => change(index)}><span>#{index + 1}</span><b>{title(event.event_type)}</b><small>{event.provider_object_id} · {new Date(event.ingested_at).toLocaleString()}</small><ArrowRight size={14}/></button>)}</div></section></div>
    {hasOlder && <div className="live-replay-limit"><p>Showing {events.length} recent observations. Load earlier records to continue replaying source history.</p><button className="outline-btn" onClick={onLoadOlder} disabled={loadingOlder}>{loadingOlder ? "Loading…" : "Load older observations"}</button></div>}
  </div>;
}
