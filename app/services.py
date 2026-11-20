"""Reglas del proceso: las transiciones usan las opciones de los cuatro formularios aportados."""
import hashlib
import re
import secrets
import unicodedata
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .models import Case, Event, Subtask, Audit, Holiday, Notification, utcnow, RadContract, RadMonth, Personnel

BOGOTA = ZoneInfo('America/Bogota')
STOP_STATUSES = {'ENVIADA_A_FINANCIERA', 'EN_MESA_DIRECTIVA', 'PAGADA', 'CERRADA'}
FOLLOWUP_ACTIONS = {
    'Enviado a revisor': ('EN_PRIMERA_REVISION', 'Revisor', 'ENVIADO_A_REVISOR'),
    'Enviado a Supervisor / Contratista': ('EN_AJUSTES_CONTRATISTA_SUPERVISOR','Supervisor_Contratista','ENVIADO_A_SUPERVISOR_CONTRATISTA'),
    'Enviado a firma revisora jurídica': ('EN_FIRMA_REVISORA_JURIDICA','Firma Jurídica','ENVIADO_A_FIRMA_REVISORA_JURIDICA'),
    'Enviado a firma revisora técnica': ('EN_FIRMA_REVISORA_TECNICA','Firma Técnica','ENVIADO_A_FIRMA_REVISORA_TECNICA'),
    'Enviado a segunda revisión': ('ENVIADA_A_SEGUNDA_REVISION','Revisor','ENVIADO_A_SEGUNDA_REVISION'),
    'Enviado para firma Directora': ('EN_FIRMA_DIRECTORA','DIRECTORA','ENVIADO_PARA_FIRMA_DIRECTORA'),
    'Devuelto a mesa directiva': ('EN_MESA_DIRECTIVA','Mesa Directiva','ENVIADO_A_MESA_DIRECTIVA'),
    'Enviado financiera': ('ENVIADA_A_FINANCIERA','Financiera','ENVIADO_A_FINANCIERA'),
    'Respuesta de mesa directiva': ('DEVUELTA_POR_MESA_DIRECTIVA','Seguimiento','DEVUELTA_POR_MESA_DIRECTIVA'),
    'Devuelta por financiera': ('DEVUELTA_POR_FINANCIERA','Seguimiento','DEVUELTA_POR_FINANCIERA'),
    'Cerrar': ('CERRADA','Seguimiento','CERRADA'),
}
REVIEW_ACTIONS = {
    'Requiere ajustes': ('EN_AJUSTES_CONTRATISTA_SUPERVISOR','Supervisor_Contratista','REQUIERE_AJUSTES_REVISOR'),
    'Enviado a segunda revisión': ('ENVIADA_A_SEGUNDA_REVISION','Revisor','ENVIADO_A_SEGUNDA_REVISION'),
    'Enviado a firma Directora': ('EN_FIRMA_DIRECTORA','DIRECTORA','ENVIADO_PARA_FIRMA_DIRECTORA'),
    'Enviado a financiera': ('ENVIADA_A_FINANCIERA','Financiera','ENVIADO_A_FINANCIERA'),
}
LEGAL_OUTCOMES = ['Aprobado','Requiere ajustes']
SUBTASK_TYPES = ['REVISOR','TECNICA','JURIDICA']
REVIEWERS = ['Hector','Oscar Nuñez','Nancy Alcala','Andrea','Soluciones Territoriales',
             'Juan Carlos','Elsa Peñaloza','Brayan Leon','Quintero Rueda Abogados']
PROVIDERS = {'Técnica':'Soluciones Territoriales','Jurídica':'Velázquez Osorio'}

class BusinessError(Exception):
    pass

def clean_text(value):
    return str(value or '').strip()

def normalize(value):
    text=unicodedata.normalize('NFKD', clean_text(value))
    return re.sub(r'\s+', ' ', ''.join(c for c in text if not unicodedata.combining(c))).casefold()

def is_yes(value):
    return normalize(value) in ('si','sí','true','1','yes','verdadero') or value is True

def parse_period(value):
    s=clean_text(value)
    if re.fullmatch(r'20\d{2}(0[1-9]|1[0-2])',s): return s
    if re.fullmatch(r'20\d{2}[-/](0?[1-9]|1[0-2])',s):
        y,m=re.split(r'[-/]',s);return f'{y}{int(m):02d}'
    if s.endswith('.0') and re.fullmatch(r'20\d{4}\.0',s): return s[:-2]
    return None

def parse_money(value):
    if value is None or clean_text(value)=='':return None
    if isinstance(value,(int,float,Decimal)):
        return Decimal(str(value)).quantize(Decimal('0.01'))
    s=clean_text(value).replace('$','').replace(' ','')
    if ',' in s and '.' in s:
        s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
    elif ',' in s:
        s=s.replace(',','.')
    try:return Decimal(s).quantize(Decimal('0.01'))
    except InvalidOperation:return None

def sla_deadline(db, received_at, special=False):
    """3/8 días hábiles; fecha de recepción cuenta si llegó entre las 08:00 y 15:00."""
    dt=received_at if received_at.tzinfo else received_at.replace(tzinfo=BOGOTA)
    local=dt.astimezone(BOGOTA)
    days=8 if special else 3
    holidays={x[0] for x in db.execute(select(Holiday.day)).all()}
    def business(d): return d.weekday()<5 and d not in holidays
    count=1 if business(local.date()) and 8<=local.hour<15 else 0
    if local.hour==15 and local.minute==0 and business(local.date()):count=1
    cur=local.date()
    while count<days:
        cur+=timedelta(days=1)
        if business(cur):count+=1
    return cur

def sla_color(case, today=None):
    if not case.sla_due or case.current_status in STOP_STATUSES:return 'Sin SLA'
    today=today or datetime.now(BOGOTA).date()
    if case.sla_due<today:return 'Vencido'
    if (case.sla_due-today).days<=1:return 'Por vencer'
    return 'OK'

def add_event(db,case,kind,actor,observations='',role='',status=None,owner=None,task_id=None):
    now=utcnow()
    e=Event(case_id=case.id_caso,raw_case_id=case.id_caso,task_id=task_id,
            occurred_at=now, kind=kind,actor_name=actor,actor_role=role,
            observations=observations,resulting_status=status or case.current_status,
            resulting_owner=owner or case.current_owner,source='web')
    db.add(e)
    case.last_event_at=now
    case.last_observation=observations or kind
    return e

def change_status(db,case,status,owner):
    case.current_status=status
    case.current_owner=owner
    case.sla_due = None if status in STOP_STATUSES else sla_deadline(db,utcnow(),case.special)

def queue_notification(db,case_id,to_email,kind,subject,body):
    if not to_email or '@' not in to_email:return
    db.add(Notification(dedupe_key=f'{case_id}:{kind}:{secrets.token_hex(8)}',case_id=case_id,
                        to_email=to_email, subject=subject, body=body))

def audit(db,user,action,target,detail=None):
    db.add(Audit(actor=user,action=action,target=target,detail=detail or {}))

def create_case(db,data,actor):
    required=['contractor_type','contractor_name','document','contract_no','period',
              'contractor_email','followup_assignee','sent_by']
    missing=[x for x in required if not clean_text(data.get(x))]
    if missing:raise BusinessError('Faltan campos obligatorios: '+', '.join(missing))
    period=parse_period(data['period'])
    if not period:raise BusinessError('El período debe tener formato AAAAMM, por ejemplo 202611.')
    if '@' not in data['contractor_email']:
        raise BusinessError('El correo del contratista no es válido.')
    amount=parse_money(data.get('amount'))
    if amount is None or amount<0:
        raise BusinessError('Indique un valor a pagar válido, igual o superior a cero.')
    key=hashlib.sha256('|'.join([normalize(data['contract_no']),period,normalize(data['document'])]).encode()).hexdigest()
    existing=db.scalars(select(Case).where(Case.dedupe_key==key)).first()
    if existing:raise BusinessError(f'La cuenta ya se registró con el ID {existing.id_caso}.')
    # Las filas importadas no tienen dedupe_key histórico: comparar identidad real,
    # sin modificar ni renombrar los expedientes ya migrados.
    source_contract=normalize(data['contract_no'])
    source_document=normalize(data['document'])
    for prior in db.scalars(select(Case).where(Case.period==period)).all():
        if (normalize(prior.contract_no)==source_contract and
            normalize(prior.document)==source_document):
            raise BusinessError(f'Ya existe una cuenta para este contrato, documento y período: {prior.id_caso}.')
    now=utcnow(); local=now.astimezone(BOGOTA)
    case_id=f'CC-{local:%Y%m%d}-{secrets.token_hex(5).upper()}'
    case=Case(id_caso=case_id,source='web',dedupe_key=key,received_at=now,registered_at=now,
      contractor_type=clean_text(data['contractor_type']), contractor_name=clean_text(data['contractor_name']),
      document=clean_text(data['document']), contract_no=clean_text(data['contract_no']), period=period,
      amount=amount, contractor_email=clean_text(data['contractor_email']).lower(),
      contractor_phone=clean_text(data.get('contractor_phone')),
      followup_assignee=clean_text(data['followup_assignee']),
      reviewer=clean_text(data.get('reviewer')), special=is_yes(data.get('special')),
      sent_by=clean_text(data['sent_by']), requires_reviewer=is_yes(data.get('requires_reviewer')),
      requires_technical=is_yes(data.get('requires_technical')),
      requires_legal=is_yes(data.get('requires_legal')),
      current_status='RECIBIDA',current_owner='Seguimiento')
    if case.contractor_type not in ('CC','NIT','Opción 3'):
        raise BusinessError('Tipo de contratista no reconocido.')
    if case.sent_by not in ('Contratista','Supervisor'):
        raise BusinessError('Seleccione quién envía la cuenta.')
    case.sla_due=sla_deadline(db,now,case.special)
    db.add(case)
    for t in SUBTASK_TYPES:
        applies=((t=='REVISOR' and case.requires_reviewer) or
                 (t=='TECNICA' and case.requires_technical) or
                 (t=='JURIDICA' and case.requires_legal))
        if applies:
            db.add(Subtask(id=f'{case_id}-{t}',case_id=case_id,raw_case_id=case_id,
                           kind=t,status='PENDIENTE',responsible={'REVISOR':'Revisor','TECNICA':'Firma Técnica','JURIDICA':'Firma Jurídica'}[t],
                           provider={'REVISOR':case.reviewer,'TECNICA':'Soluciones Territoriales','JURIDICA':'Velázquez Osorio'}[t],
                           created_at=now,active=True,source='web'))
    # Radicación automática si contrato y documento identifican una sola ficha.
    candidates=db.scalars(select(RadContract).where(RadContract.number==case.contract_no)).all()
    if len(candidates)>1:
        candidates=[x for x in candidates if normalize(x.document)==normalize(case.document)]
    if len(candidates)==1:
        contract=candidates[0]
        year=int(period[:4]);month=int(period[4:])
        rm=db.scalar(select(RadMonth).where(RadMonth.contract_id==contract.id,
                    RadMonth.year==year,RadMonth.month==month))
        if rm is None:
            rm=RadMonth(contract_id=contract.id,year=year,month=month,filed=True,last_changed_at=now)
            db.add(rm)
        else:
            rm.filed=True;rm.last_changed_at=now
    elif candidates:
        audit(db,actor,'RADICACION_REQUIERE_CONCILIACION',case_id,{'coincidencias':len(candidates)})
    add_event(db,case,'RECIBIDA',actor,role='Seguimiento',observations='Registro inicial del caso')
    audit(db,actor,'REGISTRO',case_id,{'period':period})
    queue_notification(db,case_id,case.contractor_email,'registro',f'Cuenta recibida: {case_id}',
                       f'Confirmamos el registro de la cuenta de cobro {case_id}, contrato {case.contract_no}, período {period}.')
    return case

def apply_review(db,case,outcome,observations,actor):
    if outcome not in REVIEW_ACTIONS:raise BusinessError('Resultado de revisión no válido.')
    if not observations.strip():raise BusinessError('Las observaciones son obligatorias.')
    st,owner,ev=REVIEW_ACTIONS[outcome]
    change_status(db,case,st,owner)
    add_event(db,case,ev,actor,observations,'Revisor',st,owner)
    audit(db,actor,'REVISION',case.id_caso,{'resultado':outcome})

def apply_followup(db,case,action,observations,actor,multi=False,fronts=None,closed_date=None):
    if action not in FOLLOWUP_ACTIONS:raise BusinessError('Acción no válida.')
    if not observations.strip():raise BusinessError('Las observaciones son obligatorias.')
    if action=='Cerrar' and not closed_date:
        raise BusinessError('La fecha real de cierre financiera es obligatoria al cerrar.')
    if multi and not fronts:
        raise BusinessError('Seleccione los frentes paralelos.')
    st,owner,ev=FOLLOWUP_ACTIONS[action]
    if action=='Enviado a firma revisora jurídica' and not case.requires_legal:
        raise BusinessError('Este caso no tiene revisión jurídica habilitada.')
    if action=='Enviado a firma revisora técnica' and not case.requires_technical:
        raise BusinessError('Este caso no tiene revisión técnica habilitada.')
    change_status(db,case,st,owner)
    if action=='Cerrar':
        try:case.finance_closed_at=date.fromisoformat(closed_date)
        except ValueError:raise BusinessError('Fecha de cierre inválida.')
    add_event(db,case,ev,actor,observations,'Seguimiento',st,owner)
    selected=[x for x in (fronts or []) if x in SUBTASK_TYPES] if multi else []
    if not multi:
        if 'revisor' in normalize(action):selected=['REVISOR']
        elif 'tecnica' in normalize(action):selected=['TECNICA']
        elif 'juridica' in normalize(action):selected=['JURIDICA']
    for front in selected:
        if front=='REVISOR' and not case.requires_reviewer:continue
        if front=='TECNICA' and not case.requires_technical:continue
        if front=='JURIDICA' and not case.requires_legal:continue
        sub=db.get(Subtask,f'{case.id_caso}-{front}')
        if sub is None:
            sub=Subtask(id=f'{case.id_caso}-{front}',case_id=case.id_caso,raw_case_id=case.id_caso,
                        kind=front,status='EN_CURSO',active=True,created_at=utcnow())
            db.add(sub)
        else:
            sub.status='EN_CURSO';sub.opened_at=utcnow();sub.active=True;sub.iterations+=1
        add_event(db,case,ev,actor,observations,'Seguimiento',st,owner,sub.id)
    audit(db,actor,'SEGUIMIENTO',case.id_caso,{'accion':action,'frentes':selected})

def apply_legal(db,case,revision_type,provider,outcome,observations,actor):
    if revision_type not in PROVIDERS or provider!=PROVIDERS[revision_type]:
        raise BusinessError('Tipo de revisión o proveedor no válido.')
    if outcome not in LEGAL_OUTCOMES or not observations.strip():
        raise BusinessError('Indique un resultado y observaciones.')
    kind='TECNICA' if revision_type=='Técnica' else 'JURIDICA'
    if kind=='TECNICA' and not case.requires_technical:
        raise BusinessError('El caso no requiere revisión técnica.')
    if kind=='JURIDICA' and not case.requires_legal:
        raise BusinessError('El caso no requiere revisión jurídica.')
    sub=db.get(Subtask,f'{case.id_caso}-{kind}')
    if sub is None:
        sub=Subtask(id=f'{case.id_caso}-{kind}',case_id=case.id_caso,raw_case_id=case.id_caso,
                    kind=kind,source='web',created_at=utcnow())
        db.add(sub)
    sub.provider=provider;sub.status='CERRADA';sub.active=False
    sub.result=outcome;sub.observations=observations;sub.closed_at=utcnow()
    if outcome=='Aprobado':
        state=('APROBADA_FIRMA_REVISORA_TECNICA' if kind=='TECNICA'
               else 'APROBADA_FIRMA_REVISORA_JURIDICA')
        owner='Seguimiento';ev=state
    else:
        state='EN_AJUSTES_CONTRATISTA_SUPERVISOR';owner='Supervisor_Contratista'
        ev=('DEVUELTA_FIRMA_REVISORA_TECNICA' if kind=='TECNICA'
            else 'DEVUELTA_FIRMA_REVISORA_JURIDICA')
    change_status(db,case,state,owner)
    add_event(db,case,ev,actor,observations,'Firma Técnica' if kind=='TECNICA' else 'Firma Jurídica',state,owner,sub.id)
    audit(db,actor,'FIRMA',case.id_caso,{'tipo':revision_type,'resultado':outcome})


def enqueue_due_alerts(db, current_date=None):
    """Prepara alertas de SLA una vez por día sin interrumpir la recepción de casos."""
    from sqlalchemy import select
    current_date=current_date or datetime.now(BOGOTA).date()
    due_cases=db.scalars(select(Case).where(Case.sla_due<=current_date+timedelta(days=1),
                ~Case.current_status.in_(STOP_STATUSES))).all()
    personnel={normalize(p.name):p.email for p in db.scalars(select(Personnel)).all()}
    target_keys=[f'SLA:{current_date.isoformat()}:{c.id_caso}' for c in due_cases]
    seen=set(db.scalars(select(Notification.dedupe_key).where(Notification.dedupe_key.in_(target_keys))).all()) if target_keys else set()
    queued=0
    for case in due_cases:
        key=f'SLA:{current_date.isoformat()}:{case.id_caso}'
        target=case.followup_assignee or ''
        email=target if '@' in target else personnel.get(normalize(target),'')
        if not email or key in seen:continue
        db.add(Notification(dedupe_key=key,case_id=case.id_caso,to_email=email,
            subject=f'Alerta SLA cuenta {case.id_caso}',
            body=f'La cuenta {case.id_caso} (contrato {case.contract_no}) tiene fecha límite {case.sla_due} y estado {case.current_status}.',
            status='PENDIENTE'))
        queued+=1
    return queued
