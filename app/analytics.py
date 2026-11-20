"""Percentage KPIs with explicit, non-empty denominators.

SLA current performance is not the same as final compliance: closing a case
removes its active SLA deadline, so historical final-on-time cannot be inferred.
"""
import re
from datetime import datetime
from sqlalchemy import case as sa_case, func, or_, select
from .models import Case, Event, RadMonth
from .services import BOGOTA, STOP_STATUSES

ARCHIVED = ('CERRADA', 'CERRADO', 'PAGADA', 'PAGADO')


def percentage(numerator, denominator):
    return round(numerator * 100 / denominator, 1) if denominator else None


def report_data(db, period='', assignee='', year=None):
    period = period.strip()
    assignee = assignee.strip()
    if period and not re.fullmatch(r'20\d{2}(0[1-9]|1[0-2])', period):
        raise ValueError('Periodo invalido, utilice AAAAMM.')
    if len(assignee) > 250:
        raise ValueError('Nombre de responsable demasiado largo.')
    today = datetime.now(BOGOTA).date()
    filters = []
    if period:
        filters.append(Case.period == period)
    if assignee:
        filters.append(Case.followup_assignee == assignee)

    def count(*conditions):
        return db.scalar(select(func.count(Case.id_caso)).where(*filters, *conditions)) or 0

    total = count()
    closed = count(Case.current_status.in_(ARCHIVED))
    pending = total - closed
    active_sla = (~Case.current_status.in_(tuple(STOP_STATUSES)))
    defined_sla = count(active_sla, Case.sla_due.is_not(None))
    overdue = count(active_sla, Case.sla_due < today)
    in_time = count(active_sla, Case.sla_due >= today)
    unassigned = count(~Case.current_status.in_(ARCHIVED), or_(
        Case.followup_assignee.is_(None), func.trim(Case.followup_assignee) == ''))
    cases_scope = select(Case.id_caso).where(*filters)
    returned = db.scalar(select(func.count(func.distinct(Event.case_id))).where(
        Event.case_id.in_(cases_scope),
        or_(Event.kind.ilike('%DEVUELT%'), Event.kind.ilike('%REQUIERE_AJUSTES%')),
    )) or 0
    events = db.scalar(select(func.count(Event.id)).where(Event.case_id.in_(cases_scope))) or 0
    by_stage = db.execute(select(Case.current_owner, func.count(Case.id_caso))
        .where(*filters)
        .group_by(Case.current_owner)
        .order_by(func.count(Case.id_caso).desc()).limit(12)).all()
    by_assignee = db.execute(select(
            Case.followup_assignee,
            func.count(Case.id_caso),
            func.sum(sa_case((Case.current_status.in_(ARCHIVED), 1), else_=0)),
        ).where(*filters, Case.followup_assignee.is_not(None),
                func.trim(Case.followup_assignee) != '')
        .group_by(Case.followup_assignee)
        .order_by(func.count(Case.id_caso).desc()).limit(20)).all()
    by_assignee = [(name, int(total_assigned), int(done or 0), percentage(int(done or 0), int(total_assigned)))
                   for name, total_assigned, done in by_assignee]
    assignees = db.scalars(select(Case.followup_assignee).distinct()
        .where(Case.followup_assignee.is_not(None),func.trim(Case.followup_assignee)!='')
        .order_by(Case.followup_assignee).limit(200)).all()

    reporting_year = year or (int(period[:4]) if period else today.year)
    reporting_year = max(2020, min(2100, reporting_year))
    filed_total = db.scalar(select(func.count(RadMonth.id)).where(RadMonth.year==reporting_year)) or 0
    filed = db.scalar(select(func.count(RadMonth.id)).where(
        RadMonth.year==reporting_year,RadMonth.filed.is_(True))) or 0
    monthly_rows = db.execute(select(Case.period,func.count(Case.id_caso))
        .where(Case.period.like(str(reporting_year)+'%'))
        .group_by(Case.period)).all()
    monthly_map = {k: int(v) for k,v in monthly_rows if k and re.fullmatch(str(reporting_year)+r'(0[1-9]|1[0-2])',k)}
    months = [(m, monthly_map.get(f'{reporting_year}{m:02d}',0)) for m in range(1,13)]
    peak = max((v for _,v in months),default=0)
    return {
        'total': total, 'closed': closed, 'pending': pending,
        'defined_sla': defined_sla, 'overdue': overdue, 'in_time': in_time,
        'no_sla': pending-defined_sla, 'unassigned': unassigned,
        'returned': returned, 'events': events,
        'close_pct': percentage(closed,total),
        'in_time_pct': percentage(in_time,defined_sla),
        'overdue_pct': percentage(overdue,defined_sla),
        'returned_pct': percentage(returned,total),
        'assigned_pct': percentage(pending-unassigned,pending),
        'by_stage': [(name or 'Sin responsable',count,percentage(count,total)) for name,count in by_stage],
        'by_assignee': by_assignee,
        'assignees': assignees,
        'reporting_year': reporting_year, 'filed_total': filed_total,
        'filed': filed,'filed_pct':percentage(filed,filed_total),
        'months': [(m,n,round(100*n/peak,1) if peak else 0) for m,n in months],
        'period':period,'assignee':assignee,
    }
