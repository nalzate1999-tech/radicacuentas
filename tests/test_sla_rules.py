"""Regresiones de plazo acordadas para Radicacuentas."""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.database import SessionLocal
from app.models import Case
from app.services import sla_deadline, change_status

BOGOTA = ZoneInfo("America/Bogota")


def test_sla_ignora_el_dia_del_envio_y_el_fin_de_semana():
    with SessionLocal() as db:
        # Viernes 20 de noviembre de 2026: lunes=1, martes=2, miercoles=3.
        due = sla_deadline(db, datetime(2026, 11, 20, 9, 0, tzinfo=BOGOTA))
        assert due.isoformat() == "2026-11-25"
        due_late = sla_deadline(db, datetime(2026, 11, 20, 18, 0, tzinfo=BOGOTA))
        assert due_late == due


def test_cambios_de_etapa_no_reinician_plazo():
    with SessionLocal() as db:
        case = Case(id_caso="UNIT-PLAZO-01", current_status="RECIBIDA",
                    current_owner="Seguimiento", special=False, sla_due=None)
        assert case.sla_due is None
        change_status(db, case, "EN_PRIMERA_REVISION", "Revisor")
        assert case.sla_due is not None
        first_due = case.sla_due
        change_status(db, case, "APROBADA_FIRMA_REVISORA_TECNICA", "Seguimiento")
        assert case.sla_due == first_due
