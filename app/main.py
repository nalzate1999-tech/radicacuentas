import csv
import io
import math
import os
import secrets
import shutil
import tempfile
import hashlib
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from argon2 import PasswordHasher, exceptions as argon_exc
from fastapi import FastAPI, Request, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, delete, func, or_, and_, desc, text, case as sql_case
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import get_settings
from .database import db_session, engine, SessionLocal
from .models import (Base, User, Case, Event, Subtask, RadContract, RadMonth,
                     ImportBatch, ImportIssue, Personnel, FormResponse, Audit, Notification, AuthAttempt, UserAlias, CaseAssignment)
from .services import (BOGOTA, FOLLOWUP_ACTIONS, REVIEW_ACTIONS, LEGAL_OUTCOMES,
                       REVIEWERS, PROVIDERS, SUBTASK_TYPES, BusinessError, create_case, add_event,
                       apply_followup, apply_review, apply_legal, sla_color, audit, utcnow, STOP_STATUSES)
from .case_metrics import case_age_info
from .identity import rebuild_assignments, replace_aliases, parse_aliases, WORKFLOWS
from .assignment_flow import (eligible_users, validate_target, assigned_to, assign_control,
                              transfer_stage, stage_for, stage_after_followup,
                              stage_after_review, adjust_new_event_owner)
from .analytics import report_data
from .work_queue import (queue_query, authorized_workflows, recommended_workflow,
                         WORKFLOW_LABELS)

settings=get_settings()
app=FastAPI(title='Radicación y seguimiento de cuentas',docs_url=None,redoc_url=None,openapi_url=None)
app.add_middleware(SessionMiddleware,secret_key=settings.secret,https_only=settings.session_https,
                   same_site='lax',session_cookie='rad_session',max_age=60*60*8)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=[x.strip() for x in settings.allowed_hosts.split(',')])
APP_DIR=Path(__file__).resolve().parent
app.mount('/static',StaticFiles(directory=str(APP_DIR/'static')),name='static')
templates=Jinja2Templates(directory=str(APP_DIR/'templates'))
ph=PasswordHasher(time_cost=3,memory_cost=65536,parallelism=4)
ROLES={'admin':'Administrador','seguimiento':'Seguimiento','revisor':'Revisor','abogados':'Firmas jurídica/técnica','consulta':'Consulta'}
FORM_PERMISSIONS={
    'registro':'Registro de cuentas',
    'revision':'Primera revisión',
    'segunda_revision':'Segunda revisión (usuario específico)',
    'seguimiento':'Gestión y seguimiento',
    'juridica':'Revisión jurídica y técnica',
}
EXTRA_PERMISSIONS={
    'casos':'Consulta de expedientes',
    'radicacion':'Radicación mensual (histórico)',
    'informes':'Gestión y cumplimiento',
    'exportar':'Exportación de información',
}
PERMISSIONS={**FORM_PERMISSIONS,**EXTRA_PERMISSIONS}
LEGACY_ROLE_PERMISSIONS={
    'seguimiento':{'registro','seguimiento','casos','radicacion','informes','exportar'},
    'revisor':{'revision','casos','radicacion','informes','exportar'},
    'abogados':{'juridica','casos','radicacion','informes','exportar'},
    'consulta':{'casos','radicacion','informes','exportar'},
}

def user_permissions(user):
    if not user or not user.active:return set()
    if user.role=='admin':return set(PERMISSIONS)
    if user.form_permissions is not None:
        return set(user.form_permissions).intersection(PERMISSIONS)
    return LEGACY_ROLE_PERMISSIONS.get(user.role,set())

def can(user,permission):
    perms=user_permissions(user)
    # Una función sobre expedientes requiere lectura del caso, no acceso a informes ni exportaciones.
    return permission in perms or (permission=='casos' and bool(perms.intersection(FORM_PERMISSIONS)))

def require_permission(request,db,permission):
    user=require_user(request,db)
    if not can(user,permission):raise HTTPException(403,'No tiene asignada esta herramienta.')
    return user

def invitation_url(token):
    return settings.app_url+'/activar/'+token

def find_invitation(db,token):
    if not token or len(token)>200:return None
    token_hash=hashlib.sha256(token.encode()).hexdigest()
    u=db.scalar(select(User).where(User.invite_token_hash==token_hash))
    if not u or u.active or not u.invite_expires_at:return None
    expiry=u.invite_expires_at
    if expiry.tzinfo is None:expiry=expiry.replace(tzinfo=timezone.utc)
    return u if expiry>datetime.now(timezone.utc) else None


@app.exception_handler(401)
async def unauthenticated(request, exc):
    if request.method == 'GET' and 'text/html' in request.headers.get('accept', '').lower():
        return RedirectResponse('/ingresar?sesion=expirada', status_code=303)
    return JSONResponse({'detail': 'Debe iniciar sesion.'}, status_code=401,
                        headers={'Cache-Control': 'no-store'})

@app.exception_handler(403)
async def forbidden(request, exc):
    return HTMLResponse('<h1>Acceso denegado (403)</h1><p>No tiene permisos para esta operación. <a href="/">Inicio</a></p>',403)

@app.get('/health')
def health():
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql('SELECT 1')
        return {'status':'ok'}
    except Exception:
        return JSONResponse({'status':'database-unavailable'},status_code=503)

def current_user(request,db):
    uid=request.session.get('uid')
    if not isinstance(uid,int):return None
    user=db.get(User,uid)
    return user if user and user.active else None

def require_user(request,db,allowed=None):
    user=current_user(request,db)
    if user is None:raise HTTPException(401,'Debe iniciar sesión.')
    if allowed and user.role not in set(allowed)|{'admin'}:
        raise HTTPException(403,'Sin permisos para este proceso.')
    return user

def check_csrf(request,token):
    if not token or not secrets.compare_digest(str(token),str(request.session.get('csrf',''))):
        raise HTTPException(403,'Sesión o formulario vencido. Actualice la página.')

def ctx(request,db,**extra):
    u=current_user(request,db)
    if not request.session.get('csrf'):request.session['csrf']=secrets.token_urlsafe(32)
    return dict(request=request,user=u,csrf=request.session['csrf'],roles=ROLES,can=lambda perm:can(u,perm),form_permissions=FORM_PERMISSIONS,permission_labels=PERMISSIONS,**extra)

def render(request,db,template,**extra):
    return templates.TemplateResponse(request,template,ctx(request,db,**extra))

def redirect(path):return RedirectResponse(path,status_code=303)


def bootstrap_authorized(request: Request) -> bool:
    """Only a time-limited signed session, established via a private link, may bootstrap admin."""
    if not settings.bootstrap_token:
        return False
    timestamp = request.session.get('bootstrap_verified_at')
    signature = request.session.get('bootstrap_signature')
    now = int(datetime.now(timezone.utc).timestamp())
    if not isinstance(timestamp, int) or timestamp < now - 900 or timestamp > now + 30:
        return False
    expected = hashlib.sha256(settings.bootstrap_token.encode()).hexdigest()
    return isinstance(signature, str) and secrets.compare_digest(signature, expected)


def bootstrap_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.get('/configuracion-inicial')
def initial_setup(request: Request, db: Session = Depends(db_session)):
    if db.scalar(select(func.count()).select_from(User)):
        return bootstrap_headers(redirect('/panel' if current_user(request, db) else '/ingresar?configuracion=lista'))
    if not settings.bootstrap_token or not settings.bootstrap_email:
        raise HTTPException(404)
    return bootstrap_headers(render(request, db, 'setup.html', error=None,
                                    admin_email=settings.bootstrap_email,
                                    authorized=bootstrap_authorized(request)))


@app.post('/configuracion-inicial/validar')
def verify_initial_setup_link(request: Request, csrf: str = Form(...), token: str = Form(...),
                              db: Session = Depends(db_session)):
    """Validate a bearer token delivered in the URL fragment; never embed it in public HTML."""
    check_csrf(request, csrf)
    if not settings.bootstrap_token or not settings.bootstrap_email or db.scalar(select(func.count()).select_from(User)):
        raise HTTPException(404)
    ip = request.client.host if request.client else 'unknown'
    fingerprint = hashlib.sha256(('bootstrap:' + ip).encode()).hexdigest()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    failures = db.scalar(select(func.count()).select_from(AuthAttempt).where(
        AuthAttempt.email_hash == fingerprint, AuthAttempt.success == False,
        AuthAttempt.occurred_at >= cutoff)) or 0
    if failures >= 8:
        return bootstrap_headers(JSONResponse({'ok': False, 'message': 'Demasiados intentos. Vuelve a intentar más tarde.'}, status_code=429))
    valid = len(token) >= 32 and secrets.compare_digest(token, settings.bootstrap_token)
    db.add(AuthAttempt(email_hash=fingerprint, success=valid))
    db.commit()
    if not valid:
        return bootstrap_headers(JSONResponse({'ok': False, 'message': 'El enlace de activación no es válido.'}, status_code=403))
    request.session['bootstrap_verified_at'] = int(datetime.now(timezone.utc).timestamp())
    request.session['bootstrap_signature'] = hashlib.sha256(settings.bootstrap_token.encode()).hexdigest()
    return bootstrap_headers(JSONResponse({'ok': True}))


@app.post('/configuracion-inicial')
def initial_setup_post(request: Request, csrf: str = Form(...),
                       name: str = Form(...), email: str = Form(...), password: str = Form(...),
                       confirm_password: str = Form(...), db: Session = Depends(db_session)):
    check_csrf(request, csrf)
    if not settings.bootstrap_token or not settings.bootstrap_email:
        raise HTTPException(404)
    if not bootstrap_authorized(request):
        raise HTTPException(403, 'Abre el enlace privado de activación para configurar el administrador.')
    # Impedir creación simultánea del primer administrador.
    if db.bind.dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(731020)'))
    if db.scalar(select(func.count()).select_from(User)):
        raise HTTPException(404)
    valid = (email.strip().lower() == settings.bootstrap_email
             and len(password) >= 12 and password == confirm_password and bool(name.strip()))
    if not valid:
        return bootstrap_headers(templates.TemplateResponse(request, 'setup.html', ctx(request, db,
            error='Verifica el correo autorizado y que las contraseñas coincidan y tengan al menos 12 caracteres.',
            admin_email=settings.bootstrap_email, authorized=True), status_code=400))
    db.add(User(name=name.strip()[:160], email=settings.bootstrap_email, password_hash=ph.hash(password),
                role='admin', active=True, form_permissions=list(PERMISSIONS)))
    audit(db, 'configuracion-inicial', 'CREAR_ADMIN', settings.bootstrap_email)
    db.commit()
    request.session.clear()
    return redirect('/ingresar?configurado=1')


@app.get('/activar/{token}')
def activation_page(request:Request,token:str,db:Session=Depends(db_session)):
    target=find_invitation(db,token)
    if not target:raise HTTPException(404,'Invitación no válida o vencida.')
    response=render(request,db,'activation.html',target=target,token=token,error=None)
    response.headers['Cache-Control']='no-store'
    response.headers['Referrer-Policy']='no-referrer'
    return response

@app.post('/activar/{token}')
def activate(request:Request,token:str,csrf:str=Form(...),password:str=Form(...),
             confirm_password:str=Form(...),db:Session=Depends(db_session)):
    check_csrf(request,csrf)
    target=find_invitation(db,token)
    if not target:raise HTTPException(404,'Invitación no válida o vencida.')
    if len(password)<12 or password!=confirm_password:
        response=templates.TemplateResponse(request,'activation.html',ctx(request,db,
          target=target,token=token,error='Las contraseñas deben coincidir y tener al menos 12 caracteres.'),status_code=400)
        response.headers['Cache-Control']='no-store'
        return response
    target.password_hash=ph.hash(password)
    target.active=True
    target.invite_token_hash=None
    target.invite_expires_at=None
    audit(db,target.email,'ACTIVAR_USUARIO',target.email)
    db.commit()
    return redirect('/ingresar?activado=1')

@app.get('/')
def index(request:Request):
    return redirect('/panel' if request.session.get('uid') else '/ingresar')

@app.get('/ingresar')
def login_page(request:Request,db:Session=Depends(db_session)):
    if current_user(request,db):return redirect('/panel')
    return render(request,db,'login.html',error=None,setup_available=bool(bootstrap_authorized(request) and settings.bootstrap_email and not db.scalar(select(func.count()).select_from(User))))

@app.post('/ingresar')
def login(request:Request,email:str=Form(...),password:str=Form(...),csrf:str=Form(...),db:Session=Depends(db_session)):
    check_csrf(request,csrf)
    email=email.strip().lower()
    token_hash=hashlib.sha256(email.encode()).hexdigest()
    window=datetime.now(timezone.utc)-timedelta(minutes=15)
    recent_bad=db.scalar(select(func.count()).select_from(AuthAttempt).where(
            AuthAttempt.email_hash==token_hash, AuthAttempt.success==False,
            AuthAttempt.occurred_at>=window)) or 0
    if recent_bad>=8:
        return templates.TemplateResponse(request,'login.html',ctx(request,db,
                error='Demasiados intentos. Vuelva a intentar después de 15 minutos.',setup_available=False),status_code=429)
    user=db.scalar(select(User).where(User.email==email))
    valid=False
    if user and user.active:
        try:valid=ph.verify(user.password_hash,password)
        except (argon_exc.VerifyMismatchError,argon_exc.InvalidHashError,argon_exc.VerificationError):pass
    db.add(AuthAttempt(email_hash=token_hash,success=valid));db.commit()
    if not valid:
        return templates.TemplateResponse(request,'login.html',ctx(request,db,error='Credenciales incorrectas.',setup_available=False),status_code=401)
    request.session.clear()
    request.session['uid']=user.id
    request.session['csrf']=secrets.token_urlsafe(32)
    return redirect('/panel')

@app.post('/salir')
def logout(request:Request,csrf:str=Form(...)):
    check_csrf(request,csrf)
    request.session.clear()
    return redirect('/ingresar')

@app.get('/panel')
def dashboard(request:Request,db:Session=Depends(db_session)):
    u=require_user(request,db)
    if not user_permissions(u):
        return render(request,db,'dashboard.html',total=0,closed=0,overdue=0,movements=0,by_status=[],recent=[],sla_color=sla_color,recent_age={},inbox=[],inbox_count=0,inbox_scope='mios',inbox_actions={})
    visible_cases = (select(Case) if u.role == 'admin'
                     else select(Case).where(~Case.current_status.in_(ARCHIVED_STATUSES)))
    total=db.scalar(select(func.count()).select_from(visible_cases.subquery())) or 0
    closed=(db.scalar(select(func.count()).select_from(Case)
               .where(Case.current_status.in_(ARCHIVED_STATUSES))) or 0) if u.role == 'admin' else 0
    now=datetime.now(BOGOTA).date()
    overdue=db.scalar(select(func.count()).select_from(Case).where(Case.sla_due<now,~Case.current_status.in_([*ARCHIVED_STATUSES, 'EN_MESA_DIRECTIVA', 'ENVIADA_A_FINANCIERA']))) or 0
    events=(db.scalar(select(func.count()).select_from(Event)) or 0) if u.role == 'admin' else (
        db.scalar(select(func.count()).select_from(Event)
                  .join(Case, Event.case_id == Case.id_caso)
                  .where(~Case.current_status.in_(ARCHIVED_STATUSES))) or 0)
    by_status=db.execute(
        select(Case.current_status,func.count(Case.id_caso))
        .where(True if u.role == 'admin' else ~Case.current_status.in_(ARCHIVED_STATUSES))
        .group_by(Case.current_status)
        .order_by(desc(func.count(Case.id_caso))).limit(12)
    ).all()
    recent=db.scalars(visible_cases.order_by(desc(Case.created_at),desc(Case.id_caso))
                      .limit(12)).all()
    inbox_scope='equipo' if u.role=='admin' else 'mios'
    inbox=[];inbox_count=0;inbox_actions={}
    perms=user_permissions(u)
    if authorized_workflows(perms):
        iq=queue_query(perms,u,scope=inbox_scope)
        inbox_count=db.scalar(select(func.count()).select_from(iq.subquery())) or 0
        inbox=db.scalars(iq.order_by(Case.sla_due.asc().nulls_last(),Case.registered_at.desc())
                         .limit(6)).all()
        inbox_actions={c.id_caso:recommended_workflow(c,perms) for c in inbox}
    return render(request,db,'dashboard.html',total=total,closed=closed,overdue=overdue,
                  movements=events,by_status=by_status,recent=recent,sla_color=sla_color,
                  recent_age=_case_age_map(db,recent),inbox=inbox,inbox_count=inbox_count,
                  inbox_scope=inbox_scope,inbox_actions=inbox_actions)

# Estados archivados: se incluyen en informes e historial, nunca en una bandeja de trabajo.
ARCHIVED_STATUSES = frozenset({'CERRADA', 'CERRADO', 'PAGADA', 'PAGADO'})

SLA_FILTERS = frozenset({'vencido', 'por_vencer', 'en_plazo', 'sin_sla'})


def _sla_filter(sla, today):
    active = ~Case.current_status.in_(STOP_STATUSES)
    tomorrow = today + timedelta(days=1)
    if sla == 'vencido':
        return and_(active, Case.sla_due.is_not(None), Case.sla_due < today)
    if sla == 'por_vencer':
        return and_(active, Case.sla_due >= today, Case.sla_due <= tomorrow)
    if sla == 'en_plazo':
        return and_(active, Case.sla_due > tomorrow)
    return or_(Case.sla_due.is_(None), Case.current_status.in_(STOP_STATUSES))


def _case_age_map(db, rows):
    """One aggregate query for the visible cases, avoiding per-row DB lookups."""
    ids=[c.id_caso for c in rows]
    if not ids:
        return {}
    history=db.execute(select(
        Event.case_id,
        func.min(Event.occurred_at),
        func.min(sql_case((Event.resulting_status.in_(ARCHIVED_STATUSES), Event.occurred_at), else_=None))
    ).where(Event.case_id.in_(ids)).group_by(Event.case_id)).all()
    found={case_id:(first,close) for case_id,first,close in history}
    today=datetime.now(BOGOTA).date()
    return {c.id_caso:case_age_info(c, *(found.get(c.id_caso,(None,None))),today=today) for c in rows}


def _contains(value: str) -> str:
    """Escapa comodines de LIKE para que el campo sea una busqueda literal."""
    return '%' + value.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'


def _case_search(token: str):
    """Busca por contratista, referencias y responsables sin generar duplicados."""
    term = _contains(token)
    main_columns = (
        Case.id_caso, Case.contractor_name, Case.document, Case.contract_no,
        Case.contractor_email, Case.contractor_phone, Case.supervisor_name,
        Case.supervisor_email, Case.followup_assignee, Case.reviewer,
        Case.current_owner, Case.sent_by,
    )
    in_case = [col.ilike(term, escape='!') for col in main_columns]
    in_task = (select(Subtask.id)
               .where(Subtask.case_id == Case.id_caso,
                      or_(Subtask.responsible.ilike(term, escape='!'),
                          Subtask.provider.ilike(term, escape='!')))
               .exists())
    return or_(*in_case, in_task)


def stage_destination_lists(db):
    return {name:eligible_users(db,name)
            for name in ('revision','segunda_revision','juridica')}


def require_stage_assignment(db, case, user, stage):
    if stage_for(case) != stage:
        raise HTTPException(409,'La etapa del expediente cambió. Actualice su bandeja.')
    if user.role != 'admin' and not assigned_to(db,case.id_caso,user.id,stage):
        raise HTTPException(403,'Esta etapa está asignada a otra persona.')


def followup_access(db, case, user):
    if user.role != 'admin' and not assigned_to(db,case.id_caso,user.id,'seguimiento'):
        raise HTTPException(403,'No es la persona titular de control y seguimiento.')


@app.get('/bandeja')
def work_inbox(request:Request,q:str='',gestion:str='',vista:str='',sla:str='',page:int=1,
               db:Session=Depends(db_session)):
    u=require_user(request,db)
    perms=user_permissions(u)
    allowed=authorized_workflows(perms)
    if not allowed:
        raise HTTPException(403,'No tiene formularios de gestion autorizados.')
    if gestion and gestion not in allowed:
        raise HTTPException(403,'No tiene autorizado ese formulario.')
    scope=vista or ('administracion' if u.role=='admin' else 'mios')
    if scope not in ('mios','equipo','control','administracion'):
        raise HTTPException(400,'Vista de bandeja no reconocida.')
    if sla and sla not in SLA_FILTERS:
        raise HTTPException(400,'Estado SLA no reconocido.')
    try:query=queue_query(perms,u,workflow=gestion,scope=scope)
    except PermissionError as exc:raise HTTPException(403,str(exc))
    cleaned=' '.join(q.split())[:140]
    for token in cleaned.split(' ')[:6] if cleaned else []:
        query=query.where(_case_search(token))
    today=datetime.now(BOGOTA).date()
    if sla:
        query=query.where(_sla_filter(sla,today))
    total=db.scalar(select(func.count()).select_from(query.subquery())) or 0
    page=max(1,page)
    per_page=20
    priority=sql_case((Case.sla_due < today,0),
                      (Case.sla_due <= today+timedelta(days=1),1),else_=2)
    rows=db.scalars(query.order_by(priority,Case.sla_due.asc().nulls_last(),
                Case.registered_at.desc(),Case.id_caso.desc())
                .limit(per_page).offset((page-1)*per_page)).all()
    actions={c.id_caso:(
        'redistribuir' if scope=='administracion' else
        'seguimiento' if scope=='control' else
        None if scope=='equipo' else recommended_workflow(c,perms,gestion)
    ) for c in rows}
    return render(request,db,'inbox.html',rows=rows,total=total,page=page,
                  pages=max(1,math.ceil(total/per_page)),gestion=gestion,vista=scope,
                  q=cleaned,sla=sla,allowed_workflows=allowed,
                  workflow_labels=WORKFLOW_LABELS,actions=actions,sla_color=sla_color,
                  admin=(u.role=='admin'),can_control=('seguimiento' in perms),
                  case_age=_case_age_map(db,rows))

@app.get('/casos')
def cases(request:Request,q:str='',estado:str='',periodo:str='',accion:str='',
          vista:str='todos',sla:str='',page:int=1,db:Session=Depends(db_session)):
    require_permission(request,db,'casos')
    if accion and accion not in ('revision','segunda_revision','seguimiento','juridica'):
        raise HTTPException(400,'Formulario no reconocido.')
    if accion:require_permission(request,db,accion)
    if vista not in ('todos', 'pendientes', 'cerrados'):
        raise HTTPException(400, 'Filtro no reconocido.')
    if sla and sla not in SLA_FILTERS:
        raise HTTPException(400, 'Estado SLA no reconocido.')
    # Las cuentas archivadas son exclusivamente de consulta administrativa.
    user = require_user(request, db)
    if user.role != 'admin' and vista == 'cerrados':
        raise HTTPException(403, 'El archivo de cuentas cerradas es exclusivo de administración.')
    # Los usuarios operativos no pueden recuperar cuentas archivadas con filtros.
    effective_view = 'pendientes' if (accion or user.role != 'admin') else vista
    query=select(Case)
    if effective_view == 'pendientes':
        query=query.where(~Case.current_status.in_(ARCHIVED_STATUSES))
    elif effective_view == 'cerrados':
        query=query.where(Case.current_status.in_(ARCHIVED_STATUSES))
    if accion == 'revision':
        query=query.where(Case.requires_reviewer.is_(True))
    elif accion == 'juridica':
        query=query.where(or_(Case.requires_legal.is_(True), Case.requires_technical.is_(True)))
    cleaned=' '.join(q.split())[:140]
    tokens=cleaned.split(' ')[:6] if cleaned else []
    if tokens:
        query=query.where(and_(*(_case_search(token) for token in tokens)))
    if estado.strip():query=query.where(Case.current_status==estado.strip())
    if periodo.strip():query=query.where(Case.period==periodo.strip()[:160])
    if sla:
        query=query.where(_sla_filter(sla, datetime.now(BOGOTA).date()))
    total=db.scalar(select(func.count()).select_from(query.subquery())) or 0
    page=max(1,page);page_size=30
    rows=db.scalars(query.order_by(desc(Case.registered_at),desc(Case.id_caso))
                    .limit(page_size).offset((page-1)*page_size)).all()
    status_query=select(Case.current_status).distinct()
    if effective_view == 'pendientes':
        status_query=status_query.where(~Case.current_status.in_(ARCHIVED_STATUSES))
    elif effective_view == 'cerrados':
        status_query=status_query.where(Case.current_status.in_(ARCHIVED_STATUSES))
    statuses=db.scalars(status_query.order_by(Case.current_status)).all()
    return render(request,db,'cases.html',rows=rows,total=total,page=page,pages=max(1,math.ceil(total/page_size)),
                  q=cleaned,estado=estado,periodo=periodo,accion=accion,vista=effective_view,
                  sla=sla,case_age=_case_age_map(db,rows),
                  statuses=statuses,sla_color=sla_color,archived_statuses=ARCHIVED_STATUSES)

def registration_directory(db):
    personnel=db.scalars(select(Personnel).order_by(Personnel.name)).all()
    users=db.scalars(select(User).where(User.role != 'admin')).all()
    followup_names={p.name for p in personnel} | {'Brayan'}
    followup_names.update(u.name for u in users if 'seguimiento' in (u.form_permissions or []))
    reviewer_names=set(REVIEWERS)
    reviewer_names.update(u.name for u in users if 'revision' in (u.form_permissions or []))
    return sorted(followup_names), sorted(reviewer_names), eligible_users(db,'seguimiento')


@app.get('/casos/nuevo')
def register_form(request:Request,db:Session=Depends(db_session)):
    require_permission(request,db,'registro')
    names,reviewers,controllers=registration_directory(db)
    return render(request,db,'register.html',assignees=names,reviewers=reviewers,
                  controllers=controllers,error=None,form={})

@app.post('/casos/nuevo')
def register(request:Request,csrf:str=Form(...),contractor_type:str=Form(...),
             contractor_name:str=Form(...),document:str=Form(...),contract_no:str=Form(...),
             period:str=Form(...),amount:str=Form(...),contractor_email:str=Form(...),
             contractor_phone:str=Form(''),followup_assignee:str=Form(''),
             control_user_id:int=Form(0),
             reviewer:list[str]=Form([]),special:str=Form('No'),sent_by:str=Form(...),
             requires_reviewer:str=Form('No'),requires_technical:str=Form('No'),
             requires_legal:str=Form('No'),db:Session=Depends(db_session)):
    u=require_permission(request,db,'registro');check_csrf(request,csrf)
    data=locals().copy()
    data['reviewer']=', '.join(reviewer)
    try:
        controller=validate_target(db,control_user_id,'seguimiento')
        data['followup_assignee']=controller.name
        case=create_case(db,data,u.email)
        db.flush()
        assign_control(db,case,controller.id)
        rebuild_assignments(db, case_ids=[case.id_caso])
        db.commit()
        return redirect('/casos/'+quote(case.id_caso))
    except (BusinessError,IntegrityError) as e:
        db.rollback()
        names,reviewers,controllers=registration_directory(db)
        return templates.TemplateResponse(request,'register.html',ctx(request,db,
            assignees=names,reviewers=reviewers,controllers=controllers,
            error=str(e) if isinstance(e,BusinessError) else 'Registro duplicado o error de integridad.',
            form=data),status_code=400)

@app.get('/casos/{case_id}')
def case_detail(request:Request,case_id:str,event_page:int=1,db:Session=Depends(db_session)):
    require_permission(request,db,'casos')
    case=db.get(Case,case_id)
    if not case:raise HTTPException(404,'Caso no encontrado')
    if (case.current_status in ARCHIVED_STATUSES
            and require_user(request, db).role != 'admin'):
        raise HTTPException(403, 'El historial de cuentas cerradas es exclusivo de administración.')
    event_page=max(1,event_page)
    events=db.scalars(select(Event).where(Event.case_id==case_id)
                      .order_by(Event.occurred_at.desc(),Event.id.desc())
                      .limit(60).offset((event_page-1)*60)).all()
    n_events=db.scalar(select(func.count()).select_from(Event).where(Event.case_id==case_id)) or 0
    tasks=db.scalars(select(Subtask).where(Subtask.case_id==case_id).order_by(Subtask.kind)).all()
    responses=db.scalars(select(FormResponse).where(FormResponse.case_id==case_id)
                         .order_by(FormResponse.submitted_at.desc()).limit(15)).all()
    user=require_user(request,db)
    control=db.scalar(select(User.name).join(CaseAssignment,CaseAssignment.user_id==User.id)
                      .where(CaseAssignment.case_id==case_id,
                             CaseAssignment.workflow=='seguimiento').limit(1))
    current_assignee=db.scalar(select(User.name).join(CaseAssignment,CaseAssignment.user_id==User.id)
                        .where(CaseAssignment.case_id==case_id,
                               CaseAssignment.workflow==stage_for(case)).limit(1))
    is_followup=(user.role=='admin' or assigned_to(db,case_id,user.id,'seguimiento'))
    can_review=(stage_for(case)=='revision' and can(user,'revision') and
                (user.role=='admin' or assigned_to(db,case_id,user.id,'revision')))
    can_second=(stage_for(case)=='segunda_revision' and can(user,'segunda_revision') and
                (user.role=='admin' or assigned_to(db,case_id,user.id,'segunda_revision')))
    can_legal=(stage_for(case)=='juridica' and can(user,'juridica') and
               (user.role=='admin' or assigned_to(db,case_id,user.id,'juridica')))
    return render(request,db,'case_detail.html',case=case,events=events,tasks=tasks,
                  responses=responses,event_page=event_page,event_pages=max(1,math.ceil(n_events/60)),
                  total_events=n_events,sla_color=sla_color,age=_case_age_map(db,[case])[case_id],
                  stage=stage_for(case),control_name=control,stage_assignee=current_assignee,
                  can_followup=is_followup and can(user,'seguimiento'),
                  can_review=can_review,can_second=can_second,can_legal=can_legal,
                  admin=user.role=='admin',controllers=eligible_users(db,'seguimiento'))

def _workflow_page(request,db,case,mode,error=None,status_code=200):
    choices=(list(FOLLOWUP_ACTIONS)+['Registrar novedad de control']
             if mode=='seguimiento' and stage_for(case)=='seguimiento'
             else ['Registrar novedad de control','Cerrar','Devuelta por financiera',
                   'Respuesta de mesa directiva'] if mode=='seguimiento'
             else [x for x in REVIEW_ACTIONS if x!='Enviado a segunda revisión']
                  if mode=='segunda' else
                  list(REVIEW_ACTIONS) if mode=='revisor' else LEGAL_OUTCOMES)
    response=templates.TemplateResponse(request,'workflow.html',ctx(request,db,
        case=case,mode=mode,options=choices,error=error,
        destinations=stage_destination_lists(db)),status_code=status_code)
    return response


def _open_stage_case(db,case_id):
    case=db.get(Case,case_id)
    if not case:raise HTTPException(404)
    if case.needs_reconciliation:
        raise HTTPException(409,'El caso requiere conciliación antes de gestionar.')
    if case.current_status in ARCHIVED_STATUSES:
        return None
    return case


@app.get('/casos/{case_id}/revisor')
def reviewer_form(request:Request,case_id:str,db:Session=Depends(db_session)):
    u=require_permission(request,db,'revision');case=_open_stage_case(db,case_id)
    if case is None:return redirect('/casos/'+quote(case_id))
    require_stage_assignment(db,case,u,'revision')
    return _workflow_page(request,db,case,'revisor')


@app.post('/casos/{case_id}/revisor')
def reviewer_post(request:Request,case_id:str,csrf:str=Form(...),resultado:str=Form(...),
                  observaciones:str=Form(...),destino_usuario_id:int=Form(0),
                  db:Session=Depends(db_session)):
    u=require_permission(request,db,'revision');check_csrf(request,csrf)
    case=_open_stage_case(db,case_id)
    if case is None:raise HTTPException(409,'El expediente ya finalizó.')
    require_stage_assignment(db,case,u,'revision')
    try:
        if resultado=='Enviado a segunda revisión' and destino_usuario_id==u.id:
            raise BusinessError('La segunda revisión debe hacerla otra persona.')
        if resultado not in REVIEW_ACTIONS:raise BusinessError('Resultado de revisión no válido.')
        stage=stage_after_review(resultado)
        if stage in ('revision','segunda_revision','juridica'):
            validate_target(db,destino_usuario_id,stage)
        apply_review(db,case,resultado,observaciones,u.email)
        next_user=transfer_stage(db,case,stage,destino_usuario_id)
        adjust_new_event_owner(db,case)
        audit(db,u.email,'TRASLADO_ETAPA',case_id,
              {'resultado':resultado,'etapa':stage,'destino':next_user.email if next_user else None})
        db.commit()
    except BusinessError as exc:
        db.rollback();return _workflow_page(request,db,case,'revisor',str(exc),400)
    return redirect('/casos/'+quote(case_id))


@app.get('/casos/{case_id}/segunda-revision')
def second_review_form(request:Request,case_id:str,db:Session=Depends(db_session)):
    u=require_permission(request,db,'segunda_revision');case=_open_stage_case(db,case_id)
    if case is None:return redirect('/casos/'+quote(case_id))
    require_stage_assignment(db,case,u,'segunda_revision')
    return _workflow_page(request,db,case,'segunda')


@app.post('/casos/{case_id}/segunda-revision')
def second_review_post(request:Request,case_id:str,csrf:str=Form(...),resultado:str=Form(...),
                       observaciones:str=Form(...),db:Session=Depends(db_session)):
    u=require_permission(request,db,'segunda_revision');check_csrf(request,csrf)
    case=_open_stage_case(db,case_id)
    if case is None:raise HTTPException(409,'El expediente ya finalizó.')
    require_stage_assignment(db,case,u,'segunda_revision')
    try:
        if resultado=='Enviado a segunda revisión':
            raise BusinessError('Esta etapa ya es la segunda revisión.')
        apply_review(db,case,resultado,observaciones,u.email)
        stage=stage_after_review(resultado)
        transfer_stage(db,case,stage)
        adjust_new_event_owner(db,case)
        audit(db,u.email,'TRASLADO_SEGUNDA_REVISION',case_id,
              {'resultado':resultado,'etapa':stage})
        db.commit()
    except BusinessError as exc:
        db.rollback();return _workflow_page(request,db,case,'segunda',str(exc),400)
    return redirect('/casos/'+quote(case_id))


@app.get('/casos/{case_id}/seguimiento')
def follow_form(request:Request,case_id:str,db:Session=Depends(db_session)):
    u=require_permission(request,db,'seguimiento');case=_open_stage_case(db,case_id)
    if case is None:return redirect('/casos/'+quote(case_id))
    followup_access(db,case,u)
    return _workflow_page(request,db,case,'seguimiento')


@app.post('/casos/{case_id}/seguimiento')
def follow_post(request:Request,case_id:str,csrf:str=Form(...),accion:str=Form(...),
                observaciones:str=Form(...),frente_multiple:str=Form('No'),
                frentes:list[str]=Form([]),fecha_cierre:str=Form(''),
                destino_usuario_id:int=Form(0),db:Session=Depends(db_session)):
    u=require_permission(request,db,'seguimiento');check_csrf(request,csrf)
    case=_open_stage_case(db,case_id)
    if case is None:raise HTTPException(409,'El expediente ya finalizó.')
    followup_access(db,case,u)
    if stage_for(case)!='seguimiento' and accion not in (
            'Registrar novedad de control','Cerrar','Devuelta por financiera',
            'Respuesta de mesa directiva'):
        raise HTTPException(409,'El caso está en otra etapa. Registre una novedad de control o un retorno válido.')
    try:
        if not observaciones.strip():
            raise BusinessError('Las observaciones son obligatorias.')
        if accion=='Registrar novedad de control':
            add_event(db,case,'NOVEDAD_CONTROL',u.email,observaciones,
                      role='Control y seguimiento')
            audit(db,u.email,'NOVEDAD_CONTROL',case_id,{'observacion':observaciones})
        else:
            if accion not in FOLLOWUP_ACTIONS:raise BusinessError('Acción no válida.')
            stage=stage_after_followup(accion)
            if stage in ('revision','segunda_revision','juridica'):
                validate_target(db,destino_usuario_id,stage)
            apply_followup(db,case,accion,observaciones,u.email,
                           frente_multiple=='Sí',frentes,fecha_cierre or None)
            next_user=transfer_stage(db,case,stage,destino_usuario_id)
            adjust_new_event_owner(db,case)
            audit(db,u.email,'TRASLADO_ETAPA',case_id,
                  {'accion':accion,'etapa':stage,
                   'destino':next_user.email if next_user else None})
        db.commit()
    except BusinessError as exc:
        db.rollback();return _workflow_page(request,db,case,'seguimiento',str(exc),400)
    return redirect('/casos/'+quote(case_id))


@app.get('/casos/{case_id}/abogados')
def legal_form(request:Request,case_id:str,db:Session=Depends(db_session)):
    u=require_permission(request,db,'juridica');case=_open_stage_case(db,case_id)
    if case is None:return redirect('/casos/'+quote(case_id))
    require_stage_assignment(db,case,u,'juridica')
    return _workflow_page(request,db,case,'abogados')


@app.post('/casos/{case_id}/abogados')
def legal_post(request:Request,case_id:str,csrf:str=Form(...),tipo_revision:str=Form(...),
               proveedor:str=Form(...),resultado:str=Form(...),observaciones:str=Form(...),
               db:Session=Depends(db_session)):
    u=require_permission(request,db,'juridica');check_csrf(request,csrf)
    case=_open_stage_case(db,case_id)
    if case is None:raise HTTPException(409,'El expediente ya finalizó.')
    require_stage_assignment(db,case,u,'juridica')
    try:
        apply_legal(db,case,tipo_revision,proveedor,resultado,observaciones,u.email)
        stage='seguimiento' if resultado=='Aprobado' else 'administracion'
        transfer_stage(db,case,stage)
        adjust_new_event_owner(db,case)
        audit(db,u.email,'TRASLADO_ETAPA',case_id,{'etapa':stage,'resultado':resultado})
        db.commit()
    except BusinessError as exc:
        db.rollback();return _workflow_page(request,db,case,'abogados',str(exc),400)
    return redirect('/casos/'+quote(case_id))


@app.get('/casos/{case_id}/redistribuir')
def admin_redistribute_form(request:Request,case_id:str,db:Session=Depends(db_session)):
    require_user(request,db,['admin'])
    case=_open_stage_case(db,case_id)
    if case is None:return redirect('/casos/'+quote(case_id))
    if stage_for(case)!='administracion':
        raise HTTPException(409,'La cuenta ya no requiere redistribución.')
    control=db.scalar(select(CaseAssignment.user_id).where(
        CaseAssignment.case_id==case_id,CaseAssignment.workflow=='seguimiento').limit(1))
    return render(request,db,'redistribute.html',case=case,
                  destinations=stage_destination_lists(db),control_id=control)


@app.post('/casos/{case_id}/redistribuir')
def admin_redistribute(request:Request,case_id:str,csrf:str=Form(...),
                       destino_usuario_id:int=Form(...),gestion:str=Form(...),
                       db:Session=Depends(db_session)):
    admin=require_user(request,db,['admin']);check_csrf(request,csrf)
    case=_open_stage_case(db,case_id)
    if case is None:raise HTTPException(409,'No se redistribuye un caso cerrado.')
    if stage_for(case)!='administracion':
        raise HTTPException(409,'Este caso no está en la bandeja de redistribución.')
    if gestion not in ('revision','segunda_revision','juridica','seguimiento'):
        raise HTTPException(400,'Etapa de redistribución no válida.')
    try:
        target=validate_target(db,destino_usuario_id,gestion)
        if gestion=='revision':
            case.current_status='EN_PRIMERA_REVISION'
        elif gestion=='segunda_revision':
            case.current_status='ENVIADA_A_SEGUNDA_REVISION'
        elif gestion=='juridica':
            if not (case.requires_legal or case.requires_technical):
                raise BusinessError('El caso no tiene revisión jurídica o técnica habilitada.')
            case.current_status=('EN_FIRMA_REVISORA_JURIDICA' if case.requires_legal else
                                 'EN_FIRMA_REVISORA_TECNICA')
        if gestion=='seguimiento':
            if not assigned_to(db,case.id_caso,target.id,'seguimiento'):
                raise BusinessError('Seleccione el responsable permanente ya registrado. Para cambiarlo, utilice Cambiar titular en el expediente.')
            # No conservar el estado anterior de ajustes: volvería a aparecer
            # indebidamente en la bandeja de devoluciones del administrador.
            case.current_status='RETOMADA_EN_SEGUIMIENTO'
            transfer_stage(db,case,'seguimiento')
        else:
            transfer_stage(db,case,gestion,target.id)
        add_event(db,case,'REDISTRIBUCION_ADMIN',admin.email,
                  f'Reasignado a {target.name} ({gestion})',role='Administración')
        audit(db,admin.email,'REDISTRIBUCION_ADMIN',case_id,
              {'gestion':gestion,'destinatario':target.email})
        db.commit()
    except BusinessError as exc:
        db.rollback();raise HTTPException(400,str(exc))
    return redirect('/casos/'+quote(case_id))


@app.post('/casos/{case_id}/control')
def admin_change_control(request:Request,case_id:str,csrf:str=Form(...),
                         destino_usuario_id:int=Form(...),
                         db:Session=Depends(db_session)):
    admin=require_user(request,db,['admin']);check_csrf(request,csrf)
    case=_open_stage_case(db,case_id)
    if case is None:raise HTTPException(409,'No se asigna control a un expediente cerrado.')
    try:
        target=assign_control(db,case,destino_usuario_id)
        add_event(db,case,'CAMBIO_TITULAR_CONTROL',admin.email,
                  f'Control y seguimiento asignado a {target.name}',role='Administración')
        audit(db,admin.email,'CAMBIO_TITULAR_CONTROL',case_id,{'destinatario':target.email})
        db.commit()
    except BusinessError as exc:
        db.rollback();raise HTTPException(400,str(exc))
    return redirect('/casos/'+quote(case_id))


@app.get('/radicacion')
def rad_contracts(request:Request,q:str='',page:int=1,year:int=2026,db:Session=Depends(db_session)):
    require_permission(request,db,'radicacion')
    query=select(RadContract)
    if q.strip():
        term=f'%{q.strip()[:100]}%'
        query=query.where(or_(RadContract.number.ilike(term),RadContract.contractor.ilike(term)))
    total=db.scalar(select(func.count()).select_from(query.subquery())) or 0
    page=max(1,page)
    records=db.scalars(query.order_by(RadContract.number).limit(15).offset((page-1)*15)).all()
    year=max(2020,min(2100,year))
    ids=[x.id for x in records]
    months=db.scalars(select(RadMonth).where(RadMonth.contract_id.in_(ids),RadMonth.year==year)).all() if ids else []
    month_map={(m.contract_id,m.month):m for m in months}
    return render(request,db,'radicacion.html',records=records,month_map=month_map,
                  q=q,total=total,page=page,year=year,pages=max(1,math.ceil(total/15)))

@app.post('/radicacion/{contract_id}/{month}')
def rad_update(request:Request,contract_id:int,month:int,csrf:str=Form(...),
               year:int=Form(...),filed:str=Form(...),db:Session=Depends(db_session)):
    user=require_permission(request,db,'radicacion');check_csrf(request,csrf)
    if year<2020 or year>2100 or month<1 or month>12 or not db.get(RadContract,contract_id):
        raise HTTPException(400)
    m=db.scalar(select(RadMonth).where(RadMonth.contract_id==contract_id,RadMonth.month==month,RadMonth.year==year))
    if not m:
        m=RadMonth(contract_id=contract_id,month=month,year=year)
        db.add(m)
    m.filed=(filed=='1');m.last_changed_at=utcnow()
    audit(db,user.email,'RADICACION_MES',str(contract_id),{'mes':month,'anio':year,'radicada':m.filed})
    db.commit();return redirect('/radicacion?year='+str(year))

@app.get('/informes')
def reports(request:Request,periodo:str='',responsable:str='',anio:int=0,
            db:Session=Depends(db_session)):
    require_permission(request,db,'informes')
    if anio and not (2020<=anio<=2100):
        raise HTTPException(400,'Anio no valido.')
    try:
        metrics=report_data(db,period=periodo,assignee=responsable,year=anio or None)
    except ValueError as exc:
        raise HTTPException(400,str(exc))
    return render(request,db,'reports.html',**metrics)

@app.get('/informes/casos.csv')
def export_csv(request:Request,db:Session=Depends(db_session)):
    require_permission(request,db,'exportar')
    def safe_cell(value):
        # Evitar interpretación de fórmulas al abrir el CSV con Excel.
        text='' if value is None else str(value)
        if text.lstrip().startswith(('=','+','-','@','\t','\r')):
            return "'"+text
        return text
    def gen():
        s=io.StringIO()
        w=csv.writer(s,delimiter=';')
        yield '\ufeff'
        w.writerow(['ID_CASO','Contratista','Documento','Contrato','Periodo','Estado','Responsable',
                    'Seguimiento','Fecha límite','Valor','Origen'])
        yield s.getvalue();s.seek(0);s.truncate(0)
        with SessionLocal() as stream_db:
            for c in stream_db.scalars(select(Case).order_by(Case.id_caso)).yield_per(250):
                w.writerow([safe_cell(v) for v in [c.id_caso,c.contractor_name,c.document,c.contract_no,c.period,
                           c.current_status,c.current_owner,c.followup_assignee,c.sla_due,c.amount,c.source]])
                yield s.getvalue();s.seek(0);s.truncate(0)
    return StreamingResponse(gen(),media_type='text/csv; charset=utf-8',
                 headers={'Content-Disposition':'attachment; filename="casos_radicacion.csv"',
                          'Cache-Control':'no-store'})

def users_directory(db):
    people=db.scalars(select(User).order_by(User.name)).all()
    alias_map={u.id:[] for u in people}
    for a in db.scalars(select(UserAlias).order_by(UserAlias.value)).all():
        alias_map.setdefault(a.user_id,[]).append(a.value)
    assigned=dict(db.execute(select(CaseAssignment.user_id,func.count(func.distinct(CaseAssignment.case_id)))
             .join(Case,Case.id_caso==CaseAssignment.case_id)
             .where(~Case.current_status.in_(ARCHIVED_STATUSES),Case.needs_reconciliation.is_not(True))
             .group_by(CaseAssignment.user_id)).all())
    return dict(users=people,alias_map=alias_map,assignment_counts=assigned)

@app.get('/usuarios')
def users_page(request:Request,db:Session=Depends(db_session)):
    require_user(request,db,['admin'])
    return render(request,db,'users.html',**users_directory(db),error=None,invitation=None,notice=None)


def render_users(request,db,error=None,invitation=None,notice=None,status_code=200):
    response=templates.TemplateResponse(request,'users.html',ctx(request,db,
             **users_directory(db),error=error,invitation=invitation,notice=notice),status_code=status_code)
    response.headers['Cache-Control']='no-store'
    return response

@app.post('/usuarios')
def create_user(request:Request,csrf:str=Form(...),name:str=Form(...),email:str=Form(...),
                permissions:list[str]=Form([]),aliases:str=Form(''),db:Session=Depends(db_session)):
    admin=require_user(request,db,['admin']);check_csrf(request,csrf)
    email=email.strip().lower();name=name.strip()
    perms=sorted(set(permissions).intersection(PERMISSIONS))
    if not name or len(name)>160 or not email or '@' not in email or len(email)>250:
        return render_users(request,db,error='Digite un nombre y un correo electrónico válidos.',status_code=400)
    if not perms:
        return render_users(request,db,error='Asigne al menos un formulario o herramienta.',status_code=400)
    if db.scalar(select(User.id).where(User.email==email)):
        return render_users(request,db,error='Este correo ya tiene una cuenta. Edite sus permisos en el listado.',status_code=400)
    code=secrets.token_urlsafe(36)
    u=User(name=name,email=email,password_hash=ph.hash(secrets.token_urlsafe(40)),
           role='consulta',active=False,form_permissions=perms,
           invite_token_hash=hashlib.sha256(code.encode()).hexdigest(),
           invite_expires_at=datetime.now(timezone.utc)+timedelta(hours=72))
    try:
        db.add(u);db.flush()
        summary=replace_aliases(db,u,aliases)
        audit(db,admin.email,'INVITAR_USUARIO',email,
              {'permissions':perms,'associations':summary})
        db.commit()
    except (ValueError,IntegrityError) as exc:
        db.rollback()
        return render_users(request,db,error=(str(exc) if isinstance(exc,ValueError) else
                            'No se pudieron registrar las identidades. Revise las coincidencias.'),status_code=400)
    return render_users(request,db,invitation={'email':email,'url':invitation_url(code)},
                        notice=f'Se asociaron {summary["assigned"]} gestiones por identidad. '
                               f'Coincidencias ambiguas sin asignar: {summary["ambiguous"]}.')

@app.post('/usuarios/{uid}/identidad')
def update_user_identity(request:Request,uid:int,csrf:str=Form(...),
                         name:str=Form(...),aliases:str=Form(''),
                         db:Session=Depends(db_session)):
    admin=require_user(request,db,['admin']);check_csrf(request,csrf)
    person=db.get(User,uid)
    if not person:raise HTTPException(404)
    name=' '.join(name.split())
    if len(name)<2 or len(name)>160:
        return render_users(request,db,error='Ingrese un nombre de 2 a 160 caracteres.',status_code=400)
    before=person.name
    try:
        person.name=name
        summary=replace_aliases(db,person,aliases)
        audit(db,admin.email,'IDENTIDAD_USUARIO',person.email,
              {'antes':before,'despues':name,'aliases':parse_aliases(aliases),'asociaciones':summary})
        db.commit()
    except (ValueError,IntegrityError) as exc:
        db.rollback()
        return render_users(request,db,error=str(exc) if isinstance(exc,ValueError)
                            else 'Los nombres alternativos entran en conflicto.',status_code=400)
    return render_users(request,db,notice=f'Identidad actualizada. {summary["assigned"]} gestiones vinculadas; '
                        f'{summary["ambiguous"]} coincidencias requieren revisión.')

@app.post('/usuarios/{uid}/asignar')
def assign_case_to_person(request:Request,uid:int,csrf:str=Form(...),case_id:str=Form(...),
                          workflow:str=Form(...),db:Session=Depends(db_session)):
    admin=require_user(request,db,['admin']);check_csrf(request,csrf)
    person=db.get(User,uid)
    case_id=case_id.strip()
    if not person:raise HTTPException(404)
    if workflow not in WORKFLOWS or (person.role != 'admin' and workflow not in (person.form_permissions or [])):
        return render_users(request,db,error='Esta persona no tiene permiso para ese formulario.',status_code=400)
    case=db.get(Case,case_id)
    if not case:
        return render_users(request,db,error='No existe un expediente con ese identificador.',status_code=400)
    if case.needs_reconciliation or case.current_status in ARCHIVED_STATUSES:
        return render_users(request,db,error='El expediente debe estar activo y conciliado para asignarlo.',status_code=400)
    previous=db.scalars(select(CaseAssignment).where(
        CaseAssignment.case_id==case_id,CaseAssignment.workflow==workflow)).all()
    for link in previous:db.delete(link)
    db.flush()
    db.add(CaseAssignment(case_id=case_id,user_id=person.id,workflow=workflow,
                          source='manual',matched_text='Asignación confirmada por administrador'))
    audit(db,admin.email,'ASIGNACION_MANUAL',case_id,
          {'usuario':person.email,'formulario':workflow,'sustituidos':[a.user_id for a in previous]})
    db.commit()
    return render_users(request,db,notice=f'{case_id} asignado a {person.name} en {workflow}.')

@app.post('/usuarios/{uid}/permisos')
def update_permissions(request:Request,uid:int,csrf:str=Form(...),permissions:list[str]=Form([]),
                       db:Session=Depends(db_session)):
    admin=require_user(request,db,['admin']);check_csrf(request,csrf)
    target=db.get(User,uid)
    if not target:raise HTTPException(404)
    if target.role=='admin':raise HTTPException(400,'No es posible modificar permisos del administrador.')
    perms=sorted(set(permissions).intersection(PERMISSIONS))
    if not perms:return render_users(request,db,error='Debe conservar al menos una herramienta.',status_code=400)
    old=list(user_permissions(target))
    target.form_permissions=perms
    audit(db,admin.email,'CAMBIO_PERMISOS',target.email,{'antes':old,'despues':perms})
    db.commit()
    return redirect('/usuarios')

@app.post('/usuarios/{uid}/invitacion')
def reinvite(request:Request,uid:int,csrf:str=Form(...),db:Session=Depends(db_session)):
    admin=require_user(request,db,['admin']);check_csrf(request,csrf)
    target=db.get(User,uid)
    if not target:raise HTTPException(404)
    if target.role=='admin':raise HTTPException(400,'No se pueden emitir invitaciones para administradores.')
    code=secrets.token_urlsafe(36)
    target.password_hash=ph.hash(secrets.token_urlsafe(40))
    target.active=False
    target.invite_token_hash=hashlib.sha256(code.encode()).hexdigest()
    target.invite_expires_at=datetime.now(timezone.utc)+timedelta(hours=72)
    audit(db,admin.email,'REINVITAR_USUARIO',target.email)
    db.commit()
    return render_users(request,db,invitation={'email':target.email,'url':invitation_url(code)})

@app.post('/usuarios/{uid}/eliminar')
def delete_user_account(request:Request,uid:int,csrf:str=Form(...),
                        confirm_email:str=Form(...),db:Session=Depends(db_session)):
    """Elimina una cuenta sin borrar casos ni movimientos históricos.

    Requiere confirmación del correo, rol administrador y token CSRF. Se
    conservan las novedades históricas; solo desaparecen las asociaciones
    y los alias que daban acceso a la bandeja de la persona eliminada.
    """
    admin=require_user(request,db,['admin']);check_csrf(request,csrf)
    target=db.get(User,uid)
    if not target:
        raise HTTPException(404,'El usuario no existe o ya fue eliminado.')
    if target.id==admin.id or target.role=='admin':
        return render_users(request,db,error='No se permite eliminar cuentas administradoras.',status_code=400)
    if confirm_email.strip().casefold()!=target.email.casefold():
        return render_users(request,db,error='El correo de confirmación no coincide con el usuario seleccionado.',status_code=400)

    # Copiar detalles antes de eliminar la fila; los casos y eventos nunca
    # se eliminan desde la administración de usuarios.
    identity={'id':target.id,'name':target.name,'email':target.email,
              'role':target.role,'active':target.active,
              'permissions':list(target.form_permissions or [])}
    assigned=db.scalar(select(func.count()).select_from(CaseAssignment)
                       .where(CaseAssignment.user_id==uid)) or 0
    aliases=db.scalar(select(func.count()).select_from(UserAlias)
                      .where(UserAlias.user_id==uid)) or 0
    try:
        db.execute(delete(CaseAssignment).where(CaseAssignment.user_id==uid))
        db.execute(delete(UserAlias).where(UserAlias.user_id==uid))
        db.delete(target)
        db.flush()
        audit(db,admin.email,'ELIMINAR_USUARIO',identity['email'],
              {'cuenta':identity,'vinculos_retirados':assigned,
               'alias_retirados':aliases,'casos_eliminados':0})
        db.commit()
    except IntegrityError:
        db.rollback()
        return render_users(request,db,error='No se pudo eliminar la cuenta. Verifique sus relaciones.',status_code=409)
    return redirect('/usuarios?eliminado=1')

@app.post('/usuarios/{uid}/estado')
def toggle_user(request:Request,uid:int,csrf:str=Form(...),db:Session=Depends(db_session)):
    admin=require_user(request,db,['admin']);check_csrf(request,csrf)
    target=db.get(User,uid)
    if not target:raise HTTPException(404)
    if target.id==admin.id or target.role=='admin':raise HTTPException(400,'No puede desactivar a un administrador.')
    # Un usuario invitado no se activa sin establecer su propia contraseña.
    if target.invite_token_hash and not target.active:
        return render_users(request,db,error='El usuario debe aceptar primero su invitación.',status_code=400)
    target.active=not target.active
    audit(db,admin.email,'CAMBIAR_USUARIO',target.email,{'active':target.active})
    db.commit();return redirect('/usuarios')

@app.get('/auditoria')
def audits_page(request:Request,page:int=1,db:Session=Depends(db_session)):
    require_user(request,db,['admin'])
    page=max(1,page)
    recent=db.scalars(select(Audit).order_by(Audit.id.desc()).limit(40).offset((page-1)*40)).all()
    return render(request,db,'audits.html',recent=recent,page=page)

@app.get('/perfil')
def profile(request:Request,db:Session=Depends(db_session)):
    require_user(request,db)
    return render(request,db,'profile.html',message=None,error=None)

@app.post('/perfil')
def change_password(request:Request,csrf:str=Form(...),old_password:str=Form(...),
                    new_password:str=Form(...),confirm_password:str=Form(...),
                    db:Session=Depends(db_session)):
    user=require_user(request,db);check_csrf(request,csrf)
    valid=False
    try:valid=ph.verify(user.password_hash,old_password)
    except (argon_exc.VerifyMismatchError,argon_exc.InvalidHashError,argon_exc.VerificationError):pass
    if not valid or len(new_password)<12 or new_password!=confirm_password:
        return templates.TemplateResponse(request,'profile.html',ctx(request,db,message=None,
            error='Verifique la contraseña actual, que las nuevas coincidan y tengan al menos 12 caracteres.'),status_code=400)
    user.password_hash=ph.hash(new_password)
    audit(db,user.email,'CAMBIO_PASSWORD',str(user.id))
    db.commit()
    return render(request,db,'profile.html',message='Contraseña actualizada.',error=None)

@app.get('/festivos')
def holidays_page(request:Request,db:Session=Depends(db_session)):
    from .models import Holiday
    require_user(request,db,['admin'])
    holidays=db.scalars(select(Holiday).order_by(Holiday.day.asc())).all()
    return render(request,db,'holidays.html',holidays=holidays,error=None)

@app.post('/festivos')
def holidays_post(request:Request,csrf:str=Form(...),fecha:str=Form(...),
                  descripcion:str=Form(''),db:Session=Depends(db_session)):
    from .models import Holiday
    admin=require_user(request,db,['admin']);check_csrf(request,csrf)
    try:d=date.fromisoformat(fecha)
    except ValueError:raise HTTPException(400,'Fecha inválida.')
    if not db.get(Holiday,d):
        db.add(Holiday(day=d,name=descripcion.strip()[:120]))
        audit(db,admin.email,'AGREGAR_FESTIVO',fecha,{'descripcion':descripcion[:120]})
        db.commit()
    return redirect('/festivos')

@app.post('/festivos/{fecha}/eliminar')
def holidays_delete(request:Request,fecha:str,csrf:str=Form(...),db:Session=Depends(db_session)):
    from .models import Holiday
    admin=require_user(request,db,['admin']);check_csrf(request,csrf)
    try:d=date.fromisoformat(fecha)
    except ValueError:raise HTTPException(400)
    rec=db.get(Holiday,d)
    if rec:db.delete(rec);audit(db,admin.email,'ELIMINAR_FESTIVO',fecha);db.commit()
    return redirect('/festivos')
