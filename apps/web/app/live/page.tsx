"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, ArrowRight, Beaker, CircleAlert, Database, FastForward, FileClock, GitBranch, LayoutGrid, LogOut, Menu, RotateCcw, Search, Settings2, ShieldCheck, Upload, Wallet, X } from "lucide-react";
import { API, inr, name } from "@/lib/api";
import { liveApi as api } from "@/lib/live-api";
import { LiveEvidenceGraph, type EvidenceGraph } from "@/components/live-evidence-graph";
import { ThreeFuturesReplay } from "@/components/three-futures/three-futures-replay";
import { BusinessNetwork } from "@/components/command-center/business-network";
import { MetricContributions, type Contribution } from "@/components/decision-lab/metric-contributions";
import { LiveReplay } from "@/components/live-replay";

type Account = { id: string; email: string; workspaces: { id: string; name: string }[] };
type Incident = { id: string; kind: string; classification: string; status: string; summary: string; evidence: { kind: string; id: string; external_id: string }[]; sources?: Source[]; investigations?: Investigation[]; reviews?: { status: string; note: string; reviewer_id: string; created_at: string }[]; conclusions?: { outcome: string; note: string; reviewer_id: string; created_at: string }[]; created_at: string };
type AgentJob = { id: string; incident_id: string; status: string; phase: string; attempt: number; cancel_requested: boolean; model_call_slots_reserved: number; max_model_calls: number; estimated_cost_microusd: number; investigation_id: string | null; last_error: string | null; progress: { specialist?: string; completed_plans: number; evidence_reads: number; evidence_gaps: string[] } };
type Investigation = { id: string; incident_id?: string; mode: string; finding: string; evidence: unknown[]; proposals?: { id: string; description: string; status: string; evidence_ids: string[]; simulation_available: boolean }[]; structured?: { claims: { text: string; classification: string; source_ids: string[] }[]; root_cause_hypotheses: { text: string; classification: string; source_ids: string[] }[]; unresolved_questions: string[]; execution_mode: string; policy_validation: { proposal_id: string; status: string }[] }; tool_trace: { tool: string; result_ids?: string[]; status?: string }[]; created_at?: string };
type Source = { id: string; external_id?: string; fields?: Record<string, unknown>; source_row?: Record<string, unknown>; observation_refs?: { id: string; event_type: string; ingested_at: string; import_batch_id: string | null; webhook_delivery_id: string | null }[]; kind?: string; status?: string; filename?: string; errors?: unknown[] };
type Dashboard = { mode: string; workspace_id: string; analysis_models?: { incident_llm: "credentials_present" | "unconfigured"; demand_model: string }; metrics: { gross_orders_paise: number; order_count: number; product_count: number; sellable_units: number; reserved_units: number; captured_paise: number; bank_received_paise: number; return_count: number; open_incidents: number }; record_counts: Record<string, number>; connections: Record<string, string>; orders: { id: string; external_id: string; status: string; channel: string; total_paise: number; source: string; import_batch_id: string }[]; inventory: { id: string; external_id: string; sku: string; location: string; sellable: number; reserved: number; quarantined: number; observed_at: string }[]; products: { id: string; external_id: string; sku: string; name: string; price_paise: number }[]; returns: { id: string; external_id: string; sku: string; quantity: number; condition: string; status: string }[]; incidents: Incident[]; recent_imports: { id: string; kind: string; imported_count: number; status: string }[] };
type Money = { payment: { id: string; external_id: string; amount_paise: number; fee_paise: number; fee_tax_paise: number }; refunds: { id: string; external_id: string; amount_paise: number }[]; prior_refund: { id: string; external_id: string; amount_paise: number } | null; settlement: { id: string; external_id: string; amount_paise: number } | null; bank_transactions: { id: string; external_id: string; amount_paise: number }[]; expected_paise: number; bank_total_paise: number; status: string; adjustments?: { reason_code: string; amount_paise: number; source: { kind: string; id: string }; detail: string }[]; reason_codes?: string[]; provider_variance_paise?: number | null; bank_variance_paise?: number | null; source_path?: { kind: string; id: string }[] };
type Batch = { id: string; kind: string; filename: string; status: string; imported_count: number; duplicate_count: number; errors: { row: number; message: string }[]; created_at: string };
type Preview = { headers: string[]; mapping: Record<string, string>; required_fields: string[]; rows: number; valid_count: number; duplicate_count: number; errors: { row: number; message: string }[]; preview: Record<string, string>[] };
type ProviderStatus = { status: string; configured: boolean; account: string | null; can_sync: boolean; last_successful_sync_at: string | null; last_error: string | null; webhook_configured?: boolean; webhook_path?: string | null };
type Observation = { id: string; kind: string; source_system: string; provider_object_id: string; event_type: string; ingested_at: string; import_batch_id?: string | null; webhook_delivery_id?: string | null; previous_observation_id?: string | null; normalized_record_id?: string | null };
type AuditEntry = { id: string; action: string; target_id: string; user_id: string; detail: Record<string, unknown>; created_at: string };
type RazorpaySettlement = { id: string; external_id: string; utr: string | null; amount_paise: number; calculated_paise: number | null; bank_total_paise: number; bank_external_ids: string[]; status: string; lines: { id: string; entity_id: string; type: string; credit_paise: number; debit_paise: number; payment_id: string | null }[] };
type DemandModel = { status: "ready" | "insufficient_history"; model_version: string; horizon_hours: number; forecast_attempts: number | null; sample_orders: number; active_days: number; latest_order_age_hours: number | null; message: string };
type Scenario = { id: string; snapshot_id: string; source_digest?: string; seed: number; baseline: Outcome; recovery: Outcome; branches?: Outcome[]; assumptions?: string[]; disruption?: Record<string, unknown>; demand_model?: DemandModel | null; created_at?: string };
type Outcome = { label: string; starting_sellable: number; fulfilled_units: number; unfilled_attempts: number; hypothetical_revenue_paise: number; stock_risk_units: number; ending_sellable: number; additional_operational_cost_paise?: number; refund_exposure_paise?: number; potential_refund_exposure_paise?: number; orders_fulfilled?: number; orders_delayed?: number; policy_violations?: number; recovery_step?: number | null; recovery_completed_at?: number | null; events: { step: number; event: string; requested?: number; fulfilled?: number; remaining?: number; demand_id?: string; event_id?: string; time_hours?: number }[]; source_record_ids: string[] };
type Sensitivity = { variable: string; repetitions: number; rows: { value: number; branches: { branch_index: number; label: string; mean_unfilled_attempts: number; min_unfilled_attempts: number; max_unfilled_attempts: number }[] }[]; note: string };
type Difference = { metric: string; left_value: number; right_value: number; difference: number; attribution_status?: string; demand_events: { demand_id: string; left: Outcome["events"][number]; right: Outcome["events"][number] }[]; left_contributions: Contribution[]; right_contributions: Contribution[]; explanation: string };
type Backtest = { status: string; message?: string; run_id?: string; metric?: string; model_version?: string; evaluated_samples: number; errors?: { mae: number | null; rmse: number | null; bias: number | null; normalized_mae: number | null }; baselines?: Record<string, { mae: number | null }>; origins?: { cutoff: string; prediction: number; actual: number; baselines: Record<string, number> }[]; limitations?: string[]; source_coverage?: { first_ingested_at: string; last_ingested_at: string; order_observations: number } };
type View = "Live Twin" | "Incidents" | "Scenario Lab" | "Money Trail" | "Agent Traces" | "Replay" | "Record History" | "Benchmarks" | "Integrations" | "Settings";
const nav: { label: View; icon: typeof Activity }[] = [
  { label: "Live Twin", icon: LayoutGrid }, { label: "Incidents", icon: CircleAlert },
  { label: "Scenario Lab", icon: GitBranch }, { label: "Money Trail", icon: Wallet },
  { label: "Agent Traces", icon: Activity }, { label: "Replay", icon: RotateCcw },
  { label: "Record History", icon: FileClock }, { label: "Benchmarks", icon: Beaker },
  { label: "Integrations", icon: Upload }, { label: "Settings", icon: Settings2 },
];
const liveTour: { view: View; title: string; description: string }[] = [
  { view: "Live Twin", title: "See your business", description: "The network, source inspector and observation ledger show records from this workspace." },
  { view: "Integrations", title: "Bring in source records", description: "Import CSV exports or connect a provider. Validate rows and map columns before committing a batch." },
  { view: "Incidents", title: "Follow the evidence", description: "Open a detected discrepancy, inspect its source rows, run an investigation and record a review." },
  { view: "Scenario Lab", title: "Compare three futures", description: "Create a snapshot of observed inventory, adjust assumptions, compare branches and verify a saved replay." },
  { view: "Replay", title: "Inspect recorded history", description: "Step through ingested observations and open the exact source payload for each version." },
  { view: "Record History", title: "Audit the decision", description: "Review imports, observations, saved scenarios and recorded human actions." },
];
const importKinds = ["products", "inventory", "orders", "order_items", "returns", "payments", "refunds", "settlements", "bank_transactions"];

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><div className="metric-label">{label}</div><div className="metric-value">{value}</div></div>;
}

function Blank({ title, text }: { title: string; text: string }) {
  return <div className="empty"><Database size={24}/><h3>{title}</h3><p>{text}</p></div>;
}

export default function LivePage() {
  const [account, setAccount] = useState<Account | null>(null);
  const [checked, setChecked] = useState(false);
  const [workspaceId, setWorkspaceId] = useState("");
  const refreshVersion = useRef(0);
  const [loadingWorkspace, setLoadingWorkspace] = useState(false);
  const [view, setView] = useState<View>("Live Twin");
  const [mobileNav, setMobileNav] = useState(false);
  const [tourStep, setTourStep] = useState<number | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [shopify, setShopify] = useState<ProviderStatus | null>(null);
  const [shopDomain, setShopDomain] = useState("");
  const [razorpay, setRazorpay] = useState<ProviderStatus | null>(null);
  const [razorpayKeyId, setRazorpayKeyId] = useState("");
  const [razorpaySecret, setRazorpaySecret] = useState("");
  const [razorpayWebhookSecret, setRazorpayWebhookSecret] = useState("");
  const [razorpaySettlements, setRazorpaySettlements] = useState<RazorpaySettlement[]>([]);
  const [observations, setObservations] = useState<Observation[]>([]);
  const [hasOlderObservations, setHasOlderObservations] = useState(false);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [selectedObservation, setSelectedObservation] = useState<Record<string, unknown> | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [money, setMoney] = useState<Money[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [investigations, setInvestigations] = useState<Investigation[]>([]);
  const [agentStatus, setAgentStatus] = useState<{agentic_available: boolean; auto_worker_enabled: boolean} | null>(null);
  const [agentJobs, setAgentJobs] = useState<AgentJob[]>([]);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selectedIncident, setSelectedIncident] = useState<Incident | null>(null);
  const [evidenceGraph, setEvidenceGraph] = useState<EvidenceGraph | null>(null);
  const [selectedSource, setSelectedSource] = useState<Source | null>(null);
  const [selectedDomain, setSelectedDomain] = useState("Orders");
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [replayOpen, setReplayOpen] = useState(false);
  const [snapshotId, setSnapshotId] = useState("");
  const [scenarioSku, setScenarioSku] = useState("");
  const [scenarioDemand, setScenarioDemand] = useState(30);
  const [demandMode, setDemandMode] = useState<"manual" | "observed">("manual");
  const [demandHorizon, setDemandHorizon] = useState(24);
  const [demandPreview, setDemandPreview] = useState<DemandModel | null>(null);
  const [scenarioSeed, setScenarioSeed] = useState(2026);
  const [scenarioDisruption, setScenarioDisruption] = useState(0);
  const [scenarioUnitMultiplier, setScenarioUnitMultiplier] = useState(1);
  const [scenarioExtras, setScenarioExtras] = useState({ supplier_lead_time: 0, restock_units: 0, inspection_delay: 0, fulfillment_cost_paise: 0, expedited_delivery_cost_paise: 0, unfulfilled_cost_paise: 0, return_processing_time: 0, procurement_unit_cost_paise: 0, holding_cost_paise_per_unit_hour: 0, return_inspection_cost_paise: 0 });
  const [inspectionResult, setInspectionResult] = useState("failed");
  const [supplierAvailable, setSupplierAvailable] = useState(true);
  const [sensitivity, setSensitivity] = useState<Sensitivity | null>(null);
  const [difference, setDifference] = useState<Difference | null>(null);
  const [differenceMetric, setDifferenceMetric] = useState("unfilled_attempts");
  const [sensitivityVariable, setSensitivityVariable] = useState("demand");
  const [backtest, setBacktest] = useState<Backtest | null>(null);
  const [backtestCutoff, setBacktestCutoff] = useState("");
  const [backtestHorizon, setBacktestHorizon] = useState(24);
  const [snapshotCutoff, setSnapshotCutoff] = useState("");
  const [selectedBranch, setSelectedBranch] = useState(1);
  const [strategyNote, setStrategyNote] = useState("");
  const [kind, setKind] = useState("orders");
  const [file, setFile] = useState<File | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<Preview | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [register, setRegister] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [moneyQuery, setMoneyQuery] = useState("");
  const [reviewNote, setReviewNote] = useState("");
  const [conclusionOutcome, setConclusionOutcome] = useState("explained");

  useEffect(() => { api<Account>("/live/me").then(data => {
    setAccount(data); setWorkspaceId(data.workspaces[0]?.id ?? "");
  }).catch(err => { if (!String(err).includes("401")) setError(String(err)); }).finally(() => setChecked(true)); }, []);

  const refresh = useCallback(async (id: string) => {
    if (!id) return;
    const version = ++refreshVersion.current;
    setLoadingWorkspace(true);
    const base = `/live/workspaces/${id}`;
    try {
      const [d, i, m, b, t, s, shop, pay, recon, history, actions, agent, jobs] = await Promise.all([
      api<Dashboard>(base + "/dashboard"), api<Incident[]>(base + "/incidents"),
      api<Money[]>(base + "/money"), api<Batch[]>(base + "/imports"),
      api<Investigation[]>(base + "/investigations"), api<Scenario[]>(base + "/scenarios"),
      api<ProviderStatus>(base + "/shopify/status"),
      api<ProviderStatus>(base + "/razorpay/status"),
      api<RazorpaySettlement[]>(base + "/money/razorpay-settlements"),
      api<Observation[]>(base + "/observations?limit=500"),
      api<AuditEntry[]>(base + "/audit"),
      api<{agentic_available: boolean; auto_worker_enabled: boolean}>(base + "/agent/status"),
      api<AgentJob[]>(base + "/agent/jobs"),
      ]);
      if (version !== refreshVersion.current) return;
      setDashboard(d); setIncidents(i); setMoney(m); setBatches(b); setInvestigations(t); setScenarios(s);
      setShopify(shop); setAgentStatus(agent); setAgentJobs(jobs);
      setRazorpay(pay); setRazorpaySettlements(recon); setObservations(history); setHasOlderObservations(history.length === 500); setAudit(actions);
      setScenarioSku(current => current || d.inventory[0]?.sku || "");
    } catch (error) {
      if (version === refreshVersion.current) throw error;
    } finally {
      if (version === refreshVersion.current) setLoadingWorkspace(false);
    }
  }, []);
  useEffect(() => { if (workspaceId) refresh(workspaceId).catch(err => setError(String(err))); }, [workspaceId, refresh]);

  const act = async (work: () => Promise<void>) => {
    setBusy(true); setError(""); setNotice("");
    try { await work(); } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };
  const authenticate = () => act(async () => {
    const data = await api<Account>(register ? "/live/register" : "/live/login", {
      method: "POST", body: JSON.stringify(register ? { email, password, workspace_name: workspaceName } : { email, password }),
    });
    setAccount(data); setWorkspaceId(data.workspaces[0]?.id ?? ""); setPassword("");
  });
  const signOut = () => act(async () => {
    await api("/live/logout", { method: "POST", body: "{}" });
    refreshVersion.current += 1;
    setAccount(null); setWorkspaceId(""); setDashboard(null); setSelectedIncident(null); setTourStep(null);
  });
  const base = `/live/workspaces/${workspaceId}`;
  useEffect(() => {
    if (!snapshotId || !scenarioSku || !workspaceId) { setDemandPreview(null); return; }
    let active = true;
    api<DemandModel>(`${base}/snapshots/${snapshotId}/demand-model?sku=${encodeURIComponent(scenarioSku)}&horizon_hours=${demandHorizon}`)
      .then(result => { if (active) setDemandPreview(result); })
      .catch(() => { if (active) setDemandPreview(null); });
    return () => { active = false; };
  }, [workspaceId, snapshotId, scenarioSku, demandHorizon, base]);
  useEffect(() => {
    if (demandMode === "observed" && sensitivityVariable === "demand") setSensitivityVariable("restock_units");
  }, [demandMode, sensitivityVariable]);
  const loadOlderObservations = () => act(async () => {
    const oldest = observations.at(-1);
    if (!oldest) return;
    const currentVersion = refreshVersion.current;
    const older = await api<Observation[]>(`${base}/observations?limit=500&before_id=${encodeURIComponent(oldest.id)}`);
    if (currentVersion !== refreshVersion.current) return;
    setObservations(current => [...current, ...older]);
    setHasOlderObservations(older.length === 500);
  });
  const connectShopify = () => act(async () => {
    const result = await api<{ authorization_url: string }>(base + "/shopify/authorize", {
      method: "POST", body: JSON.stringify({ shop: shopDomain.trim() }),
    });
    window.location.assign(result.authorization_url);
  });
  const syncShopify = () => act(async () => {
    const result = await api<{ counts: Record<string, number> }>(base + "/shopify/sync", { method: "POST", body: "{}" });
    await refresh(workspaceId);
    setNotice(`Shopify sync completed: ${result.counts.products} products, ${result.counts.orders} orders, ${result.counts.inventory} inventory levels.`);
  });
  const disconnectShopify = () => act(async () => {
    await api(base + "/shopify/disconnect", { method: "POST", body: "{}" });
    await refresh(workspaceId); setNotice("Shopify disconnected. Previously imported source records remain in this workspace.");
  });
  const connectRazorpay = () => act(async () => {
    await api(base + "/razorpay/connect", { method: "POST", body: JSON.stringify({ key_id: razorpayKeyId.trim(), key_secret: razorpaySecret }) });
    setRazorpaySecret(""); await refresh(workspaceId); setNotice("Razorpay credentials verified. Sync to import captured payments and settlements.");
  });
  const syncRazorpay = () => act(async () => {
    const result = await api<{ counts: Record<string, number> }>(base + "/razorpay/sync", { method: "POST", body: "{}" });
    await refresh(workspaceId); setNotice(`Razorpay sync completed: ${result.counts.payments} captured payments, ${result.counts.refunds} refunds, ${result.counts.settlements} settlements.`);
  });
  const configureRazorpayWebhook = () => act(async () => {
    await api(base + "/razorpay/webhook-secret", { method: "POST", body: JSON.stringify({ secret: razorpayWebhookSecret }) });
    setRazorpayWebhookSecret(""); await refresh(workspaceId);
    setNotice("Webhook secret saved. Register the displayed URL and matching secret in your Razorpay dashboard.");
  });
  const disconnectRazorpay = () => act(async () => {
    await api(base + "/razorpay/disconnect", { method: "POST", body: "{}" });
    await refresh(workspaceId); setNotice("Razorpay disconnected. Previously observed records remain available.");
  });
  const openIncident = (id: string) => act(async () => {
    const [incident, graph] = await Promise.all([api<Incident>(base + `/incidents/${id}`), api<EvidenceGraph>(base + `/incidents/${id}/evidence-graph`)]);
    setSelectedIncident(incident); setEvidenceGraph(graph); setReviewNote(""); setView("Incidents");
  });
  const openSource = (kindName: string, id: string) => act(async () => {
    setSelectedSource(await api<Source>(base + `/sources/${kindName}/${id}`));
  });
  const openObservation = (id: string) => act(async () => {
    setSelectedObservation(await api<Record<string, unknown>>(base + `/observations/${id}`));
  });
  const investigate = (id: string, agentic = false) => act(async () => {
    await api(base + `/incidents/${id}/investigate${agentic ? "/agentic" : ""}`, { method: "POST", body: "{}" });
    setSelectedIncident(await api<Incident>(base + `/incidents/${id}`)); await refresh(workspaceId);
  });
  const queueAgent = (id: string) => act(async () => {
    const job = await api<AgentJob>(base + "/agent/jobs", {
      method: "POST", body: JSON.stringify({ incident_id: id }),
    });
    setNotice(`Agent job ${job.id.slice(0, 8)} queued. Open Agent Traces to inspect progress.`);
    await refresh(workspaceId);
  });
  const controlAgent = (jobId: string, action: "cancel" | "resume") => act(async () => {
    await api(base + `/agent/jobs/${jobId}/${action}`, { method: "POST", body: "{}" });
    await refresh(workspaceId);
    if (selectedIncident) setSelectedIncident(await api<Incident>(base + `/incidents/${selectedIncident.id}`));
  });
  const refreshAgentJobs = () => act(async () => {
    setAgentJobs(await api<AgentJob[]>(base + "/agent/jobs"));
    if (selectedIncident) setSelectedIncident(await api<Incident>(base + `/incidents/${selectedIncident.id}`));
  });
  const reviewIncident = (status: "acknowledged" | "reviewed" | "open") => act(async () => {
    if (!selectedIncident || reviewNote.trim().length < 3) throw new Error("Add a review note of at least three characters");
    await api(base + `/incidents/${selectedIncident.id}/review`, {
      method: "POST", body: JSON.stringify({ status, note: reviewNote.trim() }),
    });
    setSelectedIncident(await api<Incident>(base + `/incidents/${selectedIncident.id}`));
    setReviewNote(""); await refresh(workspaceId);
  });
  const concludeIncident = () => act(async () => {
    if (!selectedIncident || reviewNote.trim().length < 3) throw new Error("Add a conclusion note of at least three characters");
    await api(base + `/incidents/${selectedIncident.id}/conclude`, { method: "POST",
      body: JSON.stringify({ outcome: conclusionOutcome, note: reviewNote.trim() }) });
    setSelectedIncident(await api<Incident>(base + `/incidents/${selectedIncident.id}`));
    setReviewNote(""); await refresh(workspaceId);
  });
  const previewFile = () => act(async () => {
    if (!file) throw new Error("Choose a CSV file first");
    const body = new FormData(); body.append("kind", kind); body.append("mapping", JSON.stringify(mapping)); body.append("file", file);
    const result = await api<Preview>(base + "/imports/preview", { method: "POST", body });
    setPreview(result); setMapping(result.mapping);
  });
  const commitImport = () => act(async () => {
    if (!file || !preview) throw new Error("Preview the CSV before importing");
    const body = new FormData(); body.append("kind", kind); body.append("mapping", JSON.stringify(mapping)); body.append("file", file);
    const result = await api<{ status: string; imported_count: number; duplicate_count: number; errors: { row: number; message: string }[] }>(base + "/imports", { method: "POST", body });
    await refresh(workspaceId);
    setNotice(result.status === "complete" ? `${result.imported_count} records imported; ${result.duplicate_count} duplicates skipped.` : "Import rejected. Review row errors in Import History.");
    if (result.status === "complete") { setPreview(null); setFile(null); }
  });
  const createSnapshot = () => act(async () => {
    const snapshot = await api<{ id: string; observed_cutoff: string; limitations: string[] }>(base + "/snapshots", { method: "POST", body: JSON.stringify({ observed_cutoff: snapshotCutoff ? new Date(snapshotCutoff).toISOString() : null }) });
    setSnapshotId(snapshot.id); setNotice(`Observed snapshot created at ${new Date(snapshot.observed_cutoff).toLocaleString()}. ${snapshot.limitations.join(" ")}`);
  });
  const compare = () => act(async () => {
    if (!snapshotId) throw new Error("Create a snapshot first");
    const result = await api<Scenario>(base + `/snapshots/${snapshotId}/compare`, { method: "POST",
      body: JSON.stringify({ sku: scenarioSku, incident_id: selectedIncident?.id ?? null, seed: scenarioSeed, demand: scenarioDemand, demand_mode: demandMode, horizon_hours: demandHorizon, disruption_units: scenarioDisruption, unit_multiplier: scenarioUnitMultiplier, ...scenarioExtras, inspection_result: inspectionResult, supplier_available: supplierAvailable }) });
    setScenario(result); setReplayOpen(false); setSensitivity(null); setDifference(null); await refresh(workspaceId);
  });
  const openSavedScenario = (item: Scenario) => {
    const saved = item.disruption ?? {};
    setScenario(item); setReplayOpen(false); setSnapshotId(item.snapshot_id);
    setScenarioSku(String(saved.sku ?? "")); setScenarioSeed(item.seed);
    setScenarioDemand(Number(saved.demand ?? 30));
    setDemandMode(saved.demand_mode === "observed" ? "observed" : "manual");
    setDemandHorizon(Number(saved.horizon_hours ?? 24));
    setScenarioDisruption(Number(saved.unavailable_units ?? 0));
    setScenarioUnitMultiplier(Number(saved.unit_multiplier ?? 1));
    setScenarioExtras(current => Object.fromEntries(Object.keys(current).map(key =>
      [key, Number(saved[key] ?? 0)])) as typeof current);
    setInspectionResult(String(saved.inspection_result ?? "failed"));
    setSupplierAvailable(Boolean(saved.supplier_available ?? true));
    setSensitivity(null); setDifference(null); setView("Scenario Lab");
  };
  const runSensitivity = () => act(async () => {
    if (!scenario) throw new Error("Run a scenario first");
    if (scenarioStale) throw new Error("Rerun the scenario after changing assumptions");
    const baseValue = sensitivityVariable === "demand" ? scenarioDemand : sensitivityVariable === "restock_units" ? scenarioExtras.restock_units : scenarioExtras.inspection_delay;
    const values = [...new Set([Math.max(0, Math.floor(baseValue / 2)), baseValue, Math.min(500, baseValue * 2 || 1)])];
    const result = await api<Sensitivity>(base + `/scenarios/${scenario.id}/sensitivity`, { method: "POST", body: JSON.stringify({ variable: sensitivityVariable, values, seeds: [scenarioSeed, scenarioSeed + 1, scenarioSeed + 2, scenarioSeed + 3, scenarioSeed + 4] }) });
    setSensitivity(result);
  });
  const inspectDifference = () => act(async () => {
    if (!scenario) throw new Error("Run a scenario first");
    if (scenarioStale) throw new Error("Rerun the scenario after changing assumptions");
    setDifference(await api<Difference>(base + `/scenarios/${scenario.id}/difference?left=0&right=1&metric=${differenceMetric}`));
  });
  const runBacktest = () => act(async () => {
    if (!backtestCutoff) throw new Error("Choose a historical cutoff");
    setBacktest(await api<Backtest>(base + "/backtests", { method: "POST",
      body: JSON.stringify({ cutoff: new Date(backtestCutoff).toISOString(), horizon_hours: backtestHorizon }) }));
  });
  const reviewStrategy = () => act(async () => {
    if (!scenario) throw new Error("Run a scenario first");
    if (scenarioStale) throw new Error("Rerun the scenario after changing assumptions");
    await api(base + `/scenarios/${scenario.id}/review`, { method: "POST",
      body: JSON.stringify({ branch_index: selectedBranch, note: strategyNote.trim() }) });
    setNotice("Preferred strategy recorded for human review. No external action was taken.");
    setStrategyNote(""); await refresh(workspaceId);
  });
  const verifySandbox = () => act(async () => {
    if (!scenario) throw new Error("Run a scenario first");
    if (scenarioStale) throw new Error("Rerun the scenario after changing assumptions");
    const result = await api<{ verified: boolean; branch_digest: string }>(base + `/scenarios/${scenario.id}/verify`, { method: "POST", body: "{}" });
    setNotice(result.verified ? `Sandbox replay verified against saved branches (${result.branch_digest.slice(0, 12)}).` : "Sandbox replay differs from the saved branches. Review the audit trail.");
    await refresh(workspaceId);
  });

  if (!checked) return <div className="live-auth-shell"><div className="live-auth-card">Checking Live Operations session...</div></div>;
  if (!account) return <div className="live-auth-shell"><div className="live-auth-card">
    <div className="brand-mark"><Database size={22}/></div><div className="eyebrow">OPSVERSE / LIVE OPERATIONS</div>
    <h1>{register ? "Create your workspace" : "Sign in to your business"}</h1>
    <p>Real records only. Your live workspace starts empty until you import a CSV or connect a provider.</p>
    {error && <div className="live-alert">{error}</div>}
    <label>Email<input type="email" value={email} onChange={event => setEmail(event.target.value)} autoComplete="email"/></label>
    <label>Password<input type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete={register ? "new-password" : "current-password"}/></label>
    {register && <label>Business name<input value={workspaceName} onChange={event => setWorkspaceName(event.target.value)}/></label>}
    <button className="primary-btn" onClick={authenticate} disabled={busy}>{busy ? "Working..." : register ? "Create account" : "Sign in"} <ArrowRight size={15}/></button>
    <button className="text-btn" onClick={() => { setRegister(!register); setError(""); }}>{register ? "Already have an account? Sign in" : "Create a workspace"}</button>
    <a href="/demo" className="live-demo-link">Explore the isolated synthetic demo <ArrowRight size={14}/></a>
  </div></div>;

  const workspace = account.workspaces.find(item => item.id === workspaceId);
  const metric = dashboard?.metrics;
  const counts = dashboard?.record_counts ?? {};
  const domainNodes = [
    ["Customer refs", counts.customers ?? 0], ["Orders", counts.orders ?? 0], ["Products", counts.products ?? 0],
    ["Warehouse", counts.inventory_snapshots ?? 0], ["Returns", counts.returns ?? 0],
    ["Payments", counts.payments ?? 0], ["Settlements", counts.settlements ?? 0],
    ["Bank", counts.bank_transactions ?? 0], ["Incidents", metric?.open_incidents ?? 0],
  ] as const;
  const filteredMoney = money.filter(item => !moneyQuery || [item.payment.external_id, item.settlement?.external_id,
    ...item.refunds.map(refund => refund.external_id), item.prior_refund?.external_id,
    ...item.bank_transactions.map(bank => bank.external_id)].some(value => value?.toLowerCase().includes(moneyQuery.toLowerCase())));
  const scenarioStale = !!scenario && (!!scenario.disruption && (
    scenario.snapshot_id !== snapshotId || scenario.disruption.sku !== scenarioSku || scenario.seed !== scenarioSeed ||
    (scenario.disruption.demand_mode ?? "manual") !== demandMode || (scenario.disruption.horizon_hours ?? 24) !== demandHorizon ||
    (demandMode === "manual" && scenario.disruption.demand !== scenarioDemand) || scenario.disruption.unavailable_units !== scenarioDisruption ||
    scenario.disruption.unit_multiplier !== scenarioUnitMultiplier ||
    (Object.entries(scenarioExtras).some(([key, value]) => (scenario.disruption?.[key] ?? 0) !== value) || (scenario.disruption?.inspection_result ?? "failed") !== inspectionResult || (scenario.disruption?.supplier_available ?? true) !== supplierAvailable)
  ));
  const switchWorkspace = (id: string) => {
    refreshVersion.current += 1;
    setWorkspaceId(id); setScenario(null); setSensitivity(null); setDifference(null); setSnapshotId(""); setScenarioSku("");
    setSelectedIncident(null); setEvidenceGraph(null); setSelectedSource(null); setSelectedObservation(null);
    setDashboard(null); setIncidents([]); setMoney([]); setBatches([]); setInvestigations([]);
    setScenarios([]); setShopify(null); setRazorpay(null); setRazorpaySettlements([]);
    setObservations([]); setAudit([]);
    setError(""); setNotice("");
  };
  const moveTour = (next: number) => {
    if (next >= liveTour.length) { setTourStep(null); return; }
    setTourStep(next);
    setView(liveTour[next].view);
    setMobileNav(false);
  };

  return <div className="app-shell live-shell">
    {mobileNav && <button className="live-mobile-backdrop" aria-label="Close navigation" onClick={() => setMobileNav(false)}/>}
    <aside className={`sidebar ${mobileNav ? "mobile-open" : ""}`}><div className="brand"><div className="brand-mark"><Database size={19}/></div><div><strong>OPSVERSE</strong><small>LIVE OPERATIONS</small></div></div>
      <div className="workspace-label">BUSINESS WORKSPACE</div><div className="workspace-switch"><div className="workspace-avatar">{workspace?.name.slice(0, 2).toUpperCase()}</div><div><b>{workspace?.name}</b><small>Observed business records</small></div></div>
      {account.workspaces.length > 1 && <select className="live-workspace-select" aria-label="Business workspace" value={workspaceId} onChange={event => switchWorkspace(event.target.value)}>{account.workspaces.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}
      <div className="nav-heading">OPERATIONS</div><nav>{nav.map(item => <button key={item.label} className={`nav-item ${view === item.label ? "active" : ""}`} onClick={() => { setView(item.label); setMobileNav(false); }}><item.icon size={17}/><span>{item.label}</span>{item.label === "Incidents" && !!metric?.open_incidents && <i>{metric.open_incidents}</i>}</button>)}</nav>
       <div className="sidebar-bottom"><div className="mode-indicator"><ShieldCheck size={15}/> Observed source data</div><a href="/demo" className="live-mode-link">Open synthetic Demo Mode <ArrowRight size={14}/></a></div>
    </aside>
     <main className="main"><header className="topbar"><button className="icon-btn live-mobile-menu" onClick={() => setMobileNav(true)} aria-label="Open navigation"><Menu size={18}/></button><div className="breadcrumb">{workspace?.name.toUpperCase()} <span>/</span> <strong>{view.toUpperCase()}</strong></div>{account.workspaces.length > 1 && <select className="live-compact-workspace-select" aria-label="Business workspace" value={workspaceId} onChange={event => switchWorkspace(event.target.value)}>{account.workspaces.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}<div className="top-actions"><span className="live-source-chip">OBSERVED SOURCES</span><button className="outline-btn live-tour-launch" onClick={() => moveTour(0)}><FastForward size={14}/> Guided tour</button><button className="outline-btn" onClick={() => act(() => refresh(workspaceId))} disabled={busy}>Refresh</button><button className="primary-btn" onClick={() => { setView("Integrations"); setMobileNav(false); }}><Upload size={15}/> Import data</button><button className="icon-btn" onClick={signOut} title="Sign out"><LogOut size={16}/></button></div></header>
      {error && <div className="error-banner"><CircleAlert size={16}/>{error}<button onClick={() => setError("")} aria-label="Dismiss error"><X size={16}/></button></div>}
      {notice && <div className="live-notice">{notice}</div>}
       <div className="page-heading"><div><div className="eyebrow">SOURCE-LINKED BUSINESS INTELLIGENCE</div><h1>{view}</h1><p>{view === "Live Twin" ? "Every value below comes from imported or synchronized records in this workspace." : view === "Scenario Lab" ? "Test a future from an immutable copy of observed data." : "Inspect source records and their derived results."}</p></div><div className="run-chip">WORKSPACE <span>{workspaceId.slice(0, 8).toUpperCase()}</span></div></div>
       <div className="live-journey" aria-label="Incident to decision journey"><button className={view === "Live Twin" ? "active" : ""} onClick={() => setView("Live Twin")}><span>01 / COMMAND CENTER</span><b>Understand what happened</b></button><button className={view === "Incidents" ? "active" : ""} onClick={() => setView("Incidents")}><span>02 / INCIDENT MICROSCOPE</span><b>Follow the evidence</b></button><button className={view === "Scenario Lab" ? "active" : ""} onClick={() => setView("Scenario Lab")}><span>03 / DECISION LAB</span><b>Explore what happens next</b></button></div>
       {view === "Live Twin" && <>{!dashboard || !metric ? <Blank title={loadingWorkspace ? "Loading observed records" : "No connected business data"} text={loadingWorkspace ? "Fetching authorized workspace sources..." : "Import orders, products, inventory and finance files to populate Live Operations."}/> : <>
        <div className="live-kpis"><Metric label="GROSS ORDERS" value={inr(metric.gross_orders_paise)}/><Metric label="ORDERS" value={metric.order_count}/><Metric label="PRODUCTS" value={metric.product_count}/><Metric label="SELLABLE" value={metric.sellable_units}/><Metric label="RESERVED" value={metric.reserved_units}/><Metric label="RETURNS" value={metric.return_count}/><Metric label="CAPTURED" value={inr(metric.captured_paise)}/><Metric label="BANK RECEIVED" value={inr(metric.bank_received_paise)}/><Metric label="OPEN INCIDENTS" value={metric.open_incidents}/></div>
        {!dashboard.recent_imports.length && !dashboard.record_counts.orders && !dashboard.record_counts.inventory_snapshots && <div className="live-empty-callout"><Upload size={22}/><div><b>Your live workspace is empty</b><p>Import actual CSV exports. OpsVerse will not add synthetic records here.</p></div><button className="primary-btn" onClick={() => setView("Integrations")}>Start import</button></div>}
         <div className="live-two live-twin-layout"><section className="surface"><div className="panel-head"><div><div className="eyebrow">OBSERVED BUSINESS NETWORK</div><h2>Business network</h2></div><button className="outline-btn" onClick={() => setView("Replay")}><RotateCcw size={14}/> Replay source history</button></div><BusinessNetwork nodes={domainNodes} observations={observations} selected={selectedDomain} onSelect={setSelectedDomain}/><div className="live-network-note">Select a domain to inspect its source records. Counts are scoped to {workspace?.name}.</div></section>
         <section className="surface"><div className="panel-head"><div><div className="eyebrow">SOURCE INSPECTOR</div><h2>{selectedDomain}</h2></div></div><div className="live-inspector">{selectedDomain === "Orders" || selectedDomain === "Customer refs" ? dashboard.orders.length ? dashboard.orders.slice(0, 8).map(item => <button key={item.id} className="live-record" onClick={() => openSource("order", item.id)}><span><b>{item.external_id}</b><small>{item.channel}  /  {item.status}</small></span><strong>{inr(item.total_paise)}</strong></button>) : <Blank title="No orders" text="Import an orders CSV to show real customer demand."/> : selectedDomain === "Warehouse" ? dashboard.inventory.length ? dashboard.inventory.slice(0, 8).map(item => <button key={item.id} className="live-record" onClick={() => openSource("inventory", item.id)}><span><b>{item.sku}</b><small>{item.location}  /  {item.external_id}</small></span><strong>{item.sellable} units</strong></button>) : <Blank title="No inventory" text="Import product and inventory snapshots."/> : selectedDomain === "Products" ? dashboard.products.length ? dashboard.products.slice(0, 8).map(item => <button key={item.id} className="live-record" onClick={() => openSource("product", item.id)}><span><b>{item.sku}</b><small>{item.name} / {item.external_id}</small></span><strong>{inr(item.price_paise)}</strong></button>) : <Blank title="No products" text="Import a products CSV or synchronize a product catalog."/> : selectedDomain === "Returns" ? dashboard.returns.length ? dashboard.returns.slice(0, 8).map(item => <button key={item.id} className="live-record" onClick={() => openSource("return", item.id)}><span><b>{item.external_id}</b><small>{item.sku} / {name(item.condition)} / {name(item.status)}</small></span><strong>{item.quantity} units</strong></button>) : <Blank title="No returns" text="Import return records to inspect their source and condition."/> : selectedDomain === "Incidents" ? dashboard.incidents.length ? dashboard.incidents.slice(0, 8).map(item => <button key={item.id} className="live-record" onClick={() => openIncident(item.id)}><span><b>{name(item.kind)}</b><small>{name(item.classification)}</small></span><ArrowRight size={15}/></button>) : <Blank title="No discrepancies" text="Detection runs after each accepted import."/> : <div><p className="live-muted">{counts[selectedDomain === "Bank" ? "bank_transactions" : selectedDomain.toLowerCase()] ?? 0} observed records in this domain.</p><button className="outline-btn" onClick={() => setView(selectedDomain === "Payments" || selectedDomain === "Settlements" || selectedDomain === "Bank" ? "Money Trail" : "Record History")}>Inspect records <ArrowRight size={14}/></button></div>}</div></section></div>
      <section className="surface live-dashboard-ledger"><div className="panel-head"><div><div className="eyebrow">SOURCE EVENT LEDGER</div><h2>Observation timeline <span className="count">{observations.length} shown</span></h2></div><button className="outline-btn" onClick={() => setView("Replay")}>Open replay <ArrowRight size={14}/></button></div>{observations.length ? <div className="event-list">{observations.slice(0, 8).map((item, index) => <button className="event-row" key={item.id} onClick={() => openObservation(item.id)}><span className={`event-marker ${["payment", "refund", "settlement", "bank_transaction"].includes(item.kind) ? "blue" : "teal"}`}/><span className="event-seq">#{String(observations.length - index).padStart(4, "0")}</span><b>{name(item.event_type)}</b><span className="event-entity">{item.provider_object_id}</span><span className="event-time">{new Date(item.ingested_at).toLocaleString()}</span><ArrowRight size={14}/></button>)}</div> : <Blank title="No source events" text="Import or synchronize a source to record business history."/>}</section></>}</>}
      {view === "Incidents" && <div className="live-two"><section className="surface"><div className="panel-head"><div><div className="eyebrow">REAL SOURCE DISCREPANCIES</div><h2>Detection queue</h2></div><span className="pill amber">{incidents.filter(item => item.status === "open").length} open</span></div>{incidents.length ? incidents.map(item => <button key={item.id} className={`incident-row ${selectedIncident?.id === item.id ? "selected" : ""}`} onClick={() => openIncident(item.id)}><span className={`severity ${item.classification === "confirmed_inconsistency" ? "high" : ""}`}/><div><b>{name(item.kind)}</b><small>{name(item.classification)}  /  {item.id.slice(0, 8)}</small></div><ArrowRight size={15}/></button>) : <Blank title="No imported discrepancies" text="Import inventory and financial records to run source-backed checks."/>}</section>
        <section className="surface live-detail"><div className="panel-head"><div><div className="eyebrow">INVESTIGATION</div><h2>{selectedIncident ? name(selectedIncident.kind) : "Select an incident"}</h2></div></div>{selectedIncident ? <div className="live-detail-body"><span className={`pill ${selectedIncident.classification === "confirmed_inconsistency" ? "red" : "amber"}`}>{name(selectedIncident.classification)}</span><p>{selectedIncident.summary}</p><div className="section-label">SOURCE EVIDENCE</div>{selectedIncident.evidence.map(item => <button key={item.id} className="live-record" onClick={() => openSource(item.kind, item.id)}><span><b>{item.external_id}</b><small>{name(item.kind)}  /  {item.id.slice(0, 8)}</small></span><ArrowRight size={15}/></button>)}<div className="live-review-actions"><button className="primary-btn" onClick={() => investigate(selectedIncident.id)} disabled={busy}>Investigate with read tools</button><button className="outline-btn" onClick={() => queueAgent(selectedIncident.id)} disabled={busy || !agentStatus?.agentic_available}>Queue durable AI investigation</button></div><p className="live-muted">{agentStatus?.agentic_available ? `Planner → evidence tools → analyst → skeptical critic. Automatic worker: ${agentStatus.auto_worker_enabled ? "enabled" : "off"}. Human approval required.` : "AI agent requires LLM_BASE_URL, LLM_MODEL and LLM_API_KEY on the API server. Deterministic investigation remains available."}</p><div className="section-label">FINDINGS</div>{selectedIncident.investigations?.length ? selectedIncident.investigations.map(item => <div className="live-finding" key={item.id}><span className="pill teal">{item.mode === "ai_enabled" ? "AI enabled" : "Deterministic fallback"}</span><p>{item.finding}</p><small>{item.tool_trace.length} tool or model trace entries · {item.tool_trace.some(entry => entry.tool === "agent_critic" && entry.status === "passed") ? "Agent critic verified" : item.tool_trace.some(entry => entry.tool === "agent_critic") ? "Agent critic did not verify" : "Standard investigation"}</small></div>) : <p className="live-muted">No investigation has run. Source evidence is available above.</p>}</div> : <Blank title="Choose a discrepancy" text="Each finding can be traced to imported source rows."/>}</section></div>}
      {view === "Incidents" && selectedIncident && evidenceGraph && <><LiveEvidenceGraph graph={evidenceGraph} onSource={openSource} onObservation={id => act(async () => setSelectedObservation(await api<Record<string, unknown>>(base + `/observations/${id}`)))}/><div className="live-note"><b>Next step</b> · Compare isolated recovery strategies using this workspace's observed snapshot. <button className="outline-btn" onClick={() => setView("Scenario Lab")}>Compare Recovery Strategies <ArrowRight size={14}/></button></div></>}
      {view === "Incidents" && selectedIncident?.investigations?.[0]?.structured && <section className="surface live-review"><div className="panel-head"><div><div className="eyebrow">EVIDENCE ASSESSMENT</div><h2>Findings and open questions</h2></div><span className="pill teal">{selectedIncident.investigations[0].structured.execution_mode === "ai_enabled" ? "Validated model interpretation" : "Deterministic fallback"}</span></div><div className="live-review-body"><div className="section-label">SOURCE-DERIVED CLAIMS</div>{selectedIncident.investigations[0].structured.claims.map((claim, index) => <p key={index}>{claim.text} <small>{name(claim.classification)} from {claim.source_ids.length} referenced source records</small></p>)}<div className="section-label">HYPOTHESES</div>{selectedIncident.investigations[0].structured.root_cause_hypotheses.length ? selectedIncident.investigations[0].structured.root_cause_hypotheses.map((item, index) => <p key={index}>{item.text} <small>Inferred · requires human review</small></p>) : <p className="live-muted">No root cause has been confirmed from the available evidence.</p>}<div className="section-label">UNRESOLVED</div>{selectedIncident.investigations[0].structured.unresolved_questions.map(item => <p className="live-muted" key={item}>{item}</p>)}</div></section>}
      {view === "Incidents" && selectedIncident && <section className="surface live-review"><div className="panel-head"><div><div className="eyebrow">HUMAN REVIEW</div><h2>Review record</h2></div><span className="pill muted">{name(selectedIncident.status)}</span></div><div className="live-review-body"><label>Review note<textarea value={reviewNote} onChange={event => setReviewNote(event.target.value)} placeholder="Record what you checked and what should happen next" maxLength={2000}/></label><div className="live-review-actions"><button className="outline-btn" onClick={() => reviewIncident("acknowledged")} disabled={busy}>Acknowledge</button><button className="primary-btn" onClick={() => reviewIncident("reviewed")} disabled={busy}>Mark reviewed</button>{selectedIncident.status !== "open" && <button className="outline-btn" onClick={() => reviewIncident("open")} disabled={busy}>Reopen</button>}</div><div className="live-review-actions"><label>Conclusion<select value={conclusionOutcome} onChange={event => setConclusionOutcome(event.target.value)}><option value="explained">Explained by evidence</option><option value="resolved_by_evidence">Resolved by evidence</option><option value="needs_followup">Needs follow-up</option></select></label><button className="outline-btn" onClick={concludeIncident} disabled={busy || !selectedIncident.investigations?.length || reviewNote.trim().length < 3}>Record conclusion</button></div>{selectedIncident.conclusions?.map((item, index) => <div className="live-review-entry" key={`conclusion-${index}`}><b>{name(item.outcome)}</b><span>{item.note}</span><small>{new Date(item.created_at).toLocaleString()}</small></div>)}{selectedIncident.reviews?.map((item, index) => <div className="live-review-entry" key={`${item.created_at}-${index}`}><b>{name(item.status)}</b><span>{item.note}</span><small>{item.created_at}  /  Reviewer {item.reviewer_id.slice(0, 8)}</small></div>)}</div></section>}
      {view === "Scenario Lab" && <section className="surface live-scenario-setup"><div className="panel-head"><div><div className="eyebrow">DECISION LAB / MODELED ESTIMATES</div><h2>Recovery assumptions</h2></div><span className="pill amber">User adjusted</span></div><div className="live-form-row">{([ ["supplier_lead_time", "Supplier lead time (hours)", 500], ["restock_units", "Restock availability · units", 100000], ["inspection_delay", "Inspection delay (hours)", 500], ["fulfillment_cost_paise", "Fulfillment cost · paise/unit", 100000], ["expedited_delivery_cost_paise", "Expedited supplier order", 100000], ["unfulfilled_cost_paise", "Unfulfilled order · paise", 100000], ["return_processing_time", "Inspection duration (hours)", 500], ["procurement_unit_cost_paise", "Procurement (paise/unit)", 100000], ["holding_cost_paise_per_unit_hour", "Holding (paise/unit/hour)", 100000], ["return_inspection_cost_paise", "Inspection (paise/unit)", 100000] ] as const).map(([key, label, max]) => <label key={key}>{label}<input type="number" min="0" max={max} value={scenarioExtras[key]} onChange={event => setScenarioExtras(current => ({ ...current, [key]: Number(event.target.value) }))}/></label>)}</div><p className="live-muted">These are explicit estimates. Restock is purchased in both recovery branches. The alternative expedites delivery. Inspection only releases disputed stock when the selected result passes.</p>{scenarioStale && <p className="live-evidence-missing" role="status">Assumptions changed. Saved results are stale; select Compare futures to run a new simulation.</p>}</section>}
      {view === "Scenario Lab" && <section className="surface live-inspection-setting"><label>Simulated inspection result <select value={inspectionResult} onChange={event => setInspectionResult(event.target.value)}><option value="failed">Failed - quarantine disputed units</option><option value="passed">Passed - release verified units</option><option value="hold">Indefinite hold - no release</option></select></label><label><input type="checkbox" checked={supplierAvailable} onChange={event => setSupplierAvailable(event.target.checked)}/> Replacement supplier available</label><p className="live-muted">This is an explicit hypothetical result, not a finding from imported records.</p></section>}
      {view === "Scenario Lab" && <><section className="surface live-scenario-setup"><div className="panel-head"><div><div className="eyebrow">ISOLATED DIGITAL TWIN</div><h2>Snapshot imported inventory</h2></div><span className="pill muted">No source writes</span></div><div className="live-form-row"><button className="outline-btn" onClick={createSnapshot} disabled={busy}>Create snapshot</button><code>{snapshotId ? snapshotId.slice(0, 12) : "No snapshot selected"}</code><label>SKU<select value={scenarioSku} onChange={event => setScenarioSku(event.target.value)}>{[...new Set(dashboard?.inventory.map(item => item.sku) ?? [])].map(sku => <option key={sku}>{sku}</option>)}</select></label><label>Observed at or before<input type="datetime-local" value={snapshotCutoff} onChange={event => setSnapshotCutoff(event.target.value)}/></label><label>Unavailable units<input type="number" min="0" value={scenarioDisruption} onChange={event => setScenarioDisruption(Number(event.target.value))}/></label><label>Units per demand estimate<input type="number" min="1" max="5" value={scenarioUnitMultiplier} onChange={event => setScenarioUnitMultiplier(Number(event.target.value))}/></label><label>Demand attempts<input type="number" min="1" max="500" value={scenarioDemand} onChange={event => setScenarioDemand(Number(event.target.value))}/></label><label>Seed<input type="number" value={scenarioSeed} onChange={event => setScenarioSeed(Number(event.target.value))}/></label><button className="primary-btn" onClick={compare} disabled={busy || !snapshotId}>Compare futures</button></div><p className="live-muted">All branches use the same deterministic demand schedule. The selected demand rate and disruption are estimates. Outcomes are hypothetical and can be replayed.</p></section>
      {view === "Scenario Lab" && <section className="surface live-scenario-setup"><div className="panel-head"><div><div className="eyebrow">DEMAND INPUT</div><h2>Choose how demand is generated</h2></div><span className={`pill ${demandMode === "observed" ? "teal" : "muted"}`}>{demandMode === "observed" ? "Observed model" : "Manual estimate"}</span></div><div className="live-form-row"><label>Demand source<select value={demandMode} onChange={event => setDemandMode(event.target.value as "manual" | "observed")}><option value="manual">Manual estimate</option><option value="observed" disabled={demandPreview?.status !== "ready"}>Observed SKU orders</option></select></label><label>Modeled horizon · hours<input type="number" min="1" max="168" value={demandHorizon} onChange={event => setDemandHorizon(Number(event.target.value))}/></label></div>{!snapshotId ? <p className="live-muted">Create a snapshot to check SKU order coverage.</p> : demandPreview?.status === "ready" ? <p className="live-muted">{demandPreview.model_version}: {demandPreview.forecast_attempts} attempts over {demandHorizon} hours from {demandPreview.sample_orders} SKU orders on {demandPreview.active_days} days. Latest order: {demandPreview.latest_order_age_hours} hours before snapshot. Seeded arrivals and observed order quantities are shared by all three branches.</p> : <p className="live-evidence-missing">Observed model unavailable: {demandPreview?.message ?? "Checking snapshot history."} Use manual demand until enough recent SKU order items are imported.</p>}<p className="live-muted">Demand attempts above apply only to manual mode. Units per demand scales both sources. This simple rate model is a scenario input, not a validated forecast.</p>{scenario?.demand_model && !scenarioStale && <p className="live-muted">Saved run used {scenario.demand_model.model_version} with {scenario.demand_model.sample_orders} source orders.</p>}</section>}
      {view === "Scenario Lab" && scenario && !scenarioStale && <div className="replay-launch"><button className="primary-btn" onClick={() => setReplayOpen(true)}>Replay All Futures</button><span>Watch three saved policies advance on one simulation clock.</span></div>}
      {view === "Scenario Lab" && scenario && replayOpen && !scenarioStale && <ThreeFuturesReplay base={base} scenarioId={scenario.id} onClose={() => setReplayOpen(false)}/>}
      {scenario ? <><div className="live-branches">{(scenario.branches?.length ? scenario.branches : [scenario.baseline, scenario.recovery]).map((branch, index) => <section className="surface live-branch" key={branch.label}><div className="panel-head"><div><div className="eyebrow">FUTURE {String.fromCharCode(65 + index)}</div><h2>{branch.label}</h2></div></div><Metric label="STARTING SELLABLE" value={branch.starting_sellable}/><Metric label="FULFILLED UNITS" value={branch.fulfilled_units}/><Metric label="LOST DEMAND ATTEMPTS" value={branch.unfilled_attempts}/><Metric label="HYPOTHETICAL REVENUE" value={inr(branch.hypothetical_revenue_paise)}/><Metric label="STOCK RISK UNITS" value={branch.stock_risk_units}/><details><summary>Inspect {branch.events.length} simulated events</summary><div className="live-event-scroll">{branch.events.map((event, eventIndex) => <div className="live-sim-event" key={`${event.step}-${eventIndex}`}>#{event.step} {event.event}  /  requested {event.requested ?? "-"}, fulfilled {event.fulfilled ?? "-"}</div>)}</div></details></section>)}</div><section className="surface live-review"><div className="panel-head"><div><div className="eyebrow">SANDBOX REVIEW</div><h2>Choose and verify a strategy</h2></div></div><div className="live-review-body"><label>Preferred branch<select value={selectedBranch} onChange={event => setSelectedBranch(Number(event.target.value))}>{(scenario.branches?.length ? scenario.branches : [scenario.baseline, scenario.recovery]).map((branch, index) => <option value={index} key={index}>{branch.label}</option>)}</select></label><label>Review note<textarea value={strategyNote} onChange={event => setStrategyNote(event.target.value)} placeholder="Explain why you prefer this strategy"/></label><div className="live-review-actions"><button className="outline-btn" onClick={reviewStrategy} disabled={busy || strategyNote.trim().length < 3}>Record preferred plan</button><button className="primary-btn" onClick={verifySandbox} disabled={busy}>Verify sandbox replay</button></div><p className="live-muted">Verification replays the saved snapshot with the same seed and estimates. It makes no changes to live records or providers.</p></div></section><div className="live-note">Scenario {scenario.id.slice(0, 8)} is stored separately. It cannot change imported orders, inventory, payments or bank records.</div></> : <Blank title="No scenario yet" text="Import products and inventory, create a snapshot, then compare isolated recovery branches."/>}</>}
      {view === "Scenario Lab" && scenario && !scenarioStale && <section className="surface live-scenario-setup"><div className="panel-head"><div><div className="eyebrow">TRADE-OFF MATRIX / SAVED RUN</div><h2>Calculated branch outcomes</h2></div></div><div className="live-tradeoff-scroll"><table><thead><tr><th>Policy</th><th>Fulfilled orders</th><th>Lost attempts</th><th>Recovery hour</th><th>Stock risk</th><th>Operational cost</th><th>Potential refund exposure</th><th>Disputed units used</th></tr></thead><tbody>{(scenario.branches ?? []).map((branch, index) => <tr key={branch.label}><td><b>{String.fromCharCode(65 + index)} · {branch.label}</b><small>{index === 0 ? "Uses highest observed stock" : index === 1 ? "Holds disputed stock; restock if available" : "Pauses for inspection"}</small></td><td>{branch.orders_fulfilled ?? "—"}</td><td>{branch.unfilled_attempts}</td><td>{branch.recovery_completed_at ?? "Unresolved"}</td><td>{branch.stock_risk_units} units</td><td>{inr(branch.additional_operational_cost_paise ?? 0)}</td><td>{inr(branch.potential_refund_exposure_paise ?? 0)}</td><td>{branch.policy_violations ?? "—"}</td></tr>)}</tbody></table></div><p className="live-muted">Remaining incident count is not modeled by this inventory simulator. Procurement and inspection costs use the entered assumptions. Stock risk is exposure, not a confirmed refund.</p></section>}
      {view === "Scenario Lab" && scenario && !scenarioStale && <div className="live-two decision-explain"><section className="surface"><div className="panel-head"><div><div className="eyebrow">SENSITIVITY / TESTED SEEDS</div><h2>Where strategies change</h2></div></div><div className="live-review-body"><label>Vary assumption<select value={sensitivityVariable} onChange={event => setSensitivityVariable(event.target.value)}><option value="demand">Demand attempts</option><option value="restock_units">Restock units</option><option value="inspection_delay">Inspection delay</option></select></label><button className="outline-btn" onClick={runSensitivity} disabled={busy}>Run five-seed sensitivity</button>{sensitivity && <><p className="live-muted">{sensitivity.repetitions} repetitions. {sensitivity.note}</p>{sensitivity.rows.map(row => <div className="live-history" key={row.value}><b>{name(sensitivity.variable)} = {row.value}</b>{row.branches.map(branch => <small key={branch.branch_index}>{branch.label}: mean {branch.mean_unfilled_attempts.toFixed(1)}, range {branch.min_unfilled_attempts}–{branch.max_unfilled_attempts} unfilled</small>)}</div>)}</>}</div></section><section className="surface"><div className="panel-head"><div><div className="eyebrow">EVENT DIFFERENCE EXPLORER</div><h2>Why outcomes differ</h2></div></div><div className="live-review-body"><label>Compare A to B<select value={differenceMetric} onChange={event => setDifferenceMetric(event.target.value)}><option value="unfilled_attempts">Unfilled attempts</option><option value="fulfilled_units">Fulfilled units</option><option value="hypothetical_revenue_paise">Hypothetical revenue</option><option value="additional_operational_cost_paise">Operational cost</option><option value="stock_risk_units">Stock risk units</option></select></label><button className="outline-btn" onClick={inspectDifference} disabled={busy}>Explain difference</button>{difference && <><p className="live-muted">A {difference.left_value} → B {difference.right_value}; Δ {difference.difference}. {difference.explanation}</p><div className="live-evidence-timeline">{difference.demand_events.map(item => <div className="live-history" key={item.demand_id}><b>{item.demand_id}</b><small>A fulfilled {item.left.fulfilled} / B fulfilled {item.right.fulfilled} units</small></div>)}</div></>}</div></section></div>}
      {view === "Scenario Lab" && difference && (difference.attribution_status === "reconciled" || difference.left_contributions.length > 0 || difference.right_contributions.length > 0) && !scenarioStale && <MetricContributions difference={difference}/>}
      {view === "Scenario Lab" && <section className="surface live-scenario-setup"><div className="panel-head"><div><div className="eyebrow">MODEL RELIABILITY / OBSERVED HISTORY</div><h2>Historical order-volume backtest</h2></div><span className="pill muted">No counterfactual claim</span></div><div className="live-form-row"><label>Historical cutoff<input type="datetime-local" value={backtestCutoff} onChange={event => setBacktestCutoff(event.target.value)}/></label><label>Forecast horizon · hours<input type="number" min="1" max="720" value={backtestHorizon} onChange={event => setBacktestHorizon(Number(event.target.value))}/></label><button className="outline-btn" onClick={runBacktest} disabled={busy}>Evaluate history</button></div>{backtest?.status === "insufficient_history" ? <p className="live-evidence-missing">{backtest.message}</p> : backtest?.status === "evaluated" ? <div className="live-backtest"><p className="live-muted">{backtest.evaluated_samples} rolling origins · {backtest.model_version} · order observations {backtest.source_coverage?.order_observations}</p><div className="live-backtest-stats"><Metric label="MODEL MAE" value={backtest.errors?.mae?.toFixed(2) ?? "—"}/><Metric label="MODEL RMSE" value={backtest.errors?.rmse?.toFixed(2) ?? "—"}/><Metric label="BIAS" value={backtest.errors?.bias?.toFixed(2) ?? "—"}/><Metric label="LAST VALUE MAE" value={backtest.baselines?.last_observed.mae?.toFixed(2) ?? "—"}/></div><div className="live-backtest-bars">{backtest.origins?.map(item => <div key={item.cutoff}><small>{new Date(item.cutoff).toLocaleDateString()}</small><span style={{ width: `${Math.max(2, Math.min(100, item.prediction * 10))}%` }}>Forecast {item.prediction.toFixed(1)}</span><span style={{ width: `${Math.max(2, Math.min(100, item.actual * 10))}%` }}>Observed {item.actual}</span></div>)}</div>{backtest.limitations?.map(item => <p className="live-muted" key={item}>{item}</p>)}</div> : <p className="live-muted">Three complete prior windows and a fully observed future window are required. History before first ingestion is unavailable.</p>}</section>}
      {view === "Scenario Lab" && scenario && <section className="surface live-scenario-setup"><div className="panel-head"><div><div className="eyebrow">DECISION RECEIPT / SAVED RUN</div><h2>Export the evidence and decision</h2></div><span className="pill teal">Private report</span></div><p className="live-muted">The report separates observed facts, inferred hypotheses, and hypothetical branch outcomes. It includes source and run identifiers, review and sandbox verification, and a reproducibility manifest.</p><a className="primary-btn" target="_blank" rel="noopener noreferrer" href={`${API}${base}/scenarios/${scenario.id}/receipt${selectedIncident ? `?incident_id=${encodeURIComponent(selectedIncident.id)}` : ""}`}>Open printable Decision Receipt <ArrowRight size={15}/></a></section>}
      {view === "Money Trail" && <><div className="live-money-head"><input value={moneyQuery} onChange={event => setMoneyQuery(event.target.value)} placeholder="Search payment, refund, settlement or bank ID"/><Search size={16}/></div>{filteredMoney.length ? <section className="surface live-money-list">{filteredMoney.map((item, index) => <div className="live-money-row" key={`${item.payment.id}-${index}`}><div><span className="eyebrow">CAPTURE</span><b>{item.payment.external_id}</b><strong>{inr(item.payment.amount_paise)}</strong></div><ArrowRight size={16}/><div><span className="eyebrow">FEES + TAX</span><strong>-{inr(item.payment.fee_paise + item.payment.fee_tax_paise)}</strong></div><ArrowRight size={16}/><div><span className="eyebrow">PRIOR REFUND</span><strong>-{inr(item.prior_refund?.amount_paise ?? 0)}</strong><small>{item.prior_refund?.external_id ?? "None linked"}</small></div><ArrowRight size={16}/><div><span className="eyebrow">EXPECTED</span><strong>{inr(item.expected_paise)}</strong><small>{item.settlement?.external_id ?? "No settlement"}</small></div><ArrowRight size={16}/><div><span className="eyebrow">BANK CREDIT</span><strong>{item.bank_transactions.length ? inr(item.bank_total_paise) : "Awaiting import"}</strong><small>{name(item.status)}</small></div></div>)}</section> : <Blank title="No payment records" text="Import orders, payments, refunds, settlements and bank transactions to reconcile them."/>}</>}
      {view === "Money Trail" && filteredMoney.length > 0 && <section className="surface live-list-surface money-proof"><div className="panel-head"><div><div className="eyebrow">TWO-STAGE RECONCILIATION</div><h2>Source-backed adjustments</h2></div></div>{filteredMoney.map((item, index) => <details className="live-history" key={`${item.payment.id}-proof-${index}`}><summary>{item.payment.external_id} · {item.reason_codes?.length ? item.reason_codes.join(" · ") : "Balanced"}</summary><p>Provider variance: {item.provider_variance_paise == null ? "No settlement" : inr(item.provider_variance_paise)} · Bank variance: {item.bank_variance_paise == null ? "No linked bank credit" : inr(item.bank_variance_paise)}</p><div className="money-source-path">{item.source_path?.map((ref, position) => <button className="outline-btn" key={`${ref.kind}-${ref.id}-${position}`} onClick={() => openSource(ref.kind, ref.id)}>{name(ref.kind)} {ref.id.slice(0, 8)}</button>)}</div>{item.adjustments?.map((adjustment, position) => <button className="live-record" key={`${adjustment.reason_code}-${position}`} onClick={() => openSource(adjustment.source.kind, adjustment.source.id)}><span><b>{name(adjustment.reason_code)}</b><small>{adjustment.detail}</small></span><strong>{inr(adjustment.amount_paise)}</strong></button>)}</details>)}</section>}
      {view === "Agent Traces" && <section className="surface live-list-surface"><div className="panel-head"><div><div className="eyebrow">DURABLE AGENT QUEUE</div><h2>Checkpointed investigations</h2></div><button className="outline-btn" onClick={refreshAgentJobs} disabled={busy}>Refresh jobs</button></div>{agentJobs.length ? agentJobs.map(item => <div className="live-trace" key={item.id}><span className="pill teal">{name(item.status)} / {name(item.phase)}</span><b>Incident {item.incident_id.slice(0, 8)} · {item.progress.specialist ?? "Routing"}</b><small>Plans {item.progress.completed_plans} · Evidence reads {item.progress.evidence_reads} · Model slots {item.model_call_slots_reserved}/{item.max_model_calls} · Budget reservation ~US${(item.estimated_cost_microusd / 1000000).toFixed(4)} (not provider bill)</small>{item.progress.evidence_gaps?.length ? <p className="live-muted">Open evidence gaps: {item.progress.evidence_gaps.join("; ")}</p> : null}{item.last_error ? <p className="live-muted">Last failure: {item.last_error}</p> : null}<div className="live-review-actions">{["queued","running"].includes(item.status) && <button className="outline-btn" disabled={busy} onClick={() => controlAgent(item.id, "cancel")}>Cancel job</button>}{["failed","cancelled"].includes(item.status) && <button className="outline-btn" disabled={busy} onClick={() => controlAgent(item.id, "resume")}>Resume job</button>}</div></div>) : <Blank title="No queued jobs" text="Open an incident and queue a durable AI investigation. Automatic opt-in jobs appear here too."/>}</section>}
      {view === "Agent Traces" && <section className="surface live-list-surface"><div className="panel-head"><div><div className="eyebrow">WORKSPACE-SCOPED READ TOOLS</div><h2>Investigation executions</h2></div></div>{investigations.length ? investigations.map(item => <div className="live-trace" key={item.id}><span className="pill teal">{item.mode === "ai_enabled" ? "AI enabled" : "Deterministic fallback"}</span><b>{item.finding}</b><small>Incident {item.incident_id}  /  {item.created_at}</small><details><summary>Tool trace</summary><pre>{JSON.stringify(item.tool_trace, null, 2)}</pre></details></div>) : <Blank title="No investigations" text="Open a real incident and run an evidence-backed investigation."/>}</section>}
      {view === "Replay" && <LiveReplay observations={observations} workspaceId={workspaceId} onInspect={openObservation} hasOlder={hasOlderObservations} onLoadOlder={loadOlderObservations} loadingOlder={busy}/>}
      {view === "Record History" && <div className="live-two"><section className="surface"><div className="panel-head"><div><div className="eyebrow">PERSISTED CSV BATCHES</div><h2>Import history</h2></div></div>{batches.length ? batches.map(batch => <div className="live-history" key={batch.id}><b>{batch.filename}</b><small>{name(batch.kind)}  /  {batch.created_at}</small><span>{batch.status}: {batch.imported_count} imported, {batch.duplicate_count} duplicates</span>{batch.errors.map(error => <p key={error.row}>Row {error.row}: {error.message}</p>)}</div>) : <Blank title="No imports" text="Imported records and their source batches appear here."/>}</section><section className="surface"><div className="panel-head"><div><div className="eyebrow">COUNTERFACTUAL AUDIT</div><h2>Saved scenarios</h2></div></div>{scenarios.length ? scenarios.map(item => <button className="live-record" key={item.id} onClick={() => openSavedScenario(item)}><span><b>{item.id.slice(0, 8)}</b><small>Seed {item.seed}  /  snapshot {item.snapshot_id.slice(0, 8)}</small></span><ArrowRight size={15}/></button>) : <Blank title="No snapshots" text="Scenario Lab stores comparisons without changing imported records."/>}</section></div>}
      {view === "Record History" && <div className="live-two"><section className="surface"><div className="panel-head"><div><div className="eyebrow">OBSERVED SOURCE HISTORY</div><h2>Provider and import events</h2></div><span className="pill teal">{observations.length} shown</span></div>{observations.length ? observations.map(item => <button className="live-record" key={item.id} onClick={() => act(async () => setSelectedObservation(await api<Record<string, unknown>>(base + `/observations/${item.id}`)))}><span><b>{item.provider_object_id}</b><small>{name(item.source_system)}  /  {name(item.event_type)}  /  {new Date(item.ingested_at).toLocaleString()}</small><small>{item.import_batch_id ? `Import ${item.import_batch_id.slice(0, 8)}` : item.webhook_delivery_id ? `Webhook ${item.webhook_delivery_id.slice(0, 8)}` : "Provider API response"}</small></span><ArrowRight size={15}/></button>) : <Blank title="No observed events" text="Source observations appear after an import or provider sync."/>}{hasOlderObservations && <button className="outline-btn" onClick={loadOlderObservations} disabled={busy}>{busy ? "Loading…" : "Load older observations"}</button>}</section><section className="surface"><div className="panel-head"><div><div className="eyebrow">HUMAN AND SYSTEM ACTIONS</div><h2>Audit history</h2></div></div>{audit.length ? audit.map(item => <div className="live-history" key={item.id}><b>{name(item.action)}</b><small>{new Date(item.created_at).toLocaleString()}  /  {item.target_id}</small><details><summary>Recorded details</summary><pre>{JSON.stringify(item.detail, null, 2)}</pre></details></div>) : <Blank title="No audited actions" text="Imports, reviews, connections and scenarios appear here."/>}</section></div>}
      {view === "Money Trail" && razorpaySettlements.length > 0 && <section className="surface live-list-surface"><div className="panel-head"><div><div className="eyebrow">RAZORPAY PROVIDER RECONCILIATION</div><h2>Settlements and bank matches</h2></div></div>{razorpaySettlements.map(item => <div className="live-history" key={item.id}><b>{item.external_id}  /  {inr(item.amount_paise)}</b><small>UTR {item.utr ?? "Unavailable"}  /  {name(item.status)}</small><span>Provider lines: {item.calculated_paise === null ? "Unavailable" : inr(item.calculated_paise)}  /  Bank: {item.bank_external_ids.length ? inr(item.bank_total_paise) : "Awaiting statement"}</span><details><summary>{item.lines.length} provider reconciliation lines</summary>{item.lines.map(line => <div className="live-record" key={line.id}><span><b>{line.entity_id}</b><small>{name(line.type)}  /  {line.payment_id ?? "No payment reference"}</small></span><strong>{inr(line.credit_paise - line.debit_paise)}</strong></div>)}</details></div>)}</section>}      {view === "Benchmarks" && <section className="surface live-list-surface"><div className="panel-head"><div><div className="eyebrow">LIVE EVALUATION</div><h2>Data coverage</h2></div></div><div className="live-coverage">{importKinds.map(item => <div key={item}><b>{name(item)}</b><strong>{counts[item === "inventory" ? "inventory_snapshots" : item] ?? 0}</strong></div>)}</div><div className="live-note">This workspace has no labeled ground truth. The <a href="/demo">Demo Mode benchmark</a> includes 16 labeled CSV workspaces; those scores are limited to that small test set. AI uplift is unmeasured.</div></section>}
      {view === "Integrations" && <div className="live-two live-integrations"><section className="surface"><div className="panel-head"><div><div className="eyebrow">PRIORITY 1  /  CSV</div><h2>Import business records</h2></div></div><div className="live-import-form"><label>Record type<select value={kind} onChange={event => { setKind(event.target.value); setPreview(null); setMapping({}); }}>{importKinds.map(item => <option value={item} key={item}>{name(item)}</option>)}</select></label><label>UTF-8 CSV<input type="file" accept=".csv,text/csv" onChange={event => { setFile(event.target.files?.[0] ?? null); setPreview(null); setMapping({}); }}/></label><button className="outline-btn" onClick={previewFile} disabled={busy || !file}>Validate and preview</button>{preview && <><div className="live-preview-summary">{preview.rows} rows  /  {preview.valid_count} new  /  {preview.duplicate_count} duplicates  /  {preview.errors.length} errors</div><div className="section-label">COLUMN MAPPING</div><div className="live-map-fields">{preview.required_fields.map(field => <label key={field}>{name(field)}<select value={mapping[field] ?? ""} onChange={event => setMapping({ ...mapping, [field]: event.target.value })}><option value="">Select column</option>{preview.headers.map(header => <option key={header}>{header}</option>)}</select></label>)}</div>{preview.errors.map(error => <p className="live-row-error" key={error.row}>Row {error.row}: {error.message}</p>)}<div className="section-label">SOURCE PREVIEW</div><pre>{JSON.stringify(preview.preview, null, 2)}</pre><button className="primary-btn" onClick={commitImport} disabled={busy || preview.errors.length > 0}>Commit validated batch</button></>}</div></section><section className="surface"><div className="panel-head"><div><div className="eyebrow">CONNECTION STATUS</div><h2>Source systems</h2></div></div><div className="live-connection"><b>CSV imports</b><span className="pill teal">{dashboard?.connections.csv ?? "Awaiting import"}</span><p>Uploaded files are validated, persisted and linked to their import batch.</p></div><div className="live-connection"><b>Shopify</b><span className={`pill ${shopify?.status === "connected" ? "teal" : "muted"}`}>{name(shopify?.status ?? "not_connected")}</span><p>{shopify?.account ?? (shopify?.configured ? "Connect your myshopify.com store for read-only sync." : "Configure the Shopify app and encryption key on the server to enable connection.")}</p>{shopify?.last_successful_sync_at && <small>Last successful sync: {new Date(shopify.last_successful_sync_at).toLocaleString()}</small>}{shopify?.last_error && <p className="live-row-error">{shopify.last_error}</p>}{shopify?.configured && !shopify?.can_sync && <label>Store domain<input value={shopDomain} onChange={event => setShopDomain(event.target.value)} placeholder="store.myshopify.com"/><button className="primary-btn" onClick={connectShopify} disabled={busy || !shopDomain.trim()}>Connect Shopify</button></label>}{shopify?.can_sync && <div className="live-provider-actions"><button className="outline-btn" onClick={syncShopify} disabled={busy}>Sync now</button><button className="text-btn" onClick={disconnectShopify} disabled={busy}>Disconnect</button></div>}</div><div className="live-connection"><b>Razorpay</b><span className={`pill ${razorpay?.status === "connected" ? "teal" : "muted"}`}>{name(razorpay?.status ?? "not_connected")}</span><p>{razorpay?.account ?? (razorpay?.configured ? "Enter your merchant API keys for read-only retrieval." : "Configure the credential encryption key on the server to enable connection.")}</p>{razorpay?.last_successful_sync_at && <small>Last successful sync: {new Date(razorpay.last_successful_sync_at).toLocaleString()}</small>}{razorpay?.last_error && <p className="live-row-error">{razorpay.last_error}</p>}{razorpay?.configured && !razorpay?.can_sync && <div className="live-provider-form"><label>Key ID<input value={razorpayKeyId} onChange={event => setRazorpayKeyId(event.target.value)} autoComplete="off" placeholder="rzp_test_..."/></label><label>Key secret<input type="password" value={razorpaySecret} onChange={event => setRazorpaySecret(event.target.value)} autoComplete="off"/></label><button className="primary-btn" onClick={connectRazorpay} disabled={busy || !razorpayKeyId.trim() || !razorpaySecret}>Verify and connect</button></div>}{razorpay?.can_sync && <div className="live-provider-actions"><button className="outline-btn" onClick={syncRazorpay} disabled={busy}>Sync now</button><button className="text-btn" onClick={disconnectRazorpay} disabled={busy}>Disconnect</button></div>}</div><div className="section-label">SIGNED IN</div><div className="live-connection"><b>{account.email}</b><small>{workspace?.name}  /  workspace scoped</small></div></section></div>}
      {view === "Integrations" && razorpay?.can_sync && <section className="surface live-list-surface">
        <div className="panel-head"><div><div className="eyebrow">RAZORPAY WEBHOOK</div><h2>Receive signed updates</h2></div><span className={`pill ${razorpay.webhook_configured ? "teal" : "muted"}`}>{razorpay.webhook_configured ? "Secret saved" : "Setup needed"}</span></div>
        <div className="live-import-form">
          <p>Choose a separate webhook secret, save it here, then register this URL and the same secret in your Razorpay dashboard for payment.captured, refund.processed and settlement.processed events.</p>
          {razorpay.webhook_path && <p>Webhook URL: <code>{API + razorpay.webhook_path}</code></p>}
          <label>Webhook secret<input type="password" value={razorpayWebhookSecret} onChange={event => setRazorpayWebhookSecret(event.target.value)} autoComplete="new-password" minLength={16} placeholder="At least 16 characters" /></label>
          <button className="outline-btn" onClick={configureRazorpayWebhook} disabled={busy || razorpayWebhookSecret.length < 16}>Save webhook secret</button>
          <small>Razorpay dashboard registration is required. OpsVerse verifies signatures and fetches source records with read-only APIs; periodic polling remains the recovery path.</small>
        </div>
      </section>}
      {view === "Settings" && <div className="settings-grid live-settings"><section className="surface"><div className="panel-head"><div><div className="eyebrow">BUSINESS WORKSPACE</div><h2>{workspace?.name}</h2></div></div><div className="live-settings-body"><div className="settings-line"><span>Workspace ID</span><code>{workspaceId}</code></div><div className="settings-line"><span>Signed in as</span><strong>{account.email}</strong></div><div className="settings-line"><span>Orders</span><strong>{metric?.order_count ?? 0}</strong></div><div className="settings-line"><span>Recorded observations</span><strong>{observations.length}{observations.length === 500 ? " shown (latest 500)" : ""}</strong></div><div className="settings-line"><span>Import batches</span><strong>{batches.length}</strong></div><div className="live-settings-actions"><button className="outline-btn" onClick={() => setView("Record History")}>View record history</button><button className="primary-btn" onClick={() => setView("Integrations")}>Manage data sources <ArrowRight size={14}/></button></div></div></section><section className="surface"><div className="panel-head"><div><div className="eyebrow">SOURCE CONFIGURATION</div><h2>Connections and access</h2></div></div><div className="live-settings-body"><div className="settings-line"><span>CSV</span><strong>{dashboard?.connections.csv ?? "Awaiting import"}</strong></div><div className="settings-line"><span>Shopify</span><strong>{name(shopify?.status ?? "not_connected")}</strong></div><div className="settings-line"><span>Razorpay</span><strong>{name(razorpay?.status ?? "not_connected")}</strong></div><p className="live-muted">Connections and imported records belong to this workspace. Scenario runs use copied observations and cannot change provider data.</p><div className="live-settings-actions"><button className="outline-btn" onClick={() => moveTour(0)}>Start guided tour</button><a href="/demo" className="outline-btn">Open synthetic demo</a><button className="text-btn" onClick={signOut}>Sign out</button></div></div></section></div>}
      {view === "Incidents" && selectedIncident && !!selectedIncident.investigations?.length && <section className="surface live-review"><div className="panel-head"><div><div className="eyebrow">POLICY CHECKED</div><h2>Proposed recovery steps</h2></div><span className="pill amber">Human review required</span></div><div className="live-review-body">{selectedIncident.investigations[0].proposals?.map(item => <div className="live-review-entry" key={item.id}><b>{name(item.id)}</b><span>{item.description}</span><small>{item.evidence_ids.length} source references  /  {item.simulation_available ? "Can compare in Scenario Lab" : "No execution available"}</small></div>)}<p className="live-muted">These are proposals only. No stock, payment, refund or settlement has been changed.</p></div></section>}
      {view === "Settings" && <section className="surface live-scenario-setup"><div className="panel-head"><div><div className="eyebrow">ANALYSIS MODELS</div><h2>Model availability</h2></div></div><div className="live-settings-body"><div className="settings-line"><span>Observed SKU demand</span><strong>{dashboard?.analysis_models?.demand_model ?? "Unavailable"}</strong></div><p className="live-muted">This local model needs enough recent order items for the selected SKU. Scenario Lab checks each snapshot before enabling it.</p><div className="settings-line"><span>Incident investigation LLM</span><strong>{dashboard?.analysis_models?.incident_llm === "credentials_present" ? "Credentials present" : "Not configured"}</strong></div><p className="live-muted">Credentials alone do not prove inference works. An investigation trace shows whether a model completion passed evidence validation; otherwise Live uses a deterministic fallback.</p></div></section>}
    </main>
    {tourStep !== null && <div className="live-tour-panel" role="dialog" aria-label="Live Operations guided tour"><div className="live-tour-top"><span>LIVE WORKFLOW · {tourStep + 1} / {liveTour.length}</span><button className="icon-btn" onClick={() => setTourStep(null)} aria-label="Close guided tour"><X size={14}/></button></div><h2>{liveTour[tourStep].title}</h2><p>{liveTour[tourStep].description}</p><div className="live-tour-actions"><button className="outline-btn" onClick={() => moveTour(tourStep - 1)} disabled={tourStep === 0}>Back</button><button className="primary-btn" onClick={() => moveTour(tourStep + 1)}>{tourStep === liveTour.length - 1 ? "Finish tour" : "Next step"} <ArrowRight size={14}/></button></div></div>}
    {selectedObservation && <div className="overlay" onClick={() => setSelectedObservation(null)}><div className="event-drawer" onClick={event => event.stopPropagation()}><div className="panel-head"><div><div className="eyebrow">OBSERVED SOURCE EVENT</div><h2>{String(selectedObservation.provider_object_id ?? "Observation")}</h2></div><button className="icon-btn" onClick={() => setSelectedObservation(null)} aria-label="Close"><X size={17}/></button></div><div className="live-source-body"><pre>{JSON.stringify(selectedObservation, null, 2)}</pre></div></div></div>}    {selectedSource && <div className="overlay" onClick={() => setSelectedSource(null)}><div className="event-drawer" onClick={event => event.stopPropagation()}><div className="panel-head"><div><div className="eyebrow">SOURCE RECORD</div><h2>{selectedSource.external_id ?? selectedSource.filename}</h2></div><button className="icon-btn" onClick={() => setSelectedSource(null)} aria-label="Close"><X size={17}/></button></div><div className="live-source-body"><code>{selectedSource.id}</code><div className="section-label">NORMALIZED FIELDS</div><pre>{JSON.stringify(selectedSource.fields ?? selectedSource, null, 2)}</pre><div className="section-label">OBSERVATION REFERENCES</div>{selectedSource.observation_refs?.map(item => <button className="live-record" key={item.id} onClick={() => act(async () => setSelectedObservation(await api<Record<string, unknown>>(base + `/observations/${item.id}`)))}><span><b>{name(item.event_type)}</b><small>{new Date(item.ingested_at).toLocaleString()}</small></span><ArrowRight size={15}/></button>)}<div className="section-label">SOURCE PAYLOAD</div><pre>{JSON.stringify(selectedSource.source_row ?? {}, null, 2)}</pre></div></div></div>}
  </div>;
}
