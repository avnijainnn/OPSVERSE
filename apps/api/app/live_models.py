"""Workspace-scoped operational records. Demo ledgers never write these tables."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def uid() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(160))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="owner")


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    import_batch_id: Mapped[str | None] = mapped_column(ForeignKey("import_batches.id"))
    source_system: Mapped[str] = mapped_column(String(40), default="csv")
    external_id: Mapped[str] = mapped_column(String(160))
    raw: Mapped[dict] = mapped_column(JSON)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Product(SourceMixin, Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("workspace_id", "source_system", "external_id"),)
    sku: Mapped[str] = mapped_column(String(120), index=True)
    name: Mapped[str] = mapped_column(String(255))
    price_paise: Mapped[int] = mapped_column(Integer)


class InventorySnapshot(SourceMixin, Base):
    __tablename__ = "inventory_snapshots"
    __table_args__ = (UniqueConstraint("workspace_id", "source_system", "external_id"), Index("ix_inventory_sku_time", "workspace_id", "sku", "observed_at"))
    sku: Mapped[str] = mapped_column(String(120))
    location: Mapped[str] = mapped_column(String(120))
    sellable: Mapped[int] = mapped_column(Integer)
    reserved: Mapped[int] = mapped_column(Integer)
    quarantined: Mapped[int] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Order(SourceMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("workspace_id", "source_system", "external_id"),)
    customer_ref: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(String(80))
    total_paise: Mapped[int] = mapped_column(Integer)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OrderItem(SourceMixin, Base):
    __tablename__ = "order_items"
    __table_args__ = (UniqueConstraint("workspace_id", "source_system", "external_id"),)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    sku: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_paise: Mapped[int] = mapped_column(Integer)


class Return(SourceMixin, Base):
    __tablename__ = "returns"
    __table_args__ = (UniqueConstraint("workspace_id", "source_system", "external_id"),)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    sku: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[int] = mapped_column(Integer)
    condition: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))


class Payment(SourceMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("workspace_id", "source_system", "external_id"),)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    fee_paise: Mapped[int] = mapped_column(Integer)
    fee_tax_paise: Mapped[int] = mapped_column(Integer)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Refund(SourceMixin, Base):
    __tablename__ = "refunds"
    __table_args__ = (UniqueConstraint("workspace_id", "source_system", "external_id"),)
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"), index=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    refunded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Settlement(SourceMixin, Base):
    __tablename__ = "settlements"
    __table_args__ = (UniqueConstraint("workspace_id", "source_system", "external_id"),)
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    prior_refund_id: Mapped[str | None] = mapped_column(ForeignKey("refunds.id"))
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BankTransaction(SourceMixin, Base):
    __tablename__ = "bank_transactions"
    __table_args__ = (UniqueConstraint("workspace_id", "source_system", "external_id"),)
    settlement_id: Mapped[str | None] = mapped_column(ForeignKey("settlements.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LiveIncident(Base):
    __tablename__ = "live_incidents"
    __table_args__ = (UniqueConstraint("workspace_id", "fingerprint"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    kind: Mapped[str] = mapped_column(String(60))
    classification: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="open")
    fingerprint: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LiveInvestigation(Base):
    __tablename__ = "live_investigations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("live_incidents.id"), index=True)
    mode: Mapped[str] = mapped_column(String(30))
    finding: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON)
    tool_trace: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentJob(Base):
    """Durable, workspace-scoped agent execution. Checkpoint is JSON and never holds secrets."""
    __tablename__ = "agent_jobs"
    __table_args__ = (UniqueConstraint("workspace_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("live_incidents.id"), index=True)
    requested_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    phase: Mapped[str] = mapped_column(String(24), default="plan")
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    locked_by: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_model_calls: Mapped[int] = mapped_column(Integer, default=7)
    reserved_model_calls: Mapped[int] = mapped_column(Integer, default=0)
    max_cost_microusd: Mapped[int] = mapped_column(Integer, default=200000)
    estimated_cost_microusd: Mapped[int] = mapped_column(Integer, default=0)
    reserved_cost_microusd: Mapped[int] = mapped_column(Integer, default=0)
    investigation_id: Mapped[str | None] = mapped_column(ForeignKey("live_investigations.id"))
    last_error: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)




class LiveSnapshot(Base):
    __tablename__ = "live_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    source_state: Mapped[dict] = mapped_column(JSON)
    source_digest: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[int] = mapped_column(Integer, default=2)
    observed_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assumptions: Mapped[list] = mapped_column(JSON, default=list)
    limitations: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LiveScenario(Base):
    __tablename__ = "live_scenarios"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("live_snapshots.id"), index=True)
    kind: Mapped[str] = mapped_column(String(60))
    seed: Mapped[int] = mapped_column(Integer)
    baseline: Mapped[dict] = mapped_column(JSON)
    recovery: Mapped[dict] = mapped_column(JSON)
    branches: Mapped[list] = mapped_column(JSON, default=list)
    disruption: Mapped[dict] = mapped_column(JSON, default=dict)
    assumptions: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditRecord(Base):
    __tablename__ = "audit_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str] = mapped_column(String(160))
    detail: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderConnection(Base):
    __tablename__ = "provider_connections"
    __table_args__ = (UniqueConstraint("provider", "external_account_id"),
                      UniqueConstraint("workspace_id", "provider"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    external_account_id: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    access_ciphertext: Mapped[str | None] = mapped_column(Text)
    refresh_ciphertext: Mapped[str | None] = mapped_column(Text)
    webhook_secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    webhook_previous_secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    webhook_secret_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[str | None] = mapped_column(Text)
    sync_cursor: Mapped[dict] = mapped_column(JSON, default=dict)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class OAuthState(Base):
    __tablename__ = "oauth_states"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    nonce_hash: Mapped[str] = mapped_column(String(64), unique=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    shop_domain: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (UniqueConstraint("provider", "delivery_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    connection_id: Mapped[str | None] = mapped_column(ForeignKey("provider_connections.id"))
    provider: Mapped[str] = mapped_column(String(30))
    delivery_id: Mapped[str] = mapped_column(String(160))
    event_id: Mapped[str | None] = mapped_column(String(160))
    topic: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (UniqueConstraint("workspace_id", "source_system", "idempotency_key"),
                      Index("ix_observations_object_time", "workspace_id", "source_system", "object_kind", "provider_object_id", "ingested_at"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    source_system: Mapped[str] = mapped_column(String(40))
    object_kind: Mapped[str] = mapped_column(String(40))
    provider_object_id: Mapped[str] = mapped_column(String(160))
    event_type: Mapped[str] = mapped_column(String(80))
    external_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    correlation_id: Mapped[str | None] = mapped_column(String(160))
    causation_id: Mapped[str | None] = mapped_column(String(160))
    previous_observation_id: Mapped[str | None] = mapped_column(ForeignKey("observations.id"))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    import_batch_id: Mapped[str | None] = mapped_column(ForeignKey("import_batches.id"))
    webhook_delivery_id: Mapped[str | None] = mapped_column(ForeignKey("webhook_deliveries.id"))
    normalized_record_id: Mapped[str | None] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)


_engine = None
_url = None


def configured() -> bool:
    return bool(os.getenv("DATABASE_URL"))


def engine():
    global _engine, _url
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("Live Operations requires DATABASE_URL and its database migrations")
    if _engine is None or _url != url:
        if _engine is not None:
            _engine.dispose()
        _engine = create_engine(url, pool_pre_ping=True)
        _url = url
    return _engine


def session_factory():
    return sessionmaker(bind=engine(), expire_on_commit=False)
