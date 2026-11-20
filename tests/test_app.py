"""Pruebas integrales de acceso, formularios, control de duplicados y flujos."""
import os
import re
import uuid
os.environ['DATABASE_URL']='sqlite:////tmp/radica_pytest_'+uuid.uuid4().hex+'.sqlite3'
os.environ['APP_ENV']='development'
os.environ['ALLOWED_HOSTS']='testserver,localhost,127.0.0.1'
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app,ph
from app.database import Base, engine, SessionLocal
from app.models import User, Case, Event

Base.metadata.create_all(engine)
with SessionLocal.begin() as db:
    db.add(User(name='Administrador QA',email='qa@example.org',
                password_hash=ph.hash('Contraseña-Muy-Segura-2026!'),role='admin'))
    db.add(User(name='Persona consulta',email='viewer@example.org',
                password_hash=ph.hash('Contraseña-Muy-Segura-2026!'),role='consulta'))


def token(text):
    match=re.search(r'name="csrf"\s+value="([^"]+)"',text)
    assert match, 'No se entregó token CSRF'
    return match.group(1)


def client_as(email,password='Contraseña-Muy-Segura-2026!'):
    client=TestClient(app,base_url='http://testserver',follow_redirects=False)
    r=client.get('/ingresar');assert r.status_code==200
    r=client.post('/ingresar',data={'csrf':token(r.text),'email':email,'password':password})
    assert r.status_code==303
    return client


def test_unauthenticated_and_login():
    c=TestClient(app,follow_redirects=False)
    assert c.get('/health').status_code==200
    assert c.get('/panel').status_code==401
    page=c.get('/ingresar')
    assert c.post('/ingresar',data={'csrf':'incorrecto','email':'qa@example.org','password':'nada'}).status_code==403
    assert 'name="password"' in page.text


def test_four_forms_and_csv():
    c=client_as('qa@example.org')
    form=c.get('/casos/nuevo')
    assert form.status_code==200
    assert 'name="archivo"' not in form.text
    csrf=token(form.text)
    data={'csrf':csrf,'contractor_type':'CC','contractor_name':'Contratista QA',
          'document':'112345678','contract_no':'QA-2026-1','period':'202611',
          'amount':'250000.25','contractor_email':'contratistaqa@example.org',
          'contractor_phone':'1234567','followup_assignee':'Brayan',
          'reviewer':'Hector','special':'No','sent_by':'Supervisor',
          'requires_reviewer':'Sí','requires_technical':'Sí','requires_legal':'Sí'}
    r=c.post('/casos/nuevo',data=data)
    assert r.status_code==303,r.text[:250]
    case_id=r.headers['location'].rsplit('/',1)[-1]
    assert case_id.startswith('CC-')
    assert c.post('/casos/nuevo',data=data).status_code==400
    assert c.get('/casos/'+case_id).status_code==200
    x=c.post('/casos/'+case_id+'/revisor',data={'csrf':csrf,
               'resultado':'Requiere ajustes','observaciones':'Ajustar soportes'})
    assert x.status_code==303
    x=c.post('/casos/'+case_id+'/seguimiento',data={'csrf':csrf,
           'accion':'Enviado a firma revisora jurídica','observaciones':'Escalado',
           'frente_multiple':'No','fecha_cierre':''})
    assert x.status_code==303
    x=c.post('/casos/'+case_id+'/abogados',data={'csrf':csrf,'tipo_revision':'Jurídica',
                 'proveedor':'Velázquez Osorio','resultado':'Aprobado',
                 'observaciones':'Conforme'})
    assert x.status_code==303
    with SessionLocal() as db:
        case=db.get(Case,case_id)
        assert case.current_status=='APROBADA_FIRMA_REVISORA_JURIDICA'
        assert db.scalars(select(Event).where(Event.case_id==case_id)).all()
    assert c.get('/panel').status_code==200
    assert c.get('/casos').status_code==200
    assert c.get('/informes/casos.csv').status_code==200
    assert c.get('/radicacion?year=2027').status_code==200


def test_roles():
    c=client_as('viewer@example.org')
    assert c.get('/casos').status_code==200
    assert c.get('/usuarios').status_code==403
    assert c.get('/casos/nuevo').status_code==403
    assert c.get('/administracion').status_code==403
