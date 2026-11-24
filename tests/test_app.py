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
    assert 'Responsable permanente de control' in form.text
    csrf=token(form.text)
    with SessionLocal() as db:
        admin_id=db.scalar(select(User.id).where(User.email=='qa@example.org'))
    data={'csrf':csrf,'contractor_type':'CC','contractor_name':'Contratista QA',
          'document':'112345678','contract_no':'QA-2026-1','period':'202611',
          'amount':'250000.25','contractor_email':'contratistaqa@example.org',
          'contractor_phone':'1234567','control_user_id':str(admin_id),
          'reviewer':'Hector','special':'No','sent_by':'Supervisor',
          'requires_reviewer':'Sí','requires_technical':'Sí','requires_legal':'Sí'}
    r=c.post('/casos/nuevo',data=data)
    assert r.status_code==303,r.text[:500]
    case_id=r.headers['location'].rsplit('/',1)[-1]
    assert case_id.startswith('CC-')
    with SessionLocal() as db:
        assert db.get(Case, case_id).sla_due is None
    assert c.post('/casos/nuevo',data=data).status_code==400
    assert c.get('/casos/'+case_id).status_code==200
    page=c.get('/casos/'+case_id+'/seguimiento')
    assert page.status_code==200
    assert 'Enviado para firma Directora' not in page.text
    assert 'Enviado a firma Directora' not in page.text
    x=c.post('/casos/'+case_id+'/seguimiento',data={'csrf':csrf,
           'accion':'Enviado a revisor','observaciones':'Enviado a primera revisión',
           'destino_usuario_id':str(admin_id)})
    assert x.status_code==303,x.text[:200]
    assert c.get('/casos/'+case_id+'/revisor').status_code==200
    x=c.post('/casos/'+case_id+'/revisor',data={'csrf':csrf,
               'resultado':'Requiere ajustes','observaciones':'Ajustar soportes'})
    assert x.status_code==303
    assert c.get('/casos/'+case_id+'/redistribuir').status_code==200
    x=c.post('/casos/'+case_id+'/redistribuir',data={'csrf':csrf,
            'gestion':'seguimiento','destino_usuario_id':str(admin_id)})
    assert x.status_code==303
    x=c.post('/casos/'+case_id+'/seguimiento',data={'csrf':csrf,
           'accion':'Enviado a firma revisora jurídica','observaciones':'Escalado',
           'frente_multiple':'No','fecha_cierre':'',
           'destino_usuario_id':str(admin_id)})
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
    assert c.get('/administracion').status_code==404


def test_invitation_activation_and_form_permissions():
    admin=client_as('qa@example.org')
    users_page=admin.get('/usuarios')
    assert users_page.status_code==200
    assert 'Crear acceso' in users_page.text
    uid_email='permisos'+uuid.uuid4().hex[:9]+'@example.org'
    made=admin.post('/usuarios',data={'csrf':token(users_page.text),
            'name':'Usuario de revisión','email':uid_email,'permissions':['revision','casos']})
    assert made.status_code==200,made.text[:300]
    assert 'INVITACIÓN GENERADA' in made.text or 'INVITACIÓN GENERADA'.lower() in made.text.lower()
    invite=re.search(r'https?://[^"\s<>]+/activar/([A-Za-z0-9_-]+)',made.text)
    assert invite, 'No existe un enlace de activación'
    code=invite.group(1)
    with SessionLocal() as db:
        target=db.scalar(select(User).where(User.email==uid_email))
        assert target is not None
        uid=target.id
        assert target.active is False
        assert target.form_permissions==['casos','revision']
    user_client=TestClient(app,base_url='http://testserver',follow_redirects=False)
    page=user_client.get('/activar/'+code)
    assert page.status_code==200,page.text[:300]
    bad=user_client.post('/activar/'+code,data={'csrf':token(page.text),'password':'123','confirm_password':'123'})
    assert bad.status_code==400
    ok=user_client.post('/activar/'+code,data={'csrf':token(page.text),
                          'password':'ClaveSegura-Usuario-2026!',
                          'confirm_password':'ClaveSegura-Usuario-2026!'})
    assert ok.status_code==303
    assert user_client.get('/activar/'+code).status_code==404
    login=user_client.get('/ingresar')
    response=user_client.post('/ingresar',data={'csrf':token(login.text),
           'email':uid_email,'password':'ClaveSegura-Usuario-2026!'})
    assert response.status_code==303
    assert user_client.get('/casos?accion=revision').status_code==200
    assert user_client.get('/casos/nuevo').status_code==403
    assert user_client.get('/informes').status_code==403
    assert user_client.get('/informes/casos.csv').status_code==403
    assert user_client.get('/usuarios').status_code==403
    # Las URL directas tampoco pueden dar acceso a formularios no asignados.
    assert user_client.get('/casos/PRUEBA-123/seguimiento').status_code==403
    edit=admin.post(f'/usuarios/{uid}/permisos',data={'csrf':token(users_page.text),
                   'permissions':['registro','informes']})
    assert edit.status_code==303
    assert user_client.get('/casos/nuevo').status_code==200
    assert user_client.get('/casos?accion=revision').status_code==403
    assert user_client.get('/informes').status_code==200
    assert user_client.get('/informes/casos.csv').status_code==403


def test_historical_case_dedup_and_technical_result():
    from datetime import datetime, timezone
    from app.models import Subtask
    from app.services import create_case, apply_legal, BusinessError
    with SessionLocal.begin() as db:
        imported=Case(id_caso='HIST-00001',source='import',period='202611',
                      contract_no='QA-HIST-1',document='10000000',
                      contractor_name='Persona histórica',current_status='RECIBIDA',
                      requires_technical=True,requires_legal=False)
        db.add(imported)
    values={'contractor_type':'CC','contractor_name':'Duplicado',
            'document':'10000000','contract_no':'QA-HIST-1','period':'202611',
            'amount':'10000','contractor_email':'test@example.org',
            'followup_assignee':'Brayan','sent_by':'Supervisor'}
    import pytest
    with SessionLocal.begin() as db:
        with pytest.raises(BusinessError,match='HIST-00001'):
            create_case(db,values,'qa@example.org')
    with SessionLocal.begin() as db:
        case=db.get(Case,'HIST-00001')
        apply_legal(db,case,'Técnica','Soluciones Territoriales','Aprobado',
                    'Aprobación de firma técnica',actor='qa@example.org')
        assert case.current_status=='APROBADA_FIRMA_REVISORA_TECNICA'
        db.flush()
        assert db.scalars(select(Event).where(Event.case_id==case.id_caso,
                              Event.kind=='APROBADA_FIRMA_REVISORA_TECNICA')).all()


def test_global_search_assignees_status_and_responsive_view():
    """La búsqueda permite nombres y asignados; los finalizados no son tareas."""
    from app.models import Subtask
    from app.services import BusinessError, apply_followup
    from datetime import datetime, timezone
    import pytest
    key=uuid.uuid4().hex[:8]
    open_id='BUSCA-ABIERTA-'+key
    closed_id='BUSCA-CERRADA-'+key
    with SessionLocal.begin() as db:
        db.add(Case(id_caso=open_id, source='web', contractor_name='Ana María Rodríguez '+key,
                    document='DOC-'+key, contract_no='CONTR-'+key, period='202611',
                    supervisor_name='Sonia Beltrán', followup_assignee='Daniel Acosta',
                    reviewer='Luisa Quintero', current_owner='Seguimiento',
                    current_status='EN_PRIMERA_REVISION', requires_reviewer=True,
                    registered_at=datetime.now(timezone.utc)))
        db.add(Case(id_caso=closed_id, source='import', contractor_name='Contabilidad Histórico '+key,
                    document='DOC-C-'+key, contract_no='CONTR-C-'+key, period='202611',
                    supervisor_name='Sonia Beltrán', followup_assignee='Daniel Acosta',
                    reviewer='Luisa Quintero', current_owner='Seguimiento',
                    current_status='CERRADA', requires_reviewer=True,
                    registered_at=datetime.now(timezone.utc)))
        db.add(Subtask(id='SUB-'+key, case_id=open_id, raw_case_id=open_id, kind='TECNICA',
                       responsible='Paula Valderrama', status='EN_CURSO', source='web'))
    client=client_as('qa@example.org')
    for q in ['Ana María Rodríguez', 'Sonia Beltrán', 'Daniel Acosta', 'Luisa Quintero',
              'Paula Valderrama', 'CONTR-'+key, 'DOC-'+key]:
        response=client.get('/casos',params={'q':q})
        assert response.status_code==200,(q,response.text[:200])
        assert open_id in response.text,q
    cross=client.get('/casos',params={'q':'Ana Acosta'})
    assert cross.status_code==200 and open_id in cross.text
    no_match=client.get('/casos',params={'q':'%_unlikely_'+key})
    assert no_match.status_code==200 and open_id not in no_match.text
    only_pending=client.get('/casos',params={'vista':'pendientes','q':key})
    assert open_id in only_pending.text and closed_id not in only_pending.text
    only_closed=client.get('/casos',params={'vista':'cerrados','q':key})
    assert closed_id in only_closed.text and open_id not in only_closed.text
    all_results=client.get('/casos',params={'q':key})
    assert open_id in all_results.text and closed_id in all_results.text
    work_queue=client.get('/casos',params={'accion':'revision','q':key})
    assert open_id in work_queue.text and closed_id not in work_queue.text
    redirect_closed=client.get('/casos/'+closed_id+'/revisor')
    assert redirect_closed.status_code==303 and redirect_closed.headers['location']=='/casos/'+closed_id
    assert 'case-mobile-list' in work_queue.text
    assert 'case-search-input' in work_queue.text
    assert 'Menú y herramientas' in work_queue.text
    home=client.get('/panel')
    assert 'home-case-search' in home.text
    with SessionLocal.begin() as db:
        done=db.get(Case,closed_id)
        with pytest.raises(BusinessError,match='cerrado'):
            apply_followup(db,done,'Enviado a revisor','No permitido','qa@example.org')


def test_retired_modules_not_accessible():
    c=client_as('qa@example.org')
    page=c.get('/panel')
    assert 'Cómo funciona el proceso' not in page.text
    assert 'Migración histórica' not in page.text
    assert c.get('/proceso').status_code==404
    assert c.get('/administracion').status_code==404
    assert c.post('/administracion/importar').status_code==404


def test_sla_filter_and_first_record_metrics():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from app.case_metrics import case_age_info
    now=datetime.now(ZoneInfo('America/Bogota'))
    today=now.date()
    unique=uuid.uuid4().hex[:9]
    entries=[
        ('ATRASADO',today-timedelta(days=2),'RECIBIDA'),
        ('HOY',today,'RECIBIDA'),
        ('MANANA',today+timedelta(days=1),'RECIBIDA'),
        ('ENPLAZO',today+timedelta(days=4),'RECIBIDA'),
        ('SINPLAZO',None,'RECIBIDA'),
        ('CERRADO',today-timedelta(days=3),'CERRADA'),
    ]
    with SessionLocal.begin() as db:
        for title,due,status in entries:
            started=now-timedelta(days=10)
            db.add(Case(
                id_caso=f'SLA-{unique}-{title}',source='web',
                contractor_name=f'SLA-TEST-{unique}',
                registered_at=started+timedelta(days=1),
                received_at=started,
                sla_due=due,current_status=status,
                finance_closed_at=(today-timedelta(days=7) if status=='CERRADA' else None),
            ))
    c=client_as('qa@example.org')
    for key,expected,not_expected in [
        ('vencido',['ATRASADO'],['CERRADO','HOY','ENPLAZO']),
        ('por_vencer',['HOY','MANANA'],['ATRASADO','ENPLAZO']),
        ('en_plazo',['ENPLAZO'],['ATRASADO','MANANA']),
        ('sin_sla',['SINPLAZO','CERRADO'],['ATRASADO','ENPLAZO']),
    ]:
        p=c.get('/casos',params={'q':unique,'sla':key})
        assert p.status_code==200,p.text[:300]
        for name in expected:
            assert f'SLA-{unique}-{name}' in p.text,(key,name)
        for name in not_expected:
            assert f'SLA-{unique}-{name}' not in p.text,(key,name)
        assert 'Días en proceso' in p.text
        assert 'Primer registro' in p.text
        assert 'name="sla"' in p.text
    # Invalid SLA values are rejected rather than interpolated into SQL.
    assert c.get('/casos',params={'sla':'unexpected'}).status_code==400
    closed_id=f'SLA-{unique}-CERRADO'
    detail=c.get('/casos/'+closed_id)
    assert detail.status_code==200
    assert 'Duración hasta el cierre' in detail.text
    with SessionLocal() as db:
        closed=db.get(Case,closed_id)
        age=case_age_info(closed,today=today)
        assert age['duration_days']==3
        assert age['duration_note']=='Hasta el cierre registrado'


def test_form_actions_are_consolidated_into_case_filters():
    admin=client_as('qa@example.org')
    home=admin.get('/panel')
    assert home.status_code==200
    assert '<strong>Expedientes y formularios</strong>' in home.text
    assert 'href="/casos?accion=revision"' not in home.text
    assert 'href="/casos?accion=seguimiento"' not in home.text
    assert 'href="/casos?accion=juridica"' not in home.text
    assert 'href="/casos/nuevo"' in home.text
    listing=admin.get('/casos')
    assert listing.status_code==200
    assert 'name="accion"' in listing.text
    assert 'id="case-action-input"' in listing.text
    for action in ('revision','seguimiento','juridica'):
        assert f'<option value="{action}"' in listing.text
        result=admin.get('/casos',params={'accion':action})
        assert result.status_code==200
        assert f'<option value="{action}" selected' in result.text
        assert 'href="/casos?accion=' not in result.text
    clean=admin.get('/casos',params={'accion':'revision','q':'no-existe'})
    assert 'href="/casos">Limpiar</a>' in clean.text
    viewer=client_as('viewer@example.org')
    page=viewer.get('/casos')
    assert page.status_code==200
    assert 'name="accion"' not in page.text
    assert viewer.get('/casos',params={'accion':'revision'}).status_code==403
    assert viewer.get('/casos/nuevo').status_code==403


def test_personal_queue_team_search_and_one_click_report():
    from datetime import datetime, timedelta, timezone
    key=uuid.uuid4().hex[:10]
    username='Laura Especial '+key
    email='laura-'+key+'@example.org'
    open_id='QA-BANDEJA-'+key
    other_id='QA-BANDEJA-OTRO-'+key
    archived_id='QA-BANDEJA-CERRADO-'+key
    with SessionLocal.begin() as db:
        db.add(User(name=username,email=email,
                    password_hash=ph.hash('Contraseña-Muy-Segura-2026!'),
                    role='consulta',active=True,form_permissions=['revision','casos']))
        for ident, reviewer, status in [
            (open_id,username,'EN_PRIMERA_REVISION'),
            (other_id,'Persona Distinta','EN_PRIMERA_REVISION'),
            (archived_id,username,'CERRADA'),
        ]:
            db.add(Case(id_caso=ident,source='web',contractor_name='Contratista '+key,
                        contract_no='CT-'+key,document='123'+key,period='202611',
                        followup_assignee='Otro Gestor',reviewer=reviewer,
                        current_status=status,current_owner='Revisor',
                        requires_reviewer=True,
                        registered_at=datetime.now(timezone.utc),
                        sla_due=datetime.now(timezone.utc).date()+timedelta(days=1)))
        from app.identity import rebuild_assignments
        rebuild_assignments(db)
    person=client_as(email)
    mine=person.get('/bandeja')
    assert mine.status_code==200,mine.text[:500]
    assert open_id in mine.text
    assert other_id not in mine.text and archived_id not in mine.text
    assert '/casos/'+open_id+'/revisor' in mine.text
    assert 'Registrar novedad' in mine.text
    team=person.get('/bandeja',params={'vista':'equipo','q':'CT-'+key})
    assert team.status_code==200
    assert open_id in team.text and other_id in team.text and archived_id not in team.text
    assert person.get('/bandeja',params={'gestion':'juridica'}).status_code==403
    assert person.get('/bandeja',params={'vista':'bogus'}).status_code==400
    assert person.get('/bandeja',params={'sla':'unknown'}).status_code==400
    assert person.get('/informes').status_code==403
    home=person.get('/panel')
    assert home.status_code==200 and 'Mi bandeja' in home.text
    assert person.get('/casos/'+open_id+'/revisor').status_code==200
    assert 'Volver a mi bandeja' in person.get('/casos/'+open_id+'/revisor').text
    viewer=client_as('viewer@example.org')
    assert viewer.get('/bandeja').status_code==403


def test_percentage_dashboard_denominators_and_radication_aggregation():
    from datetime import datetime, timedelta, timezone
    from app.models import RadContract, RadMonth
    from sqlalchemy import select
    key=uuid.uuid4().hex[:8]
    owner='Analista QA '+key
    current=datetime.now(timezone.utc)
    cases=[('CERRADA',None),('RECIBIDA',current.date()-timedelta(days=1)),
           ('RECIBIDA',current.date()+timedelta(days=3)),('RECIBIDA',None)]
    with SessionLocal.begin() as db:
        for i,(status,due) in enumerate(cases):
            c=Case(id_caso=f'QA-PCT-{key}-{i}',source='web',
                contractor_name='Personaje QA',period='202612',
                followup_assignee=owner,current_status=status,
                current_owner='Seguimiento',sla_due=due,
                received_at=current,registered_at=current)
            db.add(c)
            if i==1:
                db.add(Event(case_id=c.id_caso,raw_case_id=c.id_caso,
                             kind='DEVUELTA_POR_FINANCIERA',source='web'))
    admin=client_as('qa@example.org')
    report=admin.get('/informes',params={'periodo':'202612','responsable':owner})
    assert report.status_code==200,report.text[:200]
    assert 'Gestion y cumplimiento' in report.text
    assert '25.0' in report.text and '50.0' in report.text
    assert '1 de 4 expedientes' in report.text
    assert '1 de 2 expedientes' in report.text
    assert 'Casos con al menos un evento de devolucion' in report.text
    assert admin.get('/informes',params={'periodo':'2026AA'}).status_code==400
    no_data=admin.get('/informes',params={'periodo':'202701'})
    assert no_data.status_code==200
    assert 'Sin datos' in no_data.text
    assert 'Radicacion registrada' in report.text
    homepage=admin.get('/panel')
    assert 'Mi bandeja de trabajo' in homepage.text


def test_new_identity_links_cases_and_resolves_ambiguous_names():
    from datetime import datetime, timezone
    from app.models import CaseAssignment, UserAlias
    from app.identity import identity_key
    key=uuid.uuid4().hex[:9]
    firstname='Brian'+key
    fullname=firstname+' Pérez Montoya'
    email='brian-'+key+'@example.org'
    ids=[f'QA-ID-{key}-{n}' for n in range(3)]
    with SessionLocal.begin() as db:
        for cid,who in zip(ids,[fullname,'Bryan '+key,firstname]):
            db.add(Case(id_caso=cid,source='import',reviewer=who,current_owner='Revisor',
                        current_status='EN_PRIMERA_REVISION',requires_reviewer=True,
                        registered_at=datetime.now(timezone.utc)))
    admin=client_as('qa@example.org')
    page=admin.get('/usuarios')
    created=admin.post('/usuarios',data={'csrf':token(page.text),'name':fullname,
        'email':email,'aliases':'Bryan '+key+'; '+firstname+' Pérez',
        'permissions':['revision','casos']})
    assert created.status_code==200 and 'INVITACIÓN GENERADA' in created.text
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email==email));uid=user.id
        linked=set(db.scalars(select(CaseAssignment.case_id).where(
            CaseAssignment.user_id==uid)).all())
        assert set(ids).issubset(linked)
        assert db.scalar(select(UserAlias.id).where(UserAlias.user_id==uid)) is not None
    page=admin.get('/usuarios')
    created2=admin.post('/usuarios',data={'csrf':token(page.text),
        'name':firstname+' Ruiz','email':'other-'+email,
        'permissions':['revision','casos']})
    assert created2.status_code==200
    with SessionLocal() as db:
        assert db.scalar(select(CaseAssignment.id).where(CaseAssignment.case_id==ids[2])) is None
        assert db.scalar(select(CaseAssignment.id).where(
            CaseAssignment.case_id==ids[0],CaseAssignment.user_id==uid)) is not None
    page=admin.get('/usuarios')
    assigned=admin.post(f'/usuarios/{uid}/asignar',data={
        'csrf':token(page.text),'case_id':ids[2],'workflow':'revision'})
    assert assigned.status_code==200
    with SessionLocal() as db:
        link=db.scalar(select(CaseAssignment).where(CaseAssignment.case_id==ids[2]))
        assert link is not None and link.user_id==uid and link.source=='manual'
    assert identity_key(' BRIÁN  Pérez ') == identity_key('brian perez')


def test_identity_alias_collision_rejected():
    key=uuid.uuid4().hex[:9]
    with SessionLocal.begin() as db:
        db.add(User(name='Persona '+key,email='identity'+key+'@example.org',
                    password_hash=ph.hash('Contraseña-Muy-Segura-2026!'),
                    role='consulta',active=True,form_permissions=['revision','casos']))
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='identity'+key+'@example.org'))
        uid=user.id
    admin=client_as('qa@example.org')
    page=admin.get('/usuarios')
    result=admin.post(f'/usuarios/{uid}/identidad',data={'csrf':token(page.text),
        'name':'Persona '+key,'aliases':'Administrador QA'})
    assert result.status_code==400


def test_double_entry_queues_move_between_users():
    """Seguimiento ve el caso siempre; quien entrega pierde su tarea de etapa."""
    from app.models import CaseAssignment
    from datetime import datetime, timezone
    key=uuid.uuid4().hex[:8]
    accounts={}
    with SessionLocal.begin() as db:
        for role, prefix, permissions in [
            ('control','Carol', ['seguimiento','casos']),
            ('first','Andrea', ['revision','casos']),
            ('second','Allison', ['segunda_revision','casos']),
        ]:
            user=User(name=f'{prefix} {key}',email=f'{prefix.lower()}-{key}@example.org',
                      password_hash=ph.hash('Contraseña-Muy-Segura-2026!'),
                      role='consulta',active=True,form_permissions=permissions)
            db.add(user);db.flush()
            accounts[role]={'id':user.id,'email':user.email}
    admin=client_as('qa@example.org')
    case_page=admin.get('/casos/nuevo')
    assert case_page.status_code==200
    payload={'csrf':token(case_page.text),'contractor_type':'CC',
             'contractor_name':'Recibo Doble Entrada '+key,'document':'9011'+key,
             'contract_no':'DOBLE-'+key,'period':'202611',
             'amount':'100000','contractor_email':'doble-'+key+'@example.org',
             'control_user_id':str(accounts['control']['id']),
             'reviewer':f'Andrea {key}','special':'No','sent_by':'Supervisor',
             'requires_reviewer':'Sí','requires_technical':'No','requires_legal':'No'}
    result=admin.post('/casos/nuevo',data=payload)
    assert result.status_code==303,result.text[:300]
    case_id=result.headers['location'].split('/')[-1]
    carol=client_as(accounts['control']['email'])
    andrea=client_as(accounts['first']['email'])
    allison=client_as(accounts['second']['email'])
    assert case_id in carol.get('/bandeja?vista=control').text
    assert case_id in carol.get('/bandeja?vista=mios').text
    assert case_id not in andrea.get('/bandeja').text

    follow_page=carol.get(f'/casos/{case_id}/seguimiento')
    assert follow_page.status_code==200
    assert 'Enviado para firma Directora' not in follow_page.text
    rejected=carol.post(f'/casos/{case_id}/seguimiento',data={
        'csrf':token(follow_page.text),'accion':'Enviado para firma Directora',
        'observaciones':'Nunca debe crearse esta etapa'})
    assert rejected.status_code==400

    result=carol.post(f'/casos/{case_id}/seguimiento',data={
        'csrf':token(follow_page.text),'accion':'Enviado a revisor',
        'observaciones':'Envío de prueba a Andrea',
        'destino_usuario_id':str(accounts['first']['id'])})
    assert result.status_code==303,result.text[:300]
    assert case_id not in carol.get('/bandeja?vista=mios').text
    assert case_id in carol.get('/bandeja?vista=control').text
    assert case_id in andrea.get('/bandeja').text
    assert case_id not in allison.get('/bandeja').text
    assert andrea.get(f'/casos/{case_id}/revisor').status_code==200
    assert allison.get(f'/casos/{case_id}/revisor').status_code==403

    first_page=andrea.get(f'/casos/{case_id}/revisor')
    second_action=andrea.post(f'/casos/{case_id}/revisor',data={
        'csrf':token(first_page.text),'resultado':'Enviado a segunda revisión',
        'observaciones':'Andrea entrega a Allison',
        'destino_usuario_id':str(accounts['second']['id'])})
    assert second_action.status_code==303,second_action.text[:300]
    assert case_id not in andrea.get('/bandeja').text
    assert case_id in allison.get('/bandeja').text
    assert case_id in carol.get('/bandeja?vista=control').text
    assert andrea.get(f'/casos/{case_id}/revisor').status_code==409
    second_page=allison.get(f'/casos/{case_id}/segunda-revision')
    assert second_page.status_code==200
    assert 'Enviado a firma Directora' not in second_page.text

    returned=allison.post(f'/casos/{case_id}/segunda-revision',data={
        'csrf':token(second_page.text),'resultado':'Requiere ajustes',
        'observaciones':'Devolución para redistribución'})
    assert returned.status_code==303,returned.text[:300]
    assert case_id not in allison.get('/bandeja').text
    assert case_id in admin.get('/bandeja?vista=administracion').text
    assert case_id in carol.get('/bandeja?vista=control').text
    assert carol.get('/bandeja?vista=administracion').status_code==403
    redistribution=admin.get(f'/casos/{case_id}/redistribuir')
    assert redistribution.status_code==200,redistribution.text[:400]
    assigned=admin.post(f'/casos/{case_id}/redistribuir',data={
        'csrf':token(redistribution.text),'gestion':'seguimiento',
        'destino_usuario_id':str(accounts['control']['id'])})
    assert assigned.status_code==303,assigned.text[:300]
    assert case_id not in admin.get('/bandeja?vista=administracion').text
    assert case_id in carol.get('/bandeja?vista=mios').text
    assert case_id in carol.get('/bandeja?vista=control').text
    with SessionLocal() as db:
        rows=db.scalars(select(CaseAssignment).where(
            CaseAssignment.case_id==case_id)).all()
        assert len(rows)==1 and rows[0].workflow=='seguimiento'
        assert rows[0].user_id==accounts['control']['id']
    from datetime import date
    closed=carol.post(f'/casos/{case_id}/seguimiento',data={
        'csrf':token(carol.get(f'/casos/{case_id}/seguimiento').text),
        'accion':'Cerrar','observaciones':'Pago confirmado en financiera',
        'fecha_cierre':date.today().isoformat()})
    assert closed.status_code==303,closed.text[:300]
    assert case_id not in carol.get('/bandeja?vista=control').text
    assert case_id not in carol.get('/bandeja?vista=mios').text
    with SessionLocal() as db:
        assert db.get(Case,case_id).current_status=='CERRADA'
        assert db.get(Case,case_id).finance_closed_at is not None
    assert 'Enviado a firma Directora' not in str(__import__('app.services',fromlist=['REVIEW_ACTIONS']).REVIEW_ACTIONS)
    assert 'Enviado para firma Directora' not in str(__import__('app.services',fromlist=['FOLLOWUP_ACTIONS']).FOLLOWUP_ACTIONS)



def test_closed_cases_are_admin_only_across_routes():
    """El archivo administrativo no debe filtrarse por listas, detalle ni panel."""
    from datetime import datetime, timezone
    marker=uuid.uuid4().hex[:10]
    case_id='QA-ARCHIVE-'+marker
    with SessionLocal.begin() as db:
        db.add(Case(id_caso=case_id,source='web',contractor_name='ARCHIVED-SECRET-'+marker,
                    contract_no='ARCH-CONTRACT-'+marker,period='202611',
                    current_status='CERRADA',current_owner='Seguimiento',
                    created_at=datetime.now(timezone.utc),
                    registered_at=datetime.now(timezone.utc)))
    viewer=client_as('viewer@example.org')
    admin=client_as('qa@example.org')
    assert viewer.get('/casos?vista=cerrados').status_code==403
    for url in ('/casos', '/casos?vista=todos', '/casos?estado=CERRADA',
                '/casos?q='+marker, '/panel'):
        response=viewer.get(url)
        assert response.status_code==200,(url,response.status_code)
        assert case_id not in response.text,url
        assert 'ARCHIVED-SECRET-'+marker not in response.text,url
    assert viewer.get('/casos/'+case_id).status_code==403
    assert case_id in admin.get('/casos?vista=cerrados&q='+marker).text
    assert admin.get('/casos/'+case_id).status_code==200
