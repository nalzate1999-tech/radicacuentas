"""Visible case aging metrics; no historical timestamps are invented."""
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo

BOGOTA = ZoneInfo('America/Bogota')
CLOSED = frozenset({'CERRADA','CERRADO','PAGADA','PAGADO'})


def local_dt(value):
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year,value.month,value.day,tzinfo=BOGOTA)
    if value.tzinfo is None:
        value=value.replace(tzinfo=timezone.utc)
    return value.astimezone(BOGOTA)


def case_age_info(item, first_event=None, closing_event=None, today=None):
    """Calendar elapsed days, distinct from business-day SLA deadlines."""
    today=today or datetime.now(BOGOTA).date()
    candidates=[local_dt(d) for d in (item.received_at,item.registered_at) if d is not None]
    inferred=False
    if candidates:
        start=min(candidates)
        start_note='Fecha inicial registrada'
    elif item.source=='web' and item.created_at is not None:
        start=local_dt(item.created_at)
        start_note='Creado en el aplicativo'
    elif first_event is not None:
        start=local_dt(first_event)
        start_note='Primer movimiento documentado (fecha estimada)'
        inferred=True
    else:
        start=None
        start_note='No hay fecha historica verificable'
    closed=item.current_status in CLOSED
    end_date=None
    if closed:
        if closing_event is not None:
            end_date=local_dt(closing_event).date()
        elif item.finance_closed_at is not None:
            end_date=item.finance_closed_at
    else:
        end_date=today
    days=None
    if start is not None and end_date is not None and end_date>=start.date():
        days=(end_date-start.date()).days
    duration_text=(str(days)+' '+('d\u00eda' if days==1 else 'd\u00edas')
                   if days is not None else 'No disponible')
    return {
        'first_display': start.strftime('%d/%m/%Y %H:%M') if start else 'Sin fecha documentada',
        'first_note': start_note,
        'first_is_estimated': inferred,
        'duration_days': days,
        'duration_display': duration_text,
        'duration_note': ('Hasta el cierre registrado' if closed and days is not None
                          else 'Cierre sin fecha verificable' if closed
                          else 'D\u00edas calendario transcurridos'),
        'closed': closed,
    }
