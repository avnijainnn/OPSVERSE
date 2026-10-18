"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, ArrowDownRight, ArrowRight, Beaker, Check, ChevronDown, CircleAlert, Clock3, Command, Database, FastForward, FileClock, GitBranch, LayoutGrid, Menu, Pause, Play, Plus, RotateCcw, Search, Settings2, ShieldCheck, SkipForward, Sparkles, Wallet, X } from "lucide-react";
import { API, ApiError, Event, Incident, Metrics, Scenario, World, inr, name, post, request } from "@/lib/api";
import { CausalGraph } from "@/components/causal-graph";
import { BenchmarkReportPanel, LiveFixtureReportPanel } from "@/components/benchmark-report";

type View = "Live Twin"|"Incidents"|"Scenario Lab"|"Money Trail"|"Agent Traces"|"Replay"|"Benchmarks"|"Settings";
const nav: {label:View; icon:typeof Activity}[] = [
  {label:"Live Twin",icon:LayoutGrid},{label:"Incidents",icon:CircleAlert},{label:"Scenario Lab",icon:GitBranch},
  {label:"Money Trail",icon:Wallet},{label:"Agent Traces",icon:Activity},{label:"Replay",icon:RotateCcw},
  {label:"Benchmarks",icon:Beaker},{label:"Settings",icon:Settings2}
];
const network = [
  {id:"Customers",x:11,y:28,detail:"Demand source"},{id:"Orders",x:31,y:28,detail:"Order lifecycle"},
  {id:"Warehouse",x:51,y:28,detail:"Inventory ledger"},{id:"Fulfillment",x:72,y:28,detail:"Reservations"},
  {id:"Delivery",x:91,y:28,detail:"Completed orders"},{id:"Suppliers",x:31,y:71,detail:"Supply schedule"},
  {id:"Payments",x:11,y:71,detail:"Captured funds"},{id:"Settlements",x:51,y:71,detail:"Payout batches"},
  {id:"Bank",x:72,y:71,detail:"Bank credits"},{id:"Returns",x:91,y:71,detail:"Quality inspection"}
];
const links = [[0,1],[1,2],[2,3],[3,4],[1,6],[6,7],[7,8],[4,9],[9,2],[5,2]];
function deduction(paise:number):string{return paise>0?`−${inr(paise)}`:inr(0)}
function flowNodes(event:Event|null):string[]{
  if(!event)return [];
  const paths:Record<string,string[]>={
    OrderCreated:["Customers","Orders"],InventoryReserved:["Orders","Warehouse","Fulfillment"],
    OrderDelivered:["Fulfillment","Delivery"],PaymentCaptured:["Orders","Payments"],
    SettlementGenerated:["Payments","Settlements"],SettlementReceived:["Settlements","Bank"],
    ReturnReceived:["Delivery","Returns"],ReturnInspected:["Returns","Warehouse"],
    ProductStocked:["Suppliers","Warehouse"],StockSyncRejected:["Returns","Warehouse"]};
  return paths[event.event_type]??[];
}
function incidentEdges(type:string|undefined):number[]{
  return type==="damaged_return"?[7,8]:type==="settlement_mismatch"||type==="missing_payout"?[5,6]:type==="supplier_delay"?[9]:[];
}
const formatClock = (clock:string) => new Date(clock).toLocaleString("en-IN",{day:"2-digit",month:"short",hour:"2-digit",minute:"2-digit",timeZone:"Asia/Kolkata"});

function Metric({label,value,accent}:{label:string;value:string|number;accent?:string}) {return <div className="metric"><div className="metric-label">{label}</div><div className="metric-value" style={{color:accent}}>{value}</div></div>}
function Pill({children,tone="teal"}:{children:React.ReactNode;tone?:"teal"|"amber"|"red"|"muted"}) {return <span className={`pill ${tone}`}>{children}</span>}
function Empty({title,description}:{title:string;description:string}) {return <div className="empty"><Database size={25}/><h3>{title}</h3><p>{description}</p></div>}

export default function Home() {
  const [view,setView]=useState<View>("Live Twin");
  const [world,setWorld]=useState<World|null>(null);
  const [selectedNode,setSelectedNode]=useState("Warehouse");
  const [selectedIncident,setSelectedIncident]=useState<Incident|null>(null);
  const [selectedEvent,setSelectedEvent]=useState<Event|null>(null);
  const [scenario,setScenario]=useState<Scenario|null>(null);
  const [replayAt,setReplayAt]=useState<number|null>(null);
  const [replayWorld,setReplayWorld]=useState<World|null>(null);
  const [replaySource,setReplaySource]=useState<World|null>(null);
  const [moneySearch,setMoneySearch]=useState("");
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);
  const [menu,setMenu]=useState(false);
  const [mobileNav,setMobileNav]=useState(false);
  const [connected,setConnected]=useState(false);
  const [guidedStep,setGuidedStep]=useState<number|null>(null);
  const [guidedSensitivity,setGuidedSensitivity]=useState<{rows:{value:number;branch_c:{unfilled_demand:number;recovery_hours:number|null}}[]}|null>(null);
  const [liveEvent,setLiveEvent]=useState<Event|null>(null);
  const [flowIndex,setFlowIndex]=useState<number|null>(null);
  const seq=useRef(0);
  const busyRef=useRef(false);
  const retryRef=useRef<(()=>Promise<unknown>)|null>(null);
  const worldRef=useRef<World|null>(null);
  const incidentRef=useRef<Incident|null>(null);
  const scenarioRef=useRef<Scenario|null>(null);
  const runId=world?.run.run_id;

  const publishWorld=useCallback((data:World)=>{worldRef.current=data;seq.current=data.sequence;setWorld(data)},[]);
  const publishIncident=useCallback((data:Incident|null)=>{incidentRef.current=data;setSelectedIncident(data)},[]);
  const publishScenario=useCallback((data:Scenario|null)=>{scenarioRef.current=data;setScenario(data)},[]);
  const load = useCallback(async (id:string) => {const data=await request<World>(`/runs/${id}`);publishWorld(data);return data},[publishWorld]);
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        let data: World | null = null;
        const saved = localStorage.getItem("opsverse-run");
        if (saved) {
          try { data = await request<World>(`/runs/${saved}`); } catch { /* expired local run */ }
        }
        if (!data) {
          const runs = await request<World["run"][]>("/runs");
          data = runs.length
            ? await request<World>(`/runs/${runs[0].run_id}`)
            : await post<World>("/runs", {seed: 2026});
        }
        if (live) {
          publishWorld(data);
          localStorage.setItem("opsverse-run", data.run.run_id);
        }
      } catch (error) { if (live) setError(String(error)); }
    })();
    return () => { live = false; };
  }, [publishWorld]);
  useEffect(()=>{
    if(!runId)return;
    let closed=false;
    let refreshTimer:ReturnType<typeof setTimeout>|undefined;
    let retryTimer:ReturnType<typeof setTimeout>|undefined;
    let socket:WebSocket|undefined;
    const connect=()=>{
      socket=new WebSocket(`${API.replace(/^http/,"ws")}/runs/${runId}/stream?after=${seq.current}`);
      socket.onopen=()=>setConnected(true);
      socket.onmessage=e=>{
        const event:Event=JSON.parse(e.data);
        if(event.sequence_number<=seq.current)return;
        seq.current=event.sequence_number;
        setLiveEvent(event);
        clearTimeout(refreshTimer);
        refreshTimer=setTimeout(()=>{if(!closed)load(runId).catch(err=>setError(String(err)))},150);
      };
      socket.onerror=()=>socket?.close();
      socket.onclose=()=>{setConnected(false);if(!closed)retryTimer=setTimeout(connect,1200)};
    };
    connect();
    return()=>{closed=true;clearTimeout(refreshTimer);clearTimeout(retryTimer);socket?.close()};
  },[runId,load]);
  const execute=async<T,>(work:()=>Promise<T>,done?:(value:T)=>void|Promise<void>,retry?:()=>Promise<unknown>):Promise<boolean>=>{
    if(busyRef.current)return false;
    busyRef.current=true;setBusy(true);setError("");retryRef.current=null;
    try{const result=await work();await done?.(result);return true}
    catch(e){setError(e instanceof Error?e.message:String(e));retryRef.current=retry??null;return false}
    finally{busyRef.current=false;setBusy(false)}
  };
  const control=(action:string,speed?:number):Promise<boolean>=>{const id=worldRef.current?.run.run_id;if(!id)return Promise.resolve(false);return execute(()=>post<World>(`/runs/${id}/control`,{action,speed}),publishWorld,()=>control(action,speed))};
  const launch=():Promise<boolean>=>execute(()=>post<World>("/runs",{seed:2026}),data=>{
    localStorage.setItem("opsverse-run",data.run.run_id);publishWorld(data);publishIncident(null);publishScenario(null);
    setSelectedNode("Warehouse");setSelectedEvent(null);setMoneySearch("");setReplaySource(null);setReplayWorld(null);setReplayAt(null);setLiveEvent(null);setFlowIndex(null);setMobileNav(false);
    setGuidedSensitivity(null);setGuidedStep(null);setMenu(false);setView("Live Twin");
  },()=>launch());
  const inject=(type:string):Promise<boolean>=>{
    const id=worldRef.current?.run.run_id;if(!id)return Promise.resolve(false);
    return execute(()=>post<Incident>(`/runs/${id}/incidents`,{type}),async data=>{
      if(data.run_id!==id)throw new Error("The incident belongs to a different run. Refresh the demo and retry.");
      await load(id);publishIncident(data);publishScenario(null);setView("Incidents");setMenu(false);
    },()=>inject(type));
  };
  const openIncident=(id:string):Promise<boolean>=>execute(()=>request<Incident>(`/incidents/${id}`),data=>{
    if(data.run_id!==worldRef.current?.run.run_id)throw new Error("This incident belongs to an older run. Start the demo again to continue.");
    publishIncident(data);publishScenario(data.scenario?.[0]??null);setView("Incidents");
  },()=>openIncident(id));
  const compare=():Promise<boolean>=>{
    const incident=incidentRef.current;if(!incident)return Promise.resolve(false);
    return execute(()=>post<Scenario>(`/incidents/${incident.incident_id}/compare`),data=>{
      if(data.incident_id!==incident.incident_id)throw new Error("The comparison returned for a different incident.");
      publishScenario(data);setView("Scenario Lab");
    },()=>compare());
  };
  const decide=(approve:boolean):Promise<boolean>=>{
    const incident=incidentRef.current;const currentWorld=worldRef.current;
    if(!incident||!currentWorld||incident.run_id!==currentWorld.run.run_id)return Promise.resolve(false);
    const id=incident.incident_id;const run=currentWorld.run.run_id;
    return execute(async()=>{
      try{return await post<Incident>(`/incidents/${id}/decision`,{approve,expected_sequence:seq.current})}
      catch(error){
        await load(run);
        const refreshed=await request<Incident>(`/incidents/${id}`);
        publishIncident(refreshed);
        throw error;
      }
    },async data=>{publishIncident(data);await load(run)},()=>decide(approve));
  };
  const replaySavedRun=(id:string):Promise<boolean>=>execute(()=>request<World>(`/runs/${id}`),data=>{
    setReplaySource(data);setReplayWorld(data);setReplayAt(data.sequence);setView("Replay");
  },()=>replaySavedRun(id));
  const jump=useCallback((at:number)=>{const sourceId=replaySource?.run.run_id??runId;if(!sourceId)return;setReplayAt(at);request<World>(`/runs/${sourceId}?until=${at}`).then(setReplayWorld).catch(e=>setError(String(e)))},[runId,replaySource]);
  const startGuide=async():Promise<void>=>{const started=await launch();if(started){setGuidedSensitivity(null);setGuidedStep(0)}};
  const advanceGuide=async():Promise<void>=>{
    if(guidedStep===0){if(await inject("damaged_return"))setGuidedStep(1);else retryRef.current=()=>advanceGuide()}
    else if(guidedStep===1){if(await compare())setGuidedStep(2);else retryRef.current=()=>advanceGuide()}
    else if(guidedStep===2){
      const incident=incidentRef.current;if(!incident)return;
      const succeeded=await execute(()=>post<{rows:{value:number;branch_c:{unfilled_demand:number;recovery_hours:number|null}}[]}>(`/incidents/${incident.incident_id}/sensitivity`,{review_delay_hours:[0,24,48]}),result=>{setGuidedSensitivity(result);setView("Scenario Lab")},()=>advanceGuide());
      if(succeeded)setGuidedStep(3);else retryRef.current=()=>advanceGuide();
    }
    else if(guidedStep===3){if(await decide(true)){setGuidedStep(4);setView("Incidents")}else retryRef.current=()=>advanceGuide()}
    else if(guidedStep===4){
      const incident=incidentRef.current;const savedScenario=scenarioRef.current;if(!incident||!savedScenario)return;
      const receipt=await execute(async()=>{const opened=window.open(`${API}/incidents/${incident.incident_id}/receipt?result_id=${encodeURIComponent(savedScenario.result_id)}`,"_blank","noopener,noreferrer");if(!opened)throw new Error("The Decision Receipt was blocked by the browser. Allow pop-ups and retry.");return opened},()=>{setView("Scenario Lab");setGuidedStep(5)},()=>advanceGuide());
      if(!receipt)retryRef.current=()=>advanceGuide();
    }
    else if(guidedStep===5){
      const savedScenario=scenarioRef.current;if(!savedScenario)return;
      if(await replaySavedRun(savedScenario.recovery_run_id))setGuidedStep(6);else retryRef.current=()=>advanceGuide();
    }
    else setGuidedStep(null);
  };
  useEffect(()=>{
    if(guidedStep===null)return;
    document.querySelector<HTMLElement>(`[data-guide-focus="${guidedStep}"]`)?.scrollIntoView({behavior:"smooth",block:"center"});
  },[guidedStep,view,scenario,selectedIncident]);
  const entries=world?.events??[];
  useEffect(()=>{if(!liveEvent)return;const timer=setTimeout(()=>setLiveEvent(null),1900);return()=>clearTimeout(timer)},[liveEvent]);
  useEffect(()=>{if(flowIndex===null)return;const timer=setTimeout(()=>setFlowIndex(current=>current===null?null:current>=entries.length-1?null:current+1),750);return()=>clearTimeout(timer)},[flowIndex,entries.length]);
  const flowEvent=flowIndex!==null?entries[flowIndex]??null:liveEvent;
  const flowing=flowNodes(flowEvent);
  const flowingLinks=new Set(links.map(([a,b],index)=>flowing.some((node,j)=>j>0&&((flowing[j-1]===network[a].id&&node===network[b].id)||(flowing[j-1]===network[b].id&&node===network[a].id)))?index:-1));
  const active=world?.incidents.find(i=>i.status!=="resolved");
  const context=useMemo(()=>{if(!world)return null;const s=world.state;const recent=entries.filter(e=>selectedNode==="Customers"?e.event_type==="OrderCreated":selectedNode==="Warehouse"?e.aggregate_type==="product"||e.event_type.startsWith("Inventory")||e.event_type.startsWith("Return"):e.aggregate_type.toLowerCase()===selectedNode.toLowerCase().replace(/s$/,"")).slice(-4).reverse();return {recent,stock:Object.entries(s.stock).slice(0,5),orders:Object.entries(s.orders).slice(-5).reverse(),payments:Object.entries(s.payments).slice(-4).reverse()}},[world,entries,selectedNode]);

  return <div className="app-shell">
    <aside className={`sidebar ${mobileNav?"show":""}`}>
      <div className="brand"><div className="brand-mark"><Command size={20}/></div><div><strong>OPSVERSE</strong><small>OPERATIONS SIMULATOR</small></div></div>
      <div className="workspace-label">WORKSPACE <span>01</span></div>
      <div className="workspace-switch"><div className="workspace-avatar">AC</div><div><b>Aaranya Commerce</b><small>Synthetic India storefront</small></div><ChevronDown size={15}/></div>
      <div className="nav-heading">COMMAND CENTER</div>
      <nav>{nav.map(item=><button key={item.label} className={`nav-item ${view===item.label?"active":""}`} onClick={()=>{setView(item.label);setMobileNav(false)}}><item.icon size={17}/><span>{item.label}</span>{item.label==="Incidents"&&!!world?.metrics.open_incidents&&<i>{world.metrics.open_incidents}</i>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="mode-indicator"><Sparkles size={15}/><span>{world?.execution_mode==="ai_enabled"?"AI-enabled":"Deterministic demo"}</span></div><div className="version">v0.1 • sandbox environment</div></div>
    </aside>
    <main className="main">
      <div className="demo-boundary">SYNTHETIC DEMO MODE · No connected business records <a href="/live">Open Live Operations →</a></div>
      <header className="topbar"><button className="icon-btn mobile-menu" onClick={()=>setMobileNav(!mobileNav)} aria-label="Toggle navigation"><Menu size={18}/></button><div className="breadcrumb">AARANYA COMMERCE <span>/</span> <strong>{view.toUpperCase()}</strong></div><div className="top-actions"><div className="connection-state">{connected?"● Connected":"○ Reconnecting"}</div><div className="live-status"><span className="status-dot"/>{world?.run.status==="running"?"SIMULATING":"PAUSED"}</div><div className="clock"><Clock3 size={15}/>{world?formatClock(world.run.clock):"—"}</div><button className="outline-btn launch-top" onClick={launch} disabled={busy}><Sparkles size={15}/> Launch Demo</button><button className="outline-btn guide-top" onClick={startGuide} disabled={busy}><FastForward size={15}/> Guided tour</button><div className="menu-wrap"><button className="primary-btn" onClick={()=>setMenu(!menu)} disabled={!world||busy}><Plus size={16}/> Inject scenario <ChevronDown size={14}/></button>{menu&&<div className="dropdown">{[["damaged_return","Damaged return"],["settlement_mismatch","Settlement mismatch"],["supplier_delay","Supplier delay"],["missing_payout","Missing payout"]].map(([key,label])=><button key={key} onClick={()=>inject(key)}>{label}<ArrowRight size={14}/></button>)}</div>}</div></div></header>
      {error&&<div className="error-banner" role="alert"><CircleAlert size={16}/><span>{error}</span>{retryRef.current&&<button className="retry-action" onClick={()=>retryRef.current?.()} disabled={busy}>Retry action</button>}<button onClick={()=>setError("")} aria-label="Dismiss error"><X size={16}/></button></div>}
      {busy&&<div className="operation-banner" role="status"><Activity size={15}/> Applying validated sandbox operation…</div>}
      {!world?<div className="loading"><div className="brand-mark"><Command/></div><h2>Initializing business world</h2><p>Loading event ledger and projections…</p></div>:<>
      <div className="page-heading"><div><div className="eyebrow">THE FLIGHT SIMULATOR FOR AI-OPERATED BUSINESSES</div><h1>{view}</h1><p>{subtitle(view)}</p></div><div className="run-chip">RUN <span>{world.run.run_id.slice(0,8).toUpperCase()}</span><span className="chip-sep">·</span> SEED {world.run.seed}</div></div>
      {view==="Live Twin"&&<section className="demo-intro" data-guide-focus="0"><div><div className="eyebrow">FROM OPERATIONAL SIGNAL TO VERIFIED DECISION</div><h2>One damaged return. Three possible decisions. Three different futures.</h2><p>Watch OpsVerse detect an operational failure, reconstruct its evidence, simulate recovery strategies, and verify the outcome.</p></div><button className="primary-btn" onClick={startGuide} disabled={busy}><FastForward size={16}/> Start Guided Demo <ArrowRight size={15}/></button></section>}
      {view==="Live Twin"&&<><div className="kpi-strip"><Metric label="GROSS SALES" value={inr(world.metrics.gross_sales_paise)}/><Metric label="NET SALES" value={inr(world.metrics.net_sales_paise)}/><Metric label="ORDERS" value={world.metrics.order_count}/><Metric label="PENDING" value={world.metrics.pending_orders}/><Metric label="AVAILABLE STOCK" value={world.metrics.available_stock}/><Metric label="RESERVED" value={world.metrics.reserved_stock}/><Metric label="ACTIVE RETURNS" value={world.metrics.active_returns}/><Metric label="UNRECONCILED" value={inr(world.metrics.unreconciled_paise)}/><Metric label="OPEN INCIDENTS" value={world.metrics.open_incidents} accent={world.metrics.open_incidents?"#f5a66d":undefined}/></div>
      <div className="twin-layout"><section className="world-panel"><div className="panel-head"><div><div className="eyebrow">LIVE SYSTEM MAP</div><h2>Business network</h2></div><button className="outline-btn" onClick={()=>setFlowIndex(Math.max(0,entries.length-40))} disabled={!entries.length} aria-label="Replay recorded flow"><Play size={14}/> Replay event flow</button></div><div className={`world-map ${world.run.status==="running"?"running":""}`}><div className="grid-overlay"/><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">{links.map(([a,b],i)=><line key={i} x1={network[a].x} y1={network[a].y} x2={network[b].x} y2={network[b].y} className={`edge ${active&&incidentEdges(active.type).includes(i)?"warn":""} ${flowingLinks.has(i)?"flow":""}`}/>)}</svg>{network.map((node,i)=><button key={node.id} title={node.detail} onClick={()=>setSelectedNode(node.id)} className={`world-node ${selectedNode===node.id?"selected":""} ${flowing.includes(node.id)?"flow":""} ${active&&((active.type==="damaged_return"&&["Warehouse","Returns"].includes(node.id))||((active.type==="settlement_mismatch"||active.type==="missing_payout")&&["Payments","Settlements","Bank"].includes(node.id))||(active.type==="supplier_delay"&&["Suppliers","Warehouse"].includes(node.id)))?"warning":""}`} style={{left:`${node.x}%`,top:`${node.y}%`}}><span className="node-glyph">{String(i+1).padStart(2,"0")}</span><strong>{node.id}</strong><small>{node.detail}</small></button>)}{flowEvent&&<button className="flow-event" onClick={()=>setSelectedEvent(flowEvent)}><span>#{flowEvent.sequence_number} RECORDED EVENT</span><strong>{flowEvent.event_type}</strong><small>{flowEvent.aggregate_id} → inspect source event</small></button>}</div><div className="map-footer"><div><span className="legend-dot teal"/> Operational flow</div><div><span className="legend-dot blue"/> Money flow</div>{active?<button className="flow-incident-link" onClick={()=>openIncident(active.incident_id)}><span className="legend-dot amber"/> {name(active.type)} → inspect incident</button>:<div><span className="legend-dot amber"/> Attention needed</div>}</div></section>
      <aside className="context-panel"><div className="panel-head"><div><div className="eyebrow">SYSTEM INSPECTOR</div><h2>{selectedNode}</h2></div><span className="panel-icon"><Database size={17}/></span></div><NodeContext node={selectedNode} world={world} context={context} onEvent={setSelectedEvent}/></aside></div>
      <section className="timeline-panel"><div className="panel-head"><div><div className="eyebrow">EVENT LEDGER</div><h2>Simulation timeline <span className="count">{world.sequence} events</span></h2></div><div className="playback"><button title="Step one order" onClick={()=>control("step")} disabled={busy}><SkipForward size={16}/></button><button title={world.run.status==="running"?"Pause":"Resume"} className="play-btn" onClick={()=>control(world.run.status==="running"?"pause":"resume")} disabled={busy}>{world.run.status==="running"?<Pause size={16}/>:<Play size={16}/>}</button><div className="speed-picker">{[1,5,10].map(speed=><button className={world.run.speed===speed?"chosen":""} key={speed} onClick={()=>control("speed",speed)}>{speed}×</button>)}</div></div></div><div className="event-list">{entries.slice(-7).reverse().map(e=><EventRow key={e.event_id} event={e} onClick={()=>setSelectedEvent(e)}/>)}</div></section></>}
      {view==="Incidents"&&<div className="two-col"><section className="surface"><div className="panel-head"><div><div className="eyebrow">DETECTION QUEUE</div><h2>Operational incidents</h2></div><Pill tone="amber">{world.metrics.open_incidents} open</Pill></div>{world.incidents.length?world.incidents.map(i=><button key={i.incident_id} className={`incident-row ${selectedIncident?.incident_id===i.incident_id?"selected":""}`} onClick={()=>openIncident(i.incident_id)}><span className={`severity ${i.severity}`}/><div><b>{name(i.type)}</b><small>{i.incident_id} • {formatClock(i.created_at)}</small></div><Pill tone={i.status==="resolved"?"teal":"amber"}>{name(i.status)}</Pill><ArrowRight size={16}/></button>):<Empty title="No active incidents" description="Inject a scenario from the top bar to investigate a reproducible failure."/>}</section><section className="surface detail-panel">{selectedIncident?<IncidentDetail data={selectedIncident} events={entries} scenario={scenario} onCompare={compare} onApprove={decide} onReplay={replaySavedRun} busy={busy} onEvent={setSelectedEvent}/>:<Empty title="Select an incident" description="Open a detected incident to review source events, causal evidence, and a typed recovery plan."/>}</section></div>}
      {view==="Scenario Lab"&&<ScenarioView scenario={scenario} incident={selectedIncident} onCompare={compare} onApprove={decide} onReplay={replaySavedRun} busy={busy}/>}
      {view==="Money Trail"&&<MoneyView world={world} query={moneySearch} onQuery={setMoneySearch} onEvent={setSelectedEvent}/>}
      {view==="Agent Traces"&&<TraceView world={world} onOpen={openIncident}/>}
      {view==="Replay"&&<ReplayView world={replaySource??world} replayWorld={replayWorld} at={replayAt} onJump={jump} onLive={()=>{setReplayAt(null);setReplayWorld(null);setReplaySource(null)}} onEvent={setSelectedEvent}/>}
      {view==="Benchmarks"&&<BenchmarksView world={world}/>}
      {view==="Settings"&&<SettingsView world={world} onLaunch={launch} onControl={control}/>}
      </>}
    </main>
    {guidedStep!==null&&<GuidedPanel step={guidedStep} busy={busy} error={error} sensitivity={guidedSensitivity} onNext={advanceGuide} onClose={()=>setGuidedStep(null)}/>}
    {selectedEvent&&<div className="overlay" onClick={()=>setSelectedEvent(null)}><div className="event-drawer" onClick={e=>e.stopPropagation()}><div className="panel-head"><div><div className="eyebrow">SOURCE EVENT</div><h2>{selectedEvent.event_type}</h2></div><button className="icon-btn" onClick={()=>setSelectedEvent(null)} aria-label="Close"><X size={18}/></button></div><div className="drawer-meta"><span>EVENT ID</span><code>{selectedEvent.event_id}</code><span>SEQUENCE</span><code>#{selectedEvent.sequence_number}</code><span>SIMULATION TIME</span><code>{formatClock(selectedEvent.timestamp)}</code><span>CAUSED BY</span><code>{selectedEvent.causation_id??"—"}</code></div><div className="eyebrow">VALIDATED PAYLOAD</div><pre>{JSON.stringify(selectedEvent.payload,null,2)}</pre></div></div>}
  </div>;
}

function subtitle(view:View){return ({"Live Twin":"A real-time view of every order, stock movement, and payment in your simulated business.","Incidents":"Investigate anomalies with source-linked evidence and safe recovery plans.","Scenario Lab":"Fork the same business state. Compare decisions against matched future inputs.","Money Trail":"Follow every rupee from customer capture to bank credit.","Agent Traces":"Inspect deterministic tool use, evidence, and decisions.","Replay":"Reconstruct the business at any recorded event sequence.","Benchmarks":"Measured checks from the current event ledger. No invented uplift.","Settings":"Simulation controls and environment details."})[view]}
function EventRow({event,onClick,highlighted=false}:{event:Event;onClick:()=>void;highlighted?:boolean}){const tone=event.event_type.includes("Incident")||event.event_type.includes("Observed")?"amber":event.event_type.includes("Payment")||event.event_type.includes("Settlement")?"blue":"teal";return <button className={`event-row ${highlighted?"evidence-event":""}`} onClick={onClick}><span className={`event-marker ${tone}`}/><span className="event-seq">#{String(event.sequence_number).padStart(4,"0")}</span><strong>{event.event_type}</strong><span className="event-entity">{event.aggregate_id}</span><span className="event-time">{formatClock(event.timestamp)}</span><ArrowRight size={14}/></button>}
function NodeContext({node,world,context,onEvent}:{node:string;world:World;context:ReturnType<typeof Object>|null;onEvent:(e:Event)=>void}){const s=world.state;const recent=(context as {recent:Event[]}|null)?.recent??[];return <div className="node-context"><p className="context-description">{network.find(x=>x.id===node)?.detail} · live projection from {world.sequence} recorded events</p>{node==="Warehouse"||node==="Returns"?<><div className="mini-stats"><Metric label="SELLABLE" value={world.metrics.available_stock}/><Metric label="RESERVED" value={world.metrics.reserved_stock}/><Metric label="QUARANTINED" value={Object.values(s.stock).reduce((n,x)=>n+x.quarantined,0)}/><Metric label="INSPECTION" value={Object.values(s.stock).reduce((n,x)=>n+x.inspection_pending,0)}/></div><div className="section-label">INVENTORY BY SKU</div>{Object.entries(s.stock).slice(0,5).map(([sku,x])=><div className="data-line" key={sku}><span><b>{sku}</b><small>{x.name}</small></span><strong>{x.sellable}</strong></div>)}</>:node==="Orders"||node==="Fulfillment"||node==="Delivery"?<><div className="mini-stats"><Metric label="TOTAL" value={world.metrics.order_count}/><Metric label="PENDING" value={world.metrics.pending_orders}/></div><div className="section-label">RECENT ORDERS</div>{Object.entries(s.orders).slice(-6).reverse().map(([id,o])=><div className="data-line" key={id}><span><b>{id}</b><small>{o.sku}</small></span><Pill tone={o.status==="delivered"?"teal":"muted"}>{o.status}</Pill></div>)}</>:node==="Payments"||node==="Settlements"||node==="Bank"?<><div className="mini-stats"><Metric label="CAPTURED" value={inr(Object.values(s.payments).reduce((n,p)=>n+p.captured_paise,0))}/><Metric label="UNRECONCILED" value={inr(world.metrics.unreconciled_paise)}/></div><div className="section-label">PAYOUT ACTIVITY</div>{Object.entries(s.settlements).slice(-5).map(([id,x])=><div className="data-line" key={id}><span><b>{id}</b><small>{x.reconciled?"Matched":"Unmatched"}</small></span><strong>{inr(x.expected_paise)}</strong></div>)}</>:node==="Customers"?<><div className="mini-stats"><Metric label="ORDERS" value={world.metrics.order_count}/><Metric label="CHANNELS" value={new Set(Object.values(s.orders).map(order=>order.channel)).size}/></div><p className="muted-copy">Demand is represented by recorded orders. This demo does not store customer profiles.</p><div className="section-label">RECENT DEMAND</div>{Object.entries(s.orders).slice(-5).reverse().map(([id,order])=><div className="data-line" key={id}><span><b>{id}</b><small>{order.channel}</small></span><strong>{inr(order.amount_paise)}</strong></div>)}</>:<><div className="mini-stats"><Metric label="SUPPLIERS" value={Object.keys(s.suppliers).length}/><Metric label="REPORTED DELAYS" value={Object.values(s.suppliers).filter(x=>x.delay_days>0).length}/></div><div className="section-label">SUPPLY STATUS</div>{Object.entries(s.suppliers).map(([id,x])=><div className="data-line" key={id}><span><b>{id}</b><small>{x.catalog_skus.length} SKUs · {x.lead_days}d lead</small></span><Pill tone={x.delay_days?"amber":"muted"}>{x.delay_days?`+${x.delay_days}d`:"On time"}</Pill></div>)}</>}<div className="section-label recent-label">RELATED ACTIVITY</div>{recent.length?recent.map(e=><button key={e.event_id} className="context-event" onClick={()=>onEvent(e)}><span>{e.event_type}</span><small>#{e.sequence_number}</small></button>):<p className="muted-copy">No recent node events.</p>}</div>}

function IncidentDetail({data,events,scenario,onCompare,onApprove,onReplay,busy,onEvent}:{data:Incident;events:Event[];scenario:Scenario|null;onCompare:()=>void;onApprove:(approve:boolean)=>void;onReplay:(id:string)=>void;busy:boolean;onEvent:(event:Event)=>void}){
  const evidence=data.evidence_events??events.filter(e=>data.evidence_event_ids.includes(e.event_id));
  const guardEvent=evidence.find(event=>event.event_type==="StockSyncRejected");
  const plan=data.plans?.[0];
  const verification=data.verification?.[0];
  const evidenceNames=evidence.map(event=>event.event_type).join(" → ");
  return <>
    <div className="panel-head incident-head" data-guide-focus="1"><div><div className="eyebrow">INVESTIGATION · {data.severity.toUpperCase()}</div><h2>{name(data.type)}</h2></div><Pill tone={data.status==="resolved"?"teal":"amber"}>{name(data.status)}</Pill></div>
    <div className="incident-cause-effect"><div><div className="section-label">CAUSE · INCIDENT RESULT</div><p>{data.root_cause}</p></div><div><div className="section-label">RECORDED EFFECT</div><p>{data.description}</p></div></div>
    <div className="detail-grid"><div><div className="section-label">AFFECTED ENTITIES</div>{Object.entries(data.affected_entities).map(([key,value])=><div className="key-value" key={key}><span>{name(key)}</span><code>{value}</code></div>)}</div><div><div className="section-label">INVESTIGATION STATUS</div><p>{name(data.status)} · {data.evidence_event_ids.length} linked source event(s)</p></div></div>
    {guardEvent&&<div className="policy-guard"><ShieldCheck size={18}/><span>Policy guard recorded: <strong>{guardEvent.event_type}</strong> rejected the inventory sync.</span></div>}
    <div className="section-label evidence-heading" data-guide-focus="1">EVIDENCE GRAPH · RECORDED SOURCE EVENTS</div><CausalGraph events={evidence} onEvent={onEvent}/>
    <div className="section-label evidence-list-label">SOURCE EVENT LEDGER · SELECT TO INSPECT</div><div className="event-list incident-evidence-list">{evidence.map(event=><EventRow key={event.event_id} event={event} highlighted onClick={()=>onEvent(event)}/>)}</div>
    <div className="section-label">INVESTIGATOR INTERPRETATION</div><div className="finding"><Sparkles size={17}/><div><b>{data.traces?.[0]?.agent_name??"Investigator"}</b><p>{data.traces?.[0]?.finding}</p><small>{data.traces?.[0]?.mode==="ai_enabled"?"AI interpretation · AI-enabled":"Deterministic investigation"} · evidence-linked tool results</small></div></div>
    <div className="section-label">POLICY-VALIDATED RECOVERY</div><div className="plan-card"><div><b>{name(plan?.action_type??"No plan")}</b><p>Policy checked · {name(plan?.risk_level??"unknown")} risk · sandbox only</p></div><ShieldCheck size={20}/></div>
    {data.resolution&&<div className="resolution-note"><strong>Simulated outcome</strong><span>{data.resolution}</span></div>}
    {verification&&<div className="verification"><Check size={17}/><div><b>Verification {verification.passed?"passed":"failed"}</b><p>{Object.entries(verification.checks).map(([key,value])=>`${name(key)}: ${value?"pass":"fail"}`).join(" · ")}</p></div></div>}
    {verification&&<section className="outcome-summary" data-guide-focus="4"><div className="eyebrow">SANDBOX OUTCOME · RECORDED, NOT A REAL-WORLD WRITE</div><h3>{verification.passed?"Decision verified":"Decision did not verify"}</h3><div className="outcome-grid"><div><b>What went wrong</b><span>{data.root_cause}</span></div><div><b>Evidence</b><span>{evidenceNames||"No linked event names returned"}</span></div><div><b>Policy selected</b><span>{name(plan?.action_type??"No plan")}</span></div><div><b>Business change</b><span>{data.resolution??"No resolution recorded"}</span></div></div>{scenario&&<div className="outcome-actions"><a className="outline-btn" href={`${API}/incidents/${data.incident_id}/receipt?result_id=${encodeURIComponent(scenario.result_id)}`} target="_blank" rel="noopener noreferrer">Open Decision Receipt <ArrowRight size={14}/></a><button className="primary-btn" onClick={()=>onReplay(scenario.recovery_run_id)} disabled={busy}><RotateCcw size={15}/> Replay the chain</button></div>}</section>}
    <div className="detail-actions"><button className="primary-btn compare-action" onClick={onCompare} disabled={busy}><GitBranch size={16}/> Compare futures</button>{data.status==="awaiting_approval"&&<><button className="outline-btn" onClick={()=>onApprove(true)} disabled={busy}><ShieldCheck size={16}/> Approve recovery in sandbox</button><button className="text-btn" onClick={()=>onApprove(false)} disabled={busy}>Reject</button></>}</div>
  </>;
}

function ScenarioView({scenario,incident,onCompare,onApprove,onReplay,busy}:{scenario:Scenario|null;incident:Incident|null;onCompare:()=>void;onApprove:(yes:boolean)=>void;onReplay:(id:string)=>void;busy:boolean}) {
  const [both,setBoth]=useState<World[]|null>(null);
  const [loading,setLoading]=useState(false);
  const [showTimelines,setShowTimelines]=useState(false);
  const [localError,setLocalError]=useState("");
  const [sensitivity,setSensitivity]=useState<{variable:string;repetitions:number;note:string;rows:{value:number;branch_c:{open_incidents:number;unfilled_demand:number;expenses_paise:number;recovery_hours:number|null}}[]}|null>(null);
  useEffect(()=>{
    let active=true;
    setBoth(null);setShowTimelines(false);setLocalError("");
    if(!scenario){setLoading(false);return}
    setLoading(true);
    const ids=[scenario.baseline_run_id,scenario.recovery_run_id,...(scenario.third_run_id?[scenario.third_run_id]:[])];
    Promise.all(ids.map(id=>request<World>(`/runs/${id}`))).then(data=>{if(active)setBoth(data)}).catch(error=>{if(active)setLocalError(String(error))}).finally(()=>{if(active)setLoading(false)});
    return()=>{active=false};
  },[scenario?.result_id]);
  if(!incident)return <Empty title="No incident selected" description="Open an incident, then compare its recovery plan against no intervention."/>;
  if(!scenario)return <div className="surface intro-panel"><div className="icon-large"><GitBranch size={28}/></div><div className="eyebrow">CONTROLLED COUNTERFACTUAL</div><h2>One state. Three futures.</h2><p>All branches start at the same recorded event. The recovery branch applies the typed action immediately; the third defers it for manual review. Future demand is matched.</p><button className="primary-btn" onClick={onCompare} disabled={busy}>Run comparison <ArrowRight size={16}/></button></div>;
  const comparisons:[keyof Metrics,string,boolean][]=[["open_incidents","Open incidents",false],["available_stock","Sellable units",false],["active_returns","Pending inspections",false],["rejected_observations","Rejected source claims",false],["order_count","Orders",false],["unfilled_demand","Unfulfilled demand",false],["payout_disputes","Payout disputes",false],["expenses_paise","Intervention cost",true]];
  const replayBoth=async()=>{
    setLocalError("");
    if(both){setShowTimelines(true);return}
    setLoading(true);
    try{setBoth(await Promise.all([scenario.baseline_run_id,scenario.recovery_run_id,...(scenario.third_run_id?[scenario.third_run_id]:[])].map(id=>request<World>(`/runs/${id}`))));setShowTimelines(true)}
    catch(error){setLocalError(String(error))}
    finally{setLoading(false)}
  };
  const runSensitivity=async()=>{setLoading(true);setLocalError("");try{setSensitivity(await post<NonNullable<typeof sensitivity>>(`/incidents/${incident.incident_id}/sensitivity`,{review_delay_hours:[0,24,48]}))}catch(error){setLocalError(String(error))}finally{setLoading(false)}};
  const baseline=scenario.baseline_metrics;
  const recovered=scenario.recovery_metrics;
  const summaryParts:string[]=[];
  if(baseline.open_incidents!==recovered.open_incidents)summaryParts.push(`Open incidents: ${baseline.open_incidents} → ${recovered.open_incidents}`);
  if(baseline.available_stock!==recovered.available_stock)summaryParts.push(`Sellable units: ${baseline.available_stock} → ${recovered.available_stock}`);
  if(baseline.active_returns!==recovered.active_returns)summaryParts.push(`Pending inspections: ${baseline.active_returns} → ${recovered.active_returns}`);
  if(baseline.rejected_observations!==recovered.rejected_observations)summaryParts.push(`Rejected source claims: ${baseline.rejected_observations} → ${recovered.rejected_observations}`);
  if(baseline.unfilled_demand!==recovered.unfilled_demand)summaryParts.push(`Unfulfilled demand: ${baseline.unfilled_demand} → ${recovered.unfilled_demand}`);
  if(baseline.order_count!==recovered.order_count)summaryParts.push(`Orders: ${baseline.order_count} → ${recovered.order_count}`);
  if(baseline.payout_disputes!==recovered.payout_disputes)summaryParts.push(`Payout disputes: ${baseline.payout_disputes} → ${recovered.payout_disputes}`);
  if(baseline.expenses_paise!==recovered.expenses_paise)summaryParts.push(`Intervention cost: ${inr(baseline.expenses_paise)} → ${inr(recovered.expenses_paise)}`);
  const recoveryTime=(branch:World|undefined)=>{
    if(!branch)return "Loading recorded branch";
    const start=branch.events.find(event=>event.event_type==="IncidentDetected"&&event.aggregate_id===incident.incident_id);
    const verified=branch.events.find(event=>event.event_type==="RecoveryVerified"&&event.aggregate_id===incident.incident_id);
    if(!verified||!start)return branch.metrics.open_incidents?"Not recovered in this branch":"No recovery time recorded";
    const hours=(new Date(verified.timestamp).getTime()-new Date(start.timestamp).getTime())/3_600_000;
    return Number.isFinite(hours)?`${hours.toFixed(1)}h`:"Not recorded";
  };
  const verificationStatus=(branch:World|undefined)=>{
    if(!branch)return "Loading";
    const verified=branch.events.find(event=>event.event_type==="RecoveryVerified"&&event.aggregate_id===incident.incident_id);
    return verified?((verified.payload.passed===true)?"Passed":"Failed"):"Not run";
  };
  return <>
    <div className="compare-head"><div><div className="eyebrow">SOURCE INCIDENT</div><h2>{name(incident.type)}</h2><p>Matched seed • independent branch ledgers • {scenario.created_at.slice(0,10)}</p></div><Pill>Reproducible</Pill></div>
    <div className="branch-layout" data-guide-focus="2">{[
      {label:"A",title:"No intervention",subtitle:"BASELINE",id:scenario.baseline_run_id,values:scenario.baseline_metrics,css:"branch-card"},
      {label:"B",title:name(incident.plans?.[0]?.action_type??"Recovery"),subtitle:"POLICY-VALIDATED PLAN",id:scenario.recovery_run_id,values:scenario.recovery_metrics,css:"branch-card recovery"},
      ...(scenario.third_run_id&&scenario.third_metrics?[{label:"C",title:"Defer for manual review",subtitle:"DETERMINISTIC DELAY",id:scenario.third_run_id,values:scenario.third_metrics,css:"branch-card"}]:[])
    ].map((branch,index)=>{
      const branchWorld=both?.find(item=>item.run.run_id===branch.id);
      return <div className="branch-pair" key={branch.id}>{index===1&&<div className="branch-vs">VS</div>}<div className={`${branch.css} ${index===1?"featured-branch":""}`}><div className="branch-top"><span className="branch-letter">{branch.label}</span><div><div className="eyebrow">{branch.subtitle}</div><h3>{branch.title}</h3></div></div><div className="branch-id">RUN {branch.id.slice(0,8).toUpperCase()}</div>{comparisons.map(([key,label,money])=><div className={`branch-metric ${key==="open_incidents"||key==="available_stock"||key==="unfilled_demand"?"branch-key-metric":""}`} key={key}><span>{label}</span><strong>{money?inr(branch.values[key]-scenario.baseline_metrics.expenses_paise):branch.values[key]}</strong></div>)}<div className="branch-metric"><span>Recovery time</span><strong>{recoveryTime(branchWorld)}</strong></div><div className="branch-metric"><span>Verification</span><strong>{verificationStatus(branchWorld)}</strong></div><button className="primary-btn branch-replay" onClick={()=>onReplay(branch.id)} disabled={busy}><RotateCcw size={15}/> Replay {branch.label==="A"?"baseline":branch.label==="B"?"recovery":"delayed review"}</button></div></div>;
    })}</div>
    <div className="comparison-note branch-summary"><ShieldCheck size={19}/><p>{summaryParts.length?summaryParts.join(" · "):"The saved baseline and recovery metrics match for these outcomes."} Compare the saved event ledgers before approval.</p>{incident.status==="awaiting_approval"&&<button className="primary-btn" onClick={()=>onApprove(true)} disabled={busy}>Approve in sandbox <ArrowRight size={15}/></button>}</div>
    <div className="comparison-note" data-guide-focus="5"><FileClock size={19}/><p>Save this synthetic investigation, branch runs, decision status, and verification as a printable Decision Receipt.</p><a className="outline-btn" href={`${API}/incidents/${incident.incident_id}/receipt?result_id=${encodeURIComponent(scenario.result_id)}`} target="_blank" rel="noopener noreferrer">Open Decision Receipt <ArrowRight size={15}/></a></div>
    <section className="surface demo-sensitivity" data-guide-focus="3"><div className="panel-head"><div><div className="eyebrow">SENSITIVITY / SAVED BRANCH RUNS</div><h2>Manual-review delay</h2></div><button className="outline-btn" onClick={runSensitivity} disabled={loading}>{loading?"Running…":"Run delay sweep"}</button></div><p className="live-muted">Compare delayed-review branch C at 0, 24 and 48 hours. Each result is calculated from an isolated SQLite event ledger.</p>{sensitivity&&<><p className="live-muted">{sensitivity.note}</p><div className="demo-sensitivity-grid">{sensitivity.rows.map(item=><div key={item.value}><b>{item.value} hours</b><span>Recovery {item.branch_c.recovery_hours?.toFixed(1) ?? "unverified"}h</span><span>{item.branch_c.open_incidents} open incidents</span><span>{item.branch_c.unfilled_demand} unfilled demand</span><span>{inr(item.branch_c.expenses_paise)} cost</span></div>)}</div></>}{localError&&<p className="live-evidence-missing" role="alert">{localError}</p>}</section>
    <div className="replay-both-action"><button className="primary-btn" onClick={replayBoth} disabled={loading||busy}><RotateCcw size={15}/>{loading?"Loading branch ledgers…":"Replay all futures"}</button>{localError&&<span>{localError}</span>}</div>
    {showTimelines&&both&&<div className="branch-timelines">{both.map((branch,index)=><section className="surface" key={branch.run.run_id}><div className="panel-head"><div><div className="eyebrow">RECORDED BRANCH {String.fromCharCode(65+index)}</div><h2>{["No intervention","Recovery","Delayed review"][index]} timeline</h2></div><Pill tone={index?"teal":"muted"}>{branch.sequence} recorded events</Pill></div><div className="event-list">{branch.events.slice(-8).reverse().map(event=><EventRow key={event.event_id} event={event} onClick={()=>onReplay(branch.run.run_id)}/>)}</div></section>)}</div>}
  </>;
}
function MoneyView({world,query,onQuery,onEvent}:{world:World;query:string;onQuery:(x:string)=>void;onEvent:(x:Event)=>void}){const s=world.state;const q=query.trim().toLowerCase();const payments=Object.entries(s.payments).filter(([id,p])=>!q||id.toLowerCase().includes(q)||p.order_id.toLowerCase().includes(q)||Object.entries(s.settlements).some(([sid,settlement])=>settlement.payment_id===id&&(sid.toLowerCase().includes(q)||settlement.adjustment_id?.toLowerCase().includes(q))));return <><div className="finance-summary"><Metric label="CAPTURED" value={inr(Object.values(s.payments).reduce((n,x)=>n+x.captured_paise,0))}/><Metric label="GATEWAY FEES + TAX" value={inr(Object.values(s.payments).reduce((n,x)=>n+x.fee_paise+x.fee_tax_paise,0))}/><Metric label="BANK RECEIVED" value={inr(Object.values(s.settlements).reduce((n,x)=>n+(x.received_paise??0),0))}/><Metric label="UNRECONCILED" value={inr(world.metrics.unreconciled_paise)}/></div><section className="surface"><div className="panel-head"><div><div className="eyebrow">TRANSACTION EXPLORER</div><h2>Where did the money go?</h2></div><div className="search-box"><Search size={16}/><input value={query} onChange={e=>onQuery(e.target.value)} placeholder="Search order, payment, settlement…"/></div></div><div className="money-list">{payments.slice(-30).reverse().map(([paymentId,payment])=>{const settlement=Object.entries(s.settlements).find(([,x])=>x.payment_id===paymentId);const related=world.events.filter(e=>e.aggregate_id===paymentId||e.aggregate_id===settlement?.[0]||e.aggregate_id===payment.order_id);return <div className="money-row" key={paymentId}><div className="money-source"><div className="money-icon"><Wallet size={17}/></div><div><b>{payment.order_id}</b><small>{paymentId}</small></div></div><ArrowRight size={16}/><div><b>{inr(payment.captured_paise)}</b><small>Captured</small></div><ArrowRight size={16}/><div><b>{deduction(payment.fee_paise+payment.fee_tax_paise)}</b><small>Fees + tax</small></div><ArrowRight size={16}/><div><b>{deduction((settlement?.[1].prior_refund_paise??0)+payment.refunded_paise)}</b><small>Refund adjustments</small></div><ArrowRight size={16}/><div><b>{settlement?inr(settlement[1].received_paise??0):"Pending"}</b><small>{settlement?settlement[1].reconciled?"Bank matched":"Review payout":"Settlement pending"}</small></div>{related.length>0&&<button className="icon-btn" onClick={()=>onEvent(related[related.length-1])} title="Inspect source record"><ArrowRight size={16}/></button>}</div>})}{!payments.length&&<Empty title="No matching transactions" description="Try an order, payment, or settlement identifier."/>}</div></section></>}

type Trace = {execution_id:string;incident_id:string;agent_name:string;mode:string;finding:string;evidence_event_ids:string[];tool_calls:{call_id:string;tool_name:string;arguments:Record<string,unknown>;result:unknown;duration_ms:number;status:string}[]};
function TraceView({world,onOpen}:{world:World;onOpen:(id:string)=>void}) {
  const [traces,setTraces]=useState<Trace[]>([]);
  const [selected,setSelected]=useState<string|null>(null);
  const [loading,setLoading]=useState(true);
  useEffect(()=>{let alive=true;request<Trace[]>(`/runs/${world.run.run_id}/traces`).then(data=>{if(alive){setTraces(data);setLoading(false)}}).catch(()=>{if(alive)setLoading(false)});return()=>{alive=false}},[world.run.run_id,world.incidents.length]);
  const trace=traces.find(x=>x.execution_id===selected)??traces[0];
  return <div className="two-col trace-layout"><section className="surface"><div className="panel-head"><div><div className="eyebrow">EXECUTION HISTORY</div><h2>Agent traces</h2></div><Pill tone="muted">{world.execution_mode==="ai_enabled"?"AI-enabled":"Deterministic demo"}</Pill></div>{loading?<p className="trace-loading">Loading executions…</p>:traces.length?traces.map(item=><button className={trace?.execution_id===item.execution_id?"trace-row selected":"trace-row"} key={item.execution_id} onClick={()=>setSelected(item.execution_id)}><span className="trace-icon"><Sparkles size={18}/></span><span><b>{item.agent_name}</b><small>{item.incident_id} · {item.tool_calls.length} tool calls · {item.mode==="ai_enabled"?"AI-enabled":"Deterministic demo"}</small></span><ArrowRight size={16}/></button>):<Empty title="No executions yet" description="Inject an incident to run an evidence-linked investigation."/>}</section><section className="surface trace-detail">{trace?<><div className="panel-head"><div><div className="eyebrow">EXECUTION {trace.execution_id.slice(0,8).toUpperCase()}</div><h2>{trace.agent_name}</h2></div><button className="outline-btn" onClick={()=>onOpen(trace.incident_id)}>Open incident <ArrowRight size={14}/></button></div><div className="trace-body"><div className="section-label">SUPPORTED FINDING</div><p className="lead">{trace.finding}</p><div className="section-label">EVIDENCE EVENT IDS</div><div className="evidence-ids">{trace.evidence_event_ids.map(id=><code key={id}>{id}</code>)}</div><div className="section-label">VALIDATED TOOL CALLS</div>{trace.tool_calls.map((call,i)=><details className="tool-call" key={call.call_id}><summary><span className="chain-number">{String(i+1).padStart(2,"0")}</span><b>{call.tool_name}</b><Pill tone={call.status==="ok"?"teal":"red"}>{call.status}</Pill><small>{call.duration_ms.toFixed(1)} ms</small></summary><div><div className="eyebrow">ARGUMENTS</div><pre>{JSON.stringify(call.arguments,null,2)}</pre><div className="eyebrow">RESULT</div><pre>{JSON.stringify(call.result,null,2)}</pre></div></details>)}</div></>:<Empty title="Select an execution" description="Tool arguments, results and evidence appear here."/>}</section></div>;
}
function ReplayView({world,replayWorld,at,onJump,onLive,onEvent}:{world:World;replayWorld:World|null;at:number|null;onJump:(n:number)=>void;onLive:()=>void;onEvent:(e:Event)=>void}){const visible=replayWorld??world;const current=at??world.sequence;return <><div className="replay-console" data-guide-focus="6"><div><div className="eyebrow">TIME TRAVEL · {world.run.run_id.slice(0,8).toUpperCase()}</div><h2>Event #{current} <span className="muted-copy">of {world.sequence}</span></h2><p>Projection rebuilt by replaying recorded events through this sequence.</p></div><div className="replay-controls"><button className="outline-btn" onClick={()=>onJump(Math.max(0,current-1))}>← Step back</button><button className="outline-btn" onClick={()=>onJump(Math.min(world.sequence,current+1))}>Step forward →</button><button className="primary-btn" onClick={onLive}>Return live</button></div><input type="range" min="0" max={world.sequence} value={current} onChange={e=>onJump(Number(e.target.value))} aria-label="Replay sequence"/></div><div className="kpi-strip replay-kpis"><Metric label="ORDERS AT EVENT" value={visible.metrics.order_count}/><Metric label="SELLABLE STOCK" value={visible.metrics.available_stock}/><Metric label="RESERVED" value={visible.metrics.reserved_stock}/><Metric label="OPEN INCIDENTS" value={visible.metrics.open_incidents}/><Metric label="NET SALES" value={inr(visible.metrics.net_sales_paise)}/></div><section className="surface"><div className="panel-head"><div><div className="eyebrow">RECORDED HISTORY</div><h2>Event sequence</h2></div></div><div className="event-list replay-list">{world.events.filter(e=>e.sequence_number>=Math.max(1,current-15)&&e.sequence_number<=Math.min(world.sequence,current+10)).map(e=><div key={e.event_id} className={e.sequence_number===current?"replay-active":""}><EventRow event={e} onClick={()=>{onJump(e.sequence_number);onEvent(e)}}/></div>)}</div></section></>}

function BenchmarksView({world}:{world:World}){const events=world.events;const e=events.filter(x=>x.event_type==="IncidentDetected");const tests=[{label:"Incident evidence coverage",passed:world.incidents.filter(i=>i.evidence_event_ids.length>0).length,total:world.incidents.length},{label:"Stock nonnegative",passed:Object.values(world.state.stock).filter(s=>Math.min(s.sellable,s.reserved,s.quarantined,s.inspection_pending)>=0).length,total:Object.keys(world.state.stock).length},{label:"Settlement reconciliation",passed:Object.values(world.state.settlements).filter(s=>s.received_paise===null||s.reconciled).length,total:Object.keys(world.state.settlements).length},{label:"Event sequence coverage",passed:events.filter((x,i)=>i===0||x.sequence_number===events[i-1].sequence_number+1).length,total:events.length},{label:"Incident detection events",passed:e.length,total:world.incidents.length}];return <><BenchmarkReportPanel/><LiveFixtureReportPanel/><div className="benchmark-intro"><Beaker size={24}/><div><b>Current-run evaluation</b><p>Measured against the visible event ledger. Small sample sizes are preliminary. No AI uplift is claimed.</p></div><Pill tone="muted">Seed {world.run.seed}</Pill></div><div className="benchmark-grid">{tests.map(test=><div className="benchmark-card" key={test.label}><div className="eyebrow">INTEGRITY CHECK</div><h3>{test.label}</h3><div className="benchmark-score">{test.total?Math.round(test.passed/test.total*100):0}<span>%</span></div><div className="progress"><div style={{width:`${test.total?test.passed/test.total*100:0}%`}}/></div><p>{test.passed} / {test.total} records checked{test.total===0?" · insufficient data":""}</p></div>)}</div><div className="surface benchmark-foot"><b>Reproducibility</b><p>Run ID {world.run.run_id} · seed {world.run.seed} · {world.sequence} recorded events. Full regression tests are available with <code>python -m pytest apps/api/tests</code>.</p></div></>}

function SettingsView({world,onLaunch,onControl}:{world:World;onLaunch:()=>void;onControl:(action:string,speed?:number)=>void}){return <div className="settings-grid"><section className="surface"><div className="eyebrow">WORLD CONFIGURATION</div><h2>Aaranya Commerce</h2><div className="settings-line"><span>Run ID</span><code>{world.run.run_id}</code></div><div className="settings-line"><span>Seed</span><strong>{world.run.seed}</strong></div><div className="settings-line"><span>Virtual clock</span><strong>{formatClock(world.run.clock)}</strong></div><div className="settings-line"><span>Playback status</span><Pill tone={world.run.status==="running"?"teal":"muted"}>{world.run.status}</Pill></div><button className="outline-btn" onClick={onLaunch}><RotateCcw size={16}/> Reset to seed 2026</button></section><section className="surface"><div className="eyebrow">ENGINE CONTROLS</div><h2>Simulation speed</h2><p>Each tick appends a deterministic order lifecycle to the server ledger.</p><div className="setting-controls">{[1,5,10].map(n=><button key={n} className={world.run.speed===n?"primary-btn":"outline-btn"} onClick={()=>onControl("speed",n)}>{n}×</button>)}</div><div className="settings-line"><span>Execution mode</span><Pill>Deterministic demo</Pill></div><div className="settings-line"><span>Persistence</span><strong>Local SQLite ledger</strong></div></section></div>}



function GuidedPanel({step,busy,error,sensitivity,onNext,onClose}:{step:number;busy:boolean;error:string;sensitivity:{rows:{value:number;branch_c:{unfilled_demand:number;recovery_hours:number|null}}[]}|null;onNext:()=>void;onClose:()=>void}) {
  const steps=[
    {title:"A living business",body:"Orders, captures and stock movements come from the event ledger. Watch the counters move, then introduce a fault.",action:"Trigger return fault"},
    {title:"Evidence before action",body:"The invalid stock claim was recorded, but the authoritative ledger refused to sell an uninspected unit. Inspect the causal graph and source event.",action:"Test three futures"},
    {title:"Compare decisions",body:"Three isolated ledgers start from one event: no action, immediate recovery, and delayed review. Compare their calculated metrics and timelines.",action:"Test review delay"},
    {title:"Sensitivity to delay",body:"The delay sweep runs new saved branches for 0, 24 and 48 hours. Outcomes use one deterministic seed and are not confidence intervals.",action:"Approve quarantine"},
    {title:"Verify the recovery",body:"The sandbox action produced a ReturnInspected event and a deterministic verification result. No real provider action occurred.",action:"Open Decision Receipt"},
    {title:"Decision Receipt",body:"A printable receipt opens with evidence events, three branch run IDs, decision status, verification and a reproducibility manifest.",action:"Replay the chain"},
    {title:"Rewind the ledger",body:"Use the sequence slider or step controls to reconstruct earlier business state. Return live when you finish.",action:"Finish tour"},
  ];
  const current=steps[step];
  return <div className="guided-panel" role="region" aria-label={`Guided demo step ${step+1} of ${steps.length}`}><div className="guided-top"><span>GUIDED DEMO · REPRODUCIBLE</span><button onClick={onClose} aria-label="Close guided tour"><X size={15}/></button></div><div className="guided-progress" aria-hidden="true">{steps.map((_,index)=><span key={index} className={index<=step?"active":""}/>)}</div><h3>{current.title}</h3><p>{current.body}</p>{error&&<p className="guided-error" role="alert">{error}</p>}{step===3&&sensitivity&&<div className="guided-sensitivity">{sensitivity.rows.map(item=><span key={item.value}>{item.value}h review: {item.branch_c.recovery_hours?.toFixed(1) ?? "unverified"}h recovery</span>)}</div>}<button className="primary-btn" onClick={onNext} disabled={busy}>{error?`Retry · ${current.action}`:current.action}<ArrowRight size={14}/></button></div>;
}
