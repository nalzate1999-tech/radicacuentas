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
from sqlalchemy import select, func, or_, desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import get_settings
from .database import db_session, engine, SessionLocal
from .models import (Base, User, Case, Event, Subtask, RadContract, RadMonth,
                     ImportBatch, ImportIssue, Personnel, LegacyLog, FormResponse, Audit, Notification, AuthAttempt)
from .services import (BOGOTA, FOLLOWUP_ACTIONS, REVIEW_ACTIONS, LEGAL_OUTCOMES,
                       REVIEWERS, PROVIDERS, SUBTASK_TYPES, BusinessError, create_case,
                       apply_followup, apply_review, apply_legal, sla_color, audit, utcnow)
from .importer import migrate_workbook

settings=get_settings()
app=FastAPI(title='Radicación y seguimiento de cuentas',docs_url=None,redoc_url=None,openapi_url=None)
app.add_middleware(SessionMiddleware,secret_key=settings.secret,https_only=settings.session_https,
                   same_site='strict',session_cookie='rad_session',max_age=60*60*8)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=[x.strip() for x in settings.allowed_hosts.split(',')])
APP_DIR=Path(__file__).resolve().parent
app.mount('/static',StaticFiles(directory=str(APP_DIR/'static')),name='static')
templates=Jinja2Templates(directory=str(APP_DIR/'templates'))
ph=PasswordHasher(time_cost=3,memory_cost=65536,parallelism=4)
ROLES={'admin':'Administrador','seguimiento':'Seguimiento','revisor':'Revisor','abogados':'Firmas jurídica/técnica','consulta':'Consulta'}

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
    return dict(request=request,user=u,csrf=request.session['csrf'],roles=ROLES,**extra)

def render(request,db,template,**extra):
    return templates.TemplateResponse(request,template,ctx(request,db,**extra))

def redirect(path):return RedirectResponse(path,status_code=303)

@app.get('/')
def index(request:Request):
    return redirect('/panel' if request.session.get('uid') else '/ingresar')

@app.get('/ingresar')
def login_page(request:Request,db:Session=Depends(db_session)):
    if current_user(request,db):return redirect('/panel')
    return render(request,db,'login.html',error=None)

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
                error='Demasiados intentos. Vuelva a intentar después de 15 minutos.'),status_code=429)
    user=db.scalar(select(User).where(User.email==email))
    valid=False
    if user and user.active:
        try:valid=ph.verify(user.password_hash,password)
        except (argon_exc.VerifyMismatchError,argon_exc.InvalidHashError,argon_exc.VerificationError):pass
    db.add(AuthAttempt(email_hash=token_hash,success=valid));db.commit()
    if not valid:
        return templates.TemplateResponse(request,'login.html',ctx(request,db,error='Credenciales incorrectas.'),status_code=401)
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
    total=db.scalar(select(func.count()).select_from(Case)) or 0
    closed=db.scalar(select(func.count()).select_from(Case).where(Case.current_status.in_(['CERRADA','PAGADA']))) or 0
    now=datetime.now(BOGOTA).date()
    overdue=db.scalar(select(func.count()).select_from(Case).where(Case.sla_due<now,~Case.current_status.in_(['CERRADA','PAGADA','EN_MESA_DIRECTIVA','ENVIADA_A_FINANCIERA']))) or 0
    events=db.scalar(select(func.count()).select_from(Event)) or 0
    by_status=db.execute(select(Case.current_status,func.count(Case.id_caso)).group_by(Case.current_status).order_by(desc(func.count(Case.id_caso))).limit(12)).all()
    recent=db.scalars(select(Case).order_by(desc(Case.created_at),desc(Case.id_caso)).limit(12)).all()
    return render(request,db,'dashboard.html',total=total,closed=closed,overdue=overdue,
                  movements=events,by_status=by_status,recent=recent,sla_color=sla_color)

@app.get('/casos')
def cases(request:Request,q:str='',estado:str='',periodo:str='',page:int=1,db:Session=Depends(db_session)):
    require_user(request,db)
    query=select(Case)
    if q.strip():
        term=f'%{q.strip()[:100]}%'
        query=query.where(or_(Case.id_caso.ilike(term),Case.contractor_name.ilike(term),
                              Case.contract_no.ilike(term),Case.document.ilike(term)))
    if estado.strip():query=query.where(Case.current_status==estado)
    if periodo.strip():query=query.where(Case.period==periodo)
    total=db.scalar(select(func.count()).select_from(query.subquery())) or 0
    page=max(1,page);page_size=30
    rows=db.scalars(query.order_by(desc(Case.registered_at),desc(Case.id_caso))
                    .limit(page_size).offset((page-1)*page_size)).all()
    statuses=db.scalars(select(Case.current_status).distinct().order_by(Case.current_status)).all()
    return render(request,db,'cases.html',rows=rows,total=total,page=page,pages=max(1,math.ceil(total/page_size)),
                  q=q,estado=estado,periodo=periodo,statuses=statuses,sla_color=sla_color)

@app.get('/casos/nuevo')
def register_form(request:Request,db:Session=Depends(db_session)):
    require_user(request,db,['seguimiento'])
    personnel=db.scalars(select(Personnel).order_by(Personnel.name)).all()
    names=sorted(set([p.name for p in personnel]+['Brayan']))
    return render(request,db,'register.html',assignees=names,reviewers=REVIEWERS,error=None,form={})

@app.post('/casos/nuevo')
def register(request:Request,csrf:str=Form(...),contractor_type:str=Form(...),
             contractor_name:str=Form(...),document:str=Form(...),contract_no:str=Form(...),
             period:str=Form(...),amount:str=Form(...),contractor_email:str=Form(...),
             contractor_phone:str=Form(''),followup_assignee:str=Form(...),
             reviewer:list[str]=Form([]),special:str=Form('No'),sent_by:str=Form(...),
             requires_reviewer:str=Form('No'),requires_technical:str=Form('No'),
             requires_legal:str=Form('No'),db:Session=Depends(db_session)):
    u=require_user(request,db,['seguimiento']);check_csrf(request,csrf)
    data=locals().copy()
    data['reviewer']=', '.join(reviewer)
    try:
        case=create_case(db,data,u.email)
        db.commit()
        return redirect('/casos/'+quote(case.id_caso))
    except (BusinessError,IntegrityError) as e:
        db.rollback()
        personnel=db.scalars(select(Personnel).order_by(Personnel.name)).all()
        return templates.TemplateResponse(request,'register.html',ctx(request,db,
            assignees=sorted(set(p.name for p in personnel)|{'Brayan'}),reviewers=REVIEWERS,
            error=str(e) if isinstance(e,BusinessError) else 'Registro duplicado o error de integridad.',
            form=data),status_code=400)

@app.get('/casos/{case_id}')
def case_detail(request:Request,case_id:str,event_page:int=1,db:Session=Depends(db_session)):
    require_user(request,db)
    case=db.get(Case,case_id)
    if not case:raise HTTPException(404,'Caso no encontrado')
    event_page=max(1,event_page)
    events=db.scalars(select(Event).where(Event.case_id==case_id)
                      .order_by(Event.occurred_at.desc(),Event.id.desc())
                      .limit(60).offset((event_page-1)*60)).all()
    n_events=db.scalar(select(func.count()).select_from(Event).where(Event.case_id==case_id)) or 0
    tasks=db.scalars(select(Subtask).where(Subtask.case_id==case_id).order_by(Subtask.kind)).all()
    responses=db.scalars(select(FormResponse).where(FormResponse.case_id==case_id)
                         .order_by(FormResponse.submitted_at.desc()).limit(15)).all()
    return render(request,db,'case_detail.html',case=case,events=events,tasks=tasks,
                  responses=responses,event_page=event_page,event_pages=max(1,math.ceil(n_events/60)),
                  total_events=n_events,sla_color=sla_color)

@app.get('/casos/{case_id}/revisor')
def reviewer_form(request:Request,case_id:str,db:Session=Depends(db_session)):
    require_user(request,db,['revisor']);case=db.get(Case,case_id)
    if not case:raise HTTPException(404)
    return render(request,db,'workflow.html',case=case,mode='revisor',options=list(REVIEW_ACTIONS),error=None)

@app.post('/casos/{case_id}/revisor')
def reviewer_post(request:Request,case_id:str,csrf:str=Form(...),resultado:str=Form(...),
                  observaciones:str=Form(...),db:Session=Depends(db_session)):
    u=require_user(request,db,['revisor']);check_csrf(request,csrf)
    case=db.get(Case,case_id)
    if not case:raise HTTPException(404)
    try:apply_review(db,case,resultado,observaciones,u.email);db.commit()
    except BusinessError as exc:
        db.rollback();return templates.TemplateResponse(request,'workflow.html',ctx(request,db,
           case=case,mode='revisor',options=list(REVIEW_ACTIONS),error=str(exc)),status_code=400)
    return redirect('/casos/'+quote(case_id))

@app.get('/casos/{case_id}/seguimiento')
def follow_form(request:Request,case_id:str,db:Session=Depends(db_session)):
    require_user(request,db,['seguimiento']);case=db.get(Case,case_id)
    if not case:raise HTTPException(404)
    return render(request,db,'workflow.html',case=case,mode='seguimiento',options=list(FOLLOWUP_ACTIONS),error=None)

@app.post('/casos/{case_id}/seguimiento')
def follow_post(request:Request,case_id:str,csrf:str=Form(...),accion:str=Form(...),
                observaciones:str=Form(...),frente_multiple:str=Form('No'),frentes:list[str]=Form([]),
                fecha_cierre:str=Form(''),db:Session=Depends(db_session)):
    u=require_user(request,db,['seguimiento']);check_csrf(request,csrf)
    case=db.get(Case,case_id)
    if not case:raise HTTPException(404)
    try:
        apply_followup(db,case,accion,observaciones,u.email,frente_multiple=='Sí',frentes,fecha_cierre or None)
        db.commit()
    except BusinessError as exc:
        db.rollback();return templates.TemplateResponse(request,'workflow.html',ctx(request,db,
           case=case,mode='seguimiento',options=list(FOLLOWUP_ACTIONS),error=str(exc)),status_code=400)
    return redirect('/casos/'+quote(case_id))

@app.get('/casos/{case_id}/abogados')
def legal_form(request:Request,case_id:str,db:Session=Depends(db_session)):
    require_user(request,db,['abogados']);case=db.get(Case,case_id)
    if not case:raise HTTPException(404)
    return render(request,db,'workflow.html',case=case,mode='abogados',options=LEGAL_OUTCOMES,error=None)

@app.post('/casos/{case_id}/abogados')
def legal_post(request:Request,case_id:str,csrf:str=Form(...),tipo_revision:str=Form(...),
               proveedor:str=Form(...),resultado:str=Form(...),observaciones:str=Form(...),
               db:Session=Depends(db_session)):
    u=require_user(request,db,['abogados']);check_csrf(request,csrf)
    case=db.get(Case,case_id)
    if not case:raise HTTPException(404)
    try:apply_legal(db,case,tipo_revision,proveedor,resultado,observaciones,u.email);db.commit()
    except BusinessError as exc:
        db.rollback();return templates.TemplateResponse(request,'workflow.html',ctx(request,db,
           case=case,mode='abogados',options=LEGAL_OUTCOMES,error=str(exc)),status_code=400)
    return redirect('/casos/'+quote(case_id))

@app.get('/radicacion')
def rad_contracts(request:Request,q:str='',page:int=1,year:int=2026,db:Session=Depends(db_session)):
    require_user(request,db)
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
    user=require_user(request,db,['seguimiento']);check_csrf(request,csrf)
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
def reports(request:Request,db:Session=Depends(db_session)):
    require_user(request,db)
    by_owner=db.execute(select(Case.current_owner,func.count(Case.id_caso))
                        .group_by(Case.current_owner).order_by(desc(func.count(Case.id_caso)))).all()
    by_assignee=db.execute(select(Case.followup_assignee,func.count(Case.id_caso))
                           .group_by(Case.followup_assignee).order_by(desc(func.count(Case.id_caso))).limit(30)).all()
    total=db.scalar(select(func.count(Case.id_caso))) or 0
    events=db.scalar(select(func.count(Event.id))) or 0
    return render(request,db,'reports.html',by_owner=by_owner,by_assignee=by_assignee,total=total,events=events)

@app.get('/informes/casos.csv')
def export_csv(request:Request,db:Session=Depends(db_session)):
    require_user(request,db)
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

@app.get('/administracion')
def administration(request:Request,page:int=1,db:Session=Depends(db_session)):
    require_user(request,db,['admin'])
    batches=db.scalars(select(ImportBatch).order_by(ImportBatch.imported_at.desc()).limit(10)).all()
    issues=db.scalars(select(ImportIssue).order_by(ImportIssue.id.desc()).limit(60).offset((max(1,page)-1)*60)).all()
    total_issues=db.scalar(select(func.count()).select_from(ImportIssue)) or 0
    return render(request,db,'administration.html',batches=batches,issues=issues,
                  page=max(1,page),total_issues=total_issues,error=None)

@app.post('/administracion/importar')
def admin_import(request:Request,csrf:str=Form(...),archivo:UploadFile=File(...),db:Session=Depends(db_session)):
    require_user(request,db,['admin']);check_csrf(request,csrf)
    if not archivo.filename or not archivo.filename.lower().endswith('.xlsx'):
        raise HTTPException(400,'Cargue un archivo .xlsx')
    max_size=35*1024*1024
    with tempfile.NamedTemporaryFile(suffix='.xlsx',delete=False) as tmp:
        path=tmp.name;total=0
        while True:
            chunk=archivo.file.read(1024*1024)
            if not chunk:break
            total+=len(chunk)
            if total>max_size:
                tmp.close();Path(path).unlink(missing_ok=True)
                raise HTTPException(413,'Archivo superior a 35 MB: use importación por línea de comandos.')
            tmp.write(chunk)
    try:
        migrate_workbook(path)
        return redirect('/administracion')
    except (ValueError,Exception) as exc:
        # No exponer stacktrace ni rutas de infraestructura en el navegador.
        return templates.TemplateResponse(request,'administration.html',ctx(request,db,
            batches=db.scalars(select(ImportBatch).order_by(ImportBatch.imported_at.desc()).limit(10)).all(),
            issues=[],page=1,total_issues=0,error='No se completó la importación: '+str(exc)[:220]),status_code=400)
    finally:Path(path).unlink(missing_ok=True)

@app.get('/usuarios')
def users_page(request:Request,db:Session=Depends(db_session)):
    require_user(request,db,['admin'])
    users=db.scalars(select(User).order_by(User.name)).all()
    return render(request,db,'users.html',users=users,error=None)

@app.post('/usuarios')
def create_user(request:Request,csrf:str=Form(...),name:str=Form(...),email:str=Form(...),
                password:str=Form(...),role:str=Form(...),db:Session=Depends(db_session)):
    admin=require_user(request,db,['admin']);check_csrf(request,csrf)
    if role not in ROLES or len(password)<12 or '@' not in email:
        error='Rol inválido, correo inválido o contraseña menor de 12 caracteres.'
    elif db.scalar(select(User.id).where(User.email==email.lower().strip())):
        error='Ya existe un usuario con ese correo.'
    else:
        db.add(User(name=name.strip(),email=email.strip().lower(),password_hash=ph.hash(password),role=role))
        audit(db,admin.email,'CREAR_USUARIO',email.lower(),{'role':role})
        db.commit();return redirect('/usuarios')
    return templates.TemplateResponse(request,'users.html',ctx(request,db,
        users=db.scalars(select(User).order_by(User.name)).all(),error=error),status_code=400)

@app.post('/usuarios/{uid}/estado')
def toggle_user(request:Request,uid:int,csrf:str=Form(...),db:Session=Depends(db_session)):
    admin=require_user(request,db,['admin']);check_csrf(request,csrf)
    user=db.get(User,uid)
    if not user:raise HTTPException(404)
    if user.id==admin.id:raise HTTPException(400,'No puede desactivarse a sí mismo.')
    user.active=not user.active
    audit(db,admin.email,'CAMBIAR_USUARIO',user.email,{'active':user.active})
    db.commit();return redirect('/usuarios')

@app.get('/auditoria')
def audits_page(request:Request,page:int=1,db:Session=Depends(db_session)):
    require_user(request,db,['admin'])
    page=max(1,page)
    recent=db.scalars(select(Audit).order_by(Audit.id.desc()).limit(40).offset((page-1)*40)).all()
    logs=db.scalars(select(LegacyLog).order_by(LegacyLog.id.desc()).limit(40).offset((page-1)*40)).all()
    return render(request,db,'audits.html',recent=recent,logs=logs,page=page)

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

@app.post('/usuarios/{uid}/rol')
def change_role(request:Request,uid:int,csrf:str=Form(...),role:str=Form(...),db:Session=Depends(db_session)):
    admin=require_user(request,db,['admin']);check_csrf(request,csrf)
    target=db.get(User,uid)
    if not target:raise HTTPException(404)
    if target.id==admin.id or role not in ROLES:
        raise HTTPException(400,'No puede cambiar su propio rol o usar un rol desconocido.')
    old=target.role
    target.role=role
    audit(db,admin.email,'CAMBIO_ROL',target.email,{'antes':old,'despues':role})
    db.commit()
    return redirect('/usuarios')

@app.get('/festivos')
def holidays_page(request:Request,db:Session=Depends(db_session)):
    from .models import Holiday
    require_user(request,db,['admin'])
    holidays=db.scalars(select(Holiday).order_by(Holiday.day.desc()).limit(90)).all()
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
