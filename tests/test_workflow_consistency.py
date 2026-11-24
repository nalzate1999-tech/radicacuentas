"""Regresiones del recorrido QA: frentes, resultados y cierre coherente."""
import uuid
import pytest
from sqlalchemy import select
from app.database import SessionLocal
from app.models import Case, Event, Subtask
from app.services import (create_case, apply_followup, apply_review, apply_legal,
                          BusinessError)


def make_case(db):
    key=uuid.uuid4().hex
    case=create_case(db,dict(contractor_type='CC',contractor_name='QA aislado',
        document=key,contract_no=key,period='202611',amount='1000',
        contractor_email='qa@example.invalid',followup_assignee='Control QA',
        sent_by='Contratista',requires_reviewer='Sí',requires_technical='Sí',
        requires_legal='Sí'), 'qa@example.invalid')
    db.flush()
    return case


def test_complete_flow_has_correct_fronts_and_no_unfinished_closure():
    with SessionLocal() as db:
        case=make_case(db)
        with pytest.raises(BusinessError,match='revisiones pendientes'):
            apply_followup(db,case,'Cerrar','QA','qa',closed_date='2026-11-24')
        assert case.current_status=='RECIBIDA'
        for action,kind,revision,provider in [
            ('Enviado a firma revisora jurídica','JURIDICA','Jurídica','Velázquez Osorio'),
            ('Enviado a firma revisora técnica','TECNICA','Técnica','Soluciones Territoriales')]:
            before=db.scalars(select(Event).where(Event.case_id==case.id_caso)).all()
            apply_followup(db,case,action,'QA','qa')
            db.flush()
            after=db.scalars(select(Event).where(Event.case_id==case.id_caso).order_by(Event.id)).all()
            assert len(after)==len(before)+1
            assert after[-1].task_id==f'{case.id_caso}-{kind}'
            assert db.get(Subtask,f'{case.id_caso}-{kind}').status=='EN_CURSO'
            assert db.get(Subtask,f'{case.id_caso}-REVISOR').status=='PENDIENTE'
            apply_legal(db,case,revision,provider,'Aprobado','QA','qa')
            db.flush()
        apply_followup(db,case,'Enviado a revisor','QA','qa')
        apply_review(db,case,'Requiere ajustes','QA','qa')
        db.flush()
        with pytest.raises(BusinessError,match='REVISOR'):
            apply_followup(db,case,'Cerrar','QA','qa',closed_date='2026-11-24')
        apply_followup(db,case,'Enviado a revisor','QA','qa')
        sub=db.get(Subtask,f'{case.id_caso}-REVISOR')
        assert sub.result is None and sub.closed_at is None
        apply_review(db,case,'Enviado a financiera','QA','qa')
        db.flush()
        apply_followup(db,case,'Cerrar','QA','qa',closed_date='2026-11-24')
        assert case.current_status=='CERRADA'
        tasks=db.scalars(select(Subtask).where(Subtask.case_id==case.id_caso)).all()
        assert all(t.status=='CERRADA' and not t.active and t.closed_at for t in tasks)
        db.rollback()


def test_legal_adjustment_remains_pending_until_resubmitted_and_approved():
    with SessionLocal() as db:
        case=make_case(db)
        apply_followup(db,case,'Enviado a firma revisora jurídica','QA','qa')
        apply_legal(db,case,'Jurídica','Velázquez Osorio','Requiere ajustes','QA','qa')
        db.flush()
        task=db.get(Subtask,f'{case.id_caso}-JURIDICA')
        assert task.active and task.status=='REQUIERE_AJUSTES'
        assert task.closed_at is None
        with pytest.raises(BusinessError,match='JURIDICA'):
            apply_followup(db,case,'Cerrar','QA','qa',closed_date='2026-11-24')
        apply_followup(db,case,'Enviado a firma revisora jurídica','QA','qa')
        assert task.result is None and task.closed_at is None
        assert task.status=='EN_CURSO'
        db.rollback()
