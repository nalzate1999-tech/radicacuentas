from datetime import datetime, date, timezone
from sqlalchemy import (String, Integer, BigInteger, DateTime, Date, Boolean, Numeric,
                        ForeignKey, Text, JSON, UniqueConstraint, Index, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

def utcnow():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(250), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(20), default='consulta', index=True)
    form_permissions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    invite_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    invite_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Case(Base):
    __tablename__ = 'cases'
    id_caso: Mapped[str] = mapped_column(String(65), primary_key=True)
    source: Mapped[str] = mapped_column(String(20), default='web', index=True)
    needs_reconciliation: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    contractor_type: Mapped[str | None] = mapped_column(String(30))
    contractor_name: Mapped[str | None] = mapped_column(String(240), index=True)
    document: Mapped[str | None] = mapped_column(String(90), index=True)
    contract_no: Mapped[str | None] = mapped_column(String(100), index=True)
    period: Mapped[str | None] = mapped_column(String(160), index=True)
    amount: Mapped[float | None] = mapped_column(Numeric(18,2))
    contractor_email: Mapped[str | None] = mapped_column(String(250))
    contractor_phone: Mapped[str | None] = mapped_column(String(100))
    supervisor_name: Mapped[str | None] = mapped_column(String(240))
    supervisor_email: Mapped[str | None] = mapped_column(String(250))
    supervisor_phone: Mapped[str | None] = mapped_column(String(100))
    followup_assignee: Mapped[str | None] = mapped_column(String(250), index=True)
    reviewer: Mapped[str | None] = mapped_column(String(400))
    special: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_by: Mapped[str | None] = mapped_column(String(45))
    requires_reviewer: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_technical: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_legal: Mapped[bool] = mapped_column(Boolean, default=False)
    current_status: Mapped[str] = mapped_column(String(100), default='RECIBIDA', index=True)
    current_owner: Mapped[str] = mapped_column(String(100), default='Seguimiento', index=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_due: Mapped[date | None] = mapped_column(Date, index=True)
    last_observation: Mapped[str | None] = mapped_column(Text)
    finance_closed_at: Mapped[date | None] = mapped_column(Date)
    support_link: Mapped[str | None] = mapped_column(Text)
    original_values: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    events = relationship('Event', back_populates='case', order_by='Event.occurred_at')
    tasks = relationship('Subtask', back_populates='case')

class Event(Base):
    __tablename__ = 'events'
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, 'sqlite'), primary_key=True, autoincrement=True)
    case_id: Mapped[str | None] = mapped_column(String(65), ForeignKey('cases.id_caso'), index=True)
    raw_case_id: Mapped[str | None] = mapped_column(String(1024), index=True)
    task_id: Mapped[str | None] = mapped_column(String(100))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    kind: Mapped[str] = mapped_column(String(130), index=True)
    actor_role: Mapped[str | None] = mapped_column(String(120))
    actor_name: Mapped[str | None] = mapped_column(String(300))
    observations: Mapped[str | None] = mapped_column(Text)
    resulting_status: Mapped[str | None] = mapped_column(String(140))
    resulting_owner: Mapped[str | None] = mapped_column(String(120))
    provider: Mapped[str | None] = mapped_column(String(240))
    file_link: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(25), default='web')
    source_row: Mapped[int | None] = mapped_column(Integer)
    original_values: Mapped[dict | None] = mapped_column(JSON)
    case = relationship('Case', back_populates='events')
    __table_args__ = (UniqueConstraint('source', 'source_row', name='uq_event_source_row'),)

class Subtask(Base):
    __tablename__ = 'subtasks'
    id: Mapped[str] = mapped_column(String(130), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(String(65), ForeignKey('cases.id_caso'), index=True)
    raw_case_id: Mapped[str | None] = mapped_column(String(1024))
    kind: Mapped[str | None] = mapped_column(String(60))
    provider: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(100), default='PENDIENTE')
    responsible: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[date | None] = mapped_column(Date)
    result: Mapped[str | None] = mapped_column(String(200))
    observations: Mapped[str | None] = mapped_column(Text)
    file_link: Mapped[str | None] = mapped_column(Text)
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(30), default='web')
    original_values: Mapped[dict | None] = mapped_column(JSON)
    case = relationship('Case', back_populates='tasks')

class RadContract(Base):
    __tablename__ = 'rad_contracts'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(130), index=True)
    contractor: Mapped[str | None] = mapped_column(String(250))
    document_type: Mapped[str | None] = mapped_column(String(35))
    document: Mapped[str | None] = mapped_column(String(90))
    started_at: Mapped[date | None] = mapped_column(Date)
    ended_at: Mapped[date | None] = mapped_column(Date)
    monthly_value: Mapped[float | None] = mapped_column(Numeric(18,2))
    total_value: Mapped[float | None] = mapped_column(Numeric(18,2))
    contact_email: Mapped[str | None] = mapped_column(String(250))
    phone: Mapped[str | None] = mapped_column(String(100))
    dependency: Mapped[str | None] = mapped_column(String(240))
    supervisor: Mapped[str | None] = mapped_column(String(250))
    supervisor_email: Mapped[str | None] = mapped_column(String(250))
    supervisor_phone: Mapped[str | None] = mapped_column(String(100))
    last_notice_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    legacy_status: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30), default='import')
    source_row: Mapped[int | None] = mapped_column(Integer, unique=True)
    original_values: Mapped[dict | None] = mapped_column(JSON)
    months = relationship('RadMonth', back_populates='contract')

class RadMonth(Base):
    __tablename__ = 'rad_months'
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer,'sqlite'), primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey('rad_contracts.id'), index=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    filed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    contract = relationship('RadContract', back_populates='months')
    __table_args__ = (UniqueConstraint('contract_id','year','month', name='uq_rad_contract_period'),)

class Holiday(Base):
    __tablename__ = 'holidays'
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(120))

class ImportBatch(Base):
    __tablename__ = 'import_batches'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_name: Mapped[str] = mapped_column(String(260))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(25), default='EN_CURSO')
    summary: Mapped[dict] = mapped_column(JSON, default=dict)

class SourceSheet(Base):
    __tablename__ = 'source_sheets'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('import_batches.id'), index=True)
    name: Mapped[str] = mapped_column(String(240))
    headers: Mapped[list | None] = mapped_column(JSON)
    physical_rows: Mapped[int] = mapped_column(Integer, default=0)
    saved_rows: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint('batch_id','name',name='uq_source_sheet'),)

class RawRow(Base):
    __tablename__ = 'raw_rows'
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer,'sqlite'), primary_key=True, autoincrement=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey('source_sheets.id'), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    values: Mapped[list] = mapped_column(JSON)
    formulas: Mapped[dict | None] = mapped_column(JSON)
    hyperlinks: Mapped[dict | None] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint('sheet_id','row_number',name='uq_source_row'),)

class FormResponse(Base):
    __tablename__ = 'form_responses'
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer,'sqlite'), primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(35), index=True)
    source_row: Mapped[int | None] = mapped_column(Integer)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    case_id: Mapped[str | None] = mapped_column(String(65), ForeignKey('cases.id_caso'), index=True)
    raw_case_id: Mapped[str | None] = mapped_column(String(1024))
    submitted_by: Mapped[str | None] = mapped_column(String(250))
    values: Mapped[dict | None] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint('source_type','source_row',name='uq_form_response_origin'),)

class ImportIssue(Base):
    __tablename__ = 'import_issues'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('import_batches.id'), index=True)
    sheet_name: Mapped[str] = mapped_column(String(240))
    row_number: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(90), index=True)
    detail: Mapped[str] = mapped_column(Text)

class Audit(Base):
    __tablename__ = 'audits'
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer,'sqlite'), primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(260))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(130))
    detail: Mapped[dict | None] = mapped_column(JSON)

class Notification(Base):
    __tablename__ = 'notifications'
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer,'sqlite'), primary_key=True, autoincrement=True)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    case_id: Mapped[str] = mapped_column(String(65), index=True)
    to_email: Mapped[str] = mapped_column(String(250))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default='PENDIENTE', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

Index('ix_case_status_assignee', Case.current_status, Case.followup_assignee)
Index('ix_events_case_date', Event.case_id, Event.occurred_at.desc())

class Personnel(Base):
    __tablename__ = 'personnel'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(250), unique=True)

class LegacyLog(Base):
    __tablename__ = 'legacy_logs'
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer,'sqlite'), primary_key=True, autoincrement=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    level: Mapped[str | None] = mapped_column(String(30), index=True)
    process: Mapped[str | None] = mapped_column(String(120))
    case_id: Mapped[str | None] = mapped_column(String(90), index=True)
    detail: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(String(250))
    source_row: Mapped[int] = mapped_column(Integer, unique=True)

class AuthAttempt(Base):
    __tablename__ = 'auth_attempts'
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer,'sqlite'),primary_key=True,autoincrement=True)
    email_hash: Mapped[str] = mapped_column(String(64),index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow,index=True)
    success: Mapped[bool] = mapped_column(Boolean,default=False)
