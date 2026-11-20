"""Migración funcional: datos operativos, no logs ni tablas derivadas de Google Sheets."""
from __future__ import annotations
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from decimal import Decimal
from openpyxl import load_workbook
from sqlalchemy import insert, select, func
from .database import SessionLocal
from .models import (Case, Event, Subtask, RadContract, RadMonth, Personnel, Holiday,
                     ImportBatch, SourceSheet, RawRow, FormResponse, ImportIssue)
from .services import BOGOTA, parse_period, parse_money, is_yes, clean_text, normalize

FORM_SHEETS = {f'Respuestas de formulario {n}':name for n,name in [(1,'registro'),(2,'revisor'),(3,'seguimiento'),(4,'abogados')]}
MONTH_NAMES=['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
# Son salidas técnicas de Apps Script o reportes derivables; no son datos de negocio.
EXCLUDED_SHEETS = frozenset({'LOG', 'TABLERO', 'INFORME SEGUIDORES'})
REQUIRED_SHEETS = frozenset({'CASOS', 'EVENTOS', 'SUBTAREAS', 'SEG RADICACION', *FORM_SHEETS})

def value_json(v):
    if v is None:return None
    if isinstance(v, datetime):return v.isoformat()
    if isinstance(v, date):return v.isoformat()
    if isinstance(v, Decimal):return str(v)
    if isinstance(v,(int,float,str,bool)):return v
    return str(v)

def str_clean(v):
    if v is None:return ''
    if isinstance(v,float) and v.is_integer():return str(int(v))
    return clean_text(v)

def to_dt(v):
    if not v:return None
    if isinstance(v,datetime):dt=v
    elif isinstance(v,date):dt=datetime.combine(v,datetime.min.time())
    else:
        raw=str_clean(v)
        try:dt=datetime.fromisoformat(raw)
        except ValueError:return None
    if dt.tzinfo is None:dt=dt.replace(tzinfo=BOGOTA)
    return dt.astimezone(timezone.utc)

def to_date(v):
    if isinstance(v,datetime):return v.date()
    if isinstance(v,date):return v
    if v:
        try:return date.fromisoformat(str(v)[:10])
        except ValueError:return None
    return None

def row_dict(headers,row):
    return {str_clean(k):row[i] if i<len(row) else None for i,k in enumerate(headers) if k is not None and str_clean(k)}

def f(row,key,default=None):
    return row.get(key,default)

def file_hash(path):
    h=hashlib.sha256()
    with open(path,'rb') as fp:
        for block in iter(lambda:fp.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def issue(db,batch,sheet,row,category,detail):
    db.add(ImportIssue(batch_id=batch.id,sheet_name=sheet,row_number=row,category=category,detail=detail))

def migrate_workbook(file_path, dry_run=False, verbose=False):
    path=Path(file_path)
    if not path.is_file():raise FileNotFoundError(path)
    sha=file_hash(path)
    formula_wb=load_workbook(path, data_only=False, read_only=False, keep_links=True)
    value_wb=load_workbook(path, data_only=True, read_only=True, keep_links=True)
    with SessionLocal() as db:
        already=db.scalar(select(ImportBatch).where(ImportBatch.sha256==sha))
        if already:
            raise ValueError(f'Este archivo ya está migrado como lote #{already.id}; no se duplicaron datos.')
        if db.scalar(select(func.count(Case.id_caso))) or db.scalar(select(func.count(RawRow.id))):
            raise ValueError('La base destino ya contiene datos. Para evitar mezclas, migre primero a una base vacía.')
        batch=ImportBatch(source_name=path.name,sha256=sha,status='EN_CURSO',summary={})
        db.add(batch);db.flush()
        missing=REQUIRED_SHEETS.difference(formula_wb.sheetnames)
        if missing:raise ValueError('Faltan hojas operativas: '+', '.join(sorted(missing)))
        counts={};all_rows={}; headers_by_sheet={}
        excluded_counts={}
        for sh in formula_wb:
            if sh.title in EXCLUDED_SHEETS:
                excluded_counts[sh.title]='omitida'
                if verbose:print('Omitida (derivada/técnica):',sh.title,flush=True)
                continue
            cached=value_wb[sh.title]
            cached.reset_dimensions()
            crows=cached.iter_rows(values_only=True)
            # Leer las columnas existentes según el archivo, no un mapa fijo.
            c_header=next(crows,())
            headers=[str_clean(c.value) if c.value is not None else '' for c in sh[1]]
            headers_by_sheet[sh.title]=headers
            ss=SourceSheet(batch_id=batch.id,name=sh.title,headers=headers,physical_rows=max(0,sh.max_row-1))
            db.add(ss);db.flush()
            raw_buffer=[];rows=[]
            for row_num,(form_row,cached_row) in enumerate(zip(sh.iter_rows(min_row=2),crows),start=2):
                raw=[c.value for c in form_row]
                rendered=[cached_row[i] if i<len(cached_row) else None for i in range(len(form_row))]
                formulas={str(i):str(c.value) for i,c in enumerate(form_row) if c.data_type=='f'}
                links={str(i):c.hyperlink.target if c.hyperlink.target else c.hyperlink.location
                       for i,c in enumerate(form_row) if c.hyperlink is not None}
                if not (any(v is not None and str(v).strip() for v in raw) or formulas or links):
                    continue
                # Valor original + valor calculado + fórmula + hipervínculo físico.
                # No se pierden fórmulas ni celdas con error en el Excel exportado.
                raw_buffer.append(dict(sheet_id=ss.id,row_number=row_num,
                    values=[value_json(v) for v in rendered],
                    formulas=formulas or None, hyperlinks=links or None))
                rows.append((row_num,row_dict(headers,rendered)))
                if len(raw_buffer)>=450:
                    db.execute(insert(RawRow),raw_buffer);raw_buffer=[]
            if raw_buffer:db.execute(insert(RawRow),raw_buffer)
            ss.saved_rows=len(rows)
            all_rows[sh.title]=rows
            counts[sh.title]=len(rows)
            db.flush()
            if verbose:print(sh.title,counts[sh.title],flush=True)
        # Migración operacional. Casos primero para resolver referencias históricas.
        existing=set()
        match_keys=defaultdict(list)
        case_count=0
        for rn,r in all_rows.get('CASOS',[]):
            id1=str_clean(r.get('FV')); id2=str_clean(r.get('ID_CASO'))
            case_id=id1 or id2 or f'MIG-CASOS-FILA-{rn:06d}'
            duplicate_id=case_id in existing
            if duplicate_id:
                issue(db,batch,'CASOS',rn,'ID_DUPLICADO',f'Identificador repetido: {case_id}')
                case_id=f'MIG-CASOS-FILA-{rn:06d}'
            existing.add(case_id)
            if not id1 and id2:
                issue(db,batch,'CASOS',rn,'FV_VACIO','Se recuperó el ID_CASO de la última columna.')
            if not id1 and not id2:
                issue(db,batch,'CASOS',rn,'SIN_ID','Caso sin ID histórico; creado identificador temporal MIG-CASOS-FILA.')
            if id1 and id2 and id1!=id2:
                issue(db,batch,'CASOS',rn,'ID_DISCREPANTE',f'FV={id1}; ID_CASO={id2}')
            c=Case(id_caso=case_id,source='import',needs_reconciliation=duplicate_id or not(id1 or id2),
                received_at=to_dt(r.get('Timestamp_Recepcion')),
                registered_at=to_dt(r.get('Timestamp_Registro')),
                contractor_type=str_clean(r.get('Tipo_Contratista')),
                contractor_name=str_clean(r.get('Contratista')), document=str_clean(r.get('Documento')),
                contract_no=str_clean(r.get('Contrato_Numero')),
                period=parse_period(r.get('Periodo')) or str_clean(r.get('Periodo')),
                amount=parse_money(r.get('Valor_Cuenta_Cobro')),
                contractor_email=str_clean(r.get('Email_Contratista')),
                contractor_phone=str_clean(r.get('Telefono_Contratista')),
                supervisor_name=str_clean(r.get('Nombre del supervisor')),
                supervisor_email=str_clean(r.get('Correo supervisor')),
                supervisor_phone=str_clean(r.get('Telefono supervisor')),
                followup_assignee=str_clean(r.get('Seguimiento_Asignado')),
                reviewer=str_clean(r.get('Revisor_Nombre')),
                special=is_yes(r.get('Caso_Especial')),
                sent_by=str_clean(r.get('Quien_Envia')),
                requires_legal=is_yes(r.get('Aplica_Abogados')),
                requires_technical=False,
                current_status=str_clean(r.get('Estado_Actual')) or 'RECIBIDA',
                current_owner=str_clean(r.get('Responsable_Actual')) or 'Seguimiento',
                last_event_at=to_dt(r.get('Ultimo_Evento_Timestamp')),
                sla_due=to_date(r.get('Fecha_Limite_SLA')),
                last_observation=str_clean(r.get('Observacion_Vigente')),
                finance_closed_at=to_date(r.get('Fecha_Cierre_Financiera_Real')),
                support_link=str_clean(r.get('Link_Soporte_Actual')),
                original_values={k:value_json(v) for k,v in r.items()})
            db.add(c);case_count+=1
            key=(normalize(c.contract_no),str_clean(c.period),normalize(c.document))
            match_keys[key].append(case_id)
        db.flush()
        # Preserve historiales incluso cuando el ID_CASO no se encuentra (case_id NULL).
        for rn,r in all_rows.get('EVENTOS',[]):
            raw_id=str_clean(r.get('ID_CASO'))
            if raw_id and raw_id not in existing:
                issue(db,batch,'EVENTOS',rn,'EVENTO_HUERFANO',f'ID sin coincidencia en CASOS: {raw_id}')
            if raw_id in existing:
                # Los eventos antiguos también acreditan frentes tramitados,
                # aunque el registro original esté desvinculado de un Formulario 1.
                tag=str_clean(r.get('Tipo_Subtarea')).upper()
                target=db.get(Case,raw_id)
                if tag=='REVISOR':target.requires_reviewer=True
                elif tag=='TECNICA':target.requires_technical=True
                elif tag=='JURIDICA':target.requires_legal=True
            db.add(Event(case_id=raw_id if raw_id in existing else None,
                raw_case_id=raw_id,task_id=str_clean(r.get('ID_SUBTAREA')) or None,
                occurred_at=to_dt(r.get('Timestamp_Evento')),
                kind=str_clean(r.get('Evento')) or 'SIN_EVENTO',
                actor_role=str_clean(r.get('Actor_Rol')),
                actor_name=str_clean(r.get('Actor_Nombre')),
                observations=str_clean(r.get('Observaciones')),
                resulting_status=str_clean(r.get('Estado_Resultante')),
                resulting_owner=str_clean(r.get('Responsable_Resultante')),
                provider=str_clean(r.get('Proveedor_Subtarea')),
                file_link=str_clean(r.get('Link_Archivo')),
                source='import',source_row=rn,
                original_values={k:value_json(v) for k,v in r.items()}))
        db.flush()
        for rn,r in all_rows.get('SUBTAREAS',[]):
            case_id=str_clean(r.get('ID_CASO'))
            raw_task=str_clean(r.get('ID_SUBTAREA'))
            task_id=raw_task or f'MIG-SUB-FILA-{rn:06d}'
            if db.get(Subtask,task_id):
                issue(db,batch,'SUBTAREAS',rn,'SUBTAREA_DUPLICADA',f'ID {task_id}')
                task_id=f'MIG-SUB-FILA-{rn:06d}'
            if case_id and case_id not in existing:
                issue(db,batch,'SUBTAREAS',rn,'SUBTAREA_HUERFANA',f'ID {case_id}')
            task_kind=str_clean(r.get('Tipo_Subtarea')).upper()
            if case_id in existing:
                target=db.get(Case,case_id)
                if task_kind=='REVISOR':target.requires_reviewer=True
                elif task_kind=='TECNICA':target.requires_technical=True
                elif task_kind=='JURIDICA':target.requires_legal=True
            db.add(Subtask(id=task_id,case_id=case_id if case_id in existing else None,
                raw_case_id=case_id,kind=task_kind,
                provider=str_clean(r.get('Proveedor')),
                status=str_clean(r.get('Estado_Subtarea')) or 'PENDIENTE',
                responsible=str_clean(r.get('Responsable_Subtarea')),
                created_at=to_dt(r.get('Fecha_Creacion')),
                opened_at=to_dt(r.get('Fecha_Apertura')),
                closed_at=to_dt(r.get('Fecha_Cierre')),
                due_at=to_date(r.get('Fecha_Limite_SLA')),
                result=str_clean(r.get('Resultado')),
                observations=str_clean(r.get('Observaciones')),
                file_link=str_clean(r.get('Link_Archivo')),
                iterations=int(r.get('Iteraciones') or 0) if isinstance(r.get('Iteraciones'),(int,float)) else 0,
                active=is_yes(r.get('Activa')),source='import',
                original_values={k:value_json(v) for k,v in r.items()}))
        db.flush()
        for rn,r in all_rows.get('SEG RADICACION',[]):
            number=str_clean(r.get('Contrato'))
            if not number:
                issue(db,batch,'SEG RADICACION',rn,'SIN_CONTRATO','Fila sin número de contrato.')
                number=f'MIG-RAD-{rn}'
            rc=RadContract(number=number, contractor=str_clean(r.get('Nombre Contratista')),
                document_type=str_clean(r.get('Tipo de Documento')),
                document=str_clean(r.get('Num Documento ')) or str_clean(r.get('Num Documento')),
                started_at=to_date(r.get('Fecha Inicio')),ended_at=to_date(r.get('Fecha fin')),
                monthly_value=parse_money(r.get('Val Mensual')),
                total_value=parse_money(r.get('Val Total')),
                contact_email=str_clean(r.get('Correo Contratista')),
                phone=str_clean(r.get('Teléfono')),
                dependency=str_clean(r.get('Dependencia')),
                supervisor=str_clean(r.get('Supervisor')),
                supervisor_email=str_clean(r.get('Correo supervisor ')),
                supervisor_phone=str_clean(r.get('Telefono supervisor')),
                last_notice_at=to_dt(r.get('Último Aviso')),
                legacy_status=str_clean(r.get('Estado Actual')),
                source='import',source_row=rn,
                original_values={k:value_json(v) for k,v in r.items()})
            db.add(rc);db.flush()
            for month,name in enumerate(MONTH_NAMES,start=1):
                raw_month=next((v for k,v in r.items() if k.strip()==name),None)
                db.add(RadMonth(contract_id=rc.id,year=2026,month=month,filed=is_yes(raw_month)))
        for rn,r in all_rows.get('USUARIOS',[]):
            name=str_clean(r.get('Nombre'));email=str_clean(r.get('Email')).lower()
            if name and email and '@' in email and not db.scalar(select(Personnel.id).where(Personnel.email==email)):
                db.add(Personnel(name=name,email=email))
        for rn,r in all_rows.get('FESTIVOS',[]):
            d=to_date(r.get('Fecha'))
            if d and not db.get(Holiday,d):db.add(Holiday(day=d))
        # El libro contiene respuestas originales sin ID de caso en el Formulario 1.
        # Sólo vincular si contrato + documento + período llevan a una coincidencia única.
        for sn,formtype in FORM_SHEETS.items():
            for rn,r in all_rows.get(sn,[]):
                raw_id=str_clean(r.get('ID_CASO'))
                case_id=raw_id if raw_id in existing else None
                if not raw_id and formtype=='registro':
                    key=(normalize(r.get('Contrato')),parse_period(r.get('Periodo')) or str_clean(r.get('Periodo')),
                         normalize(r.get('Documento')))
                    matched=match_keys.get(key,[])
                    if len(matched)==1:case_id=matched[0]
                    elif len(matched)>1:issue(db,batch,sn,rn,'VINCULO_AMBIGUO','Más de un caso para contrato/período/documento.')
                    else:issue(db,batch,sn,rn,'FORM1_SIN_VINCULO','No hubo coincidencia exacta y única.')
                elif raw_id and raw_id not in existing:
                    issue(db,batch,sn,rn,'FORMULARIO_HUERFANO',f'ID sin coincidencia: {raw_id}')
                if case_id and formtype=='registro':
                    target=db.get(Case,case_id)
                    for name,field in [('¿Requiere revisión del revisor?','requires_reviewer'),
                                       ('¿Requiere revisión técnica?','requires_technical'),
                                       ('¿Requiere revisión jurídica?','requires_legal')]:
                        if is_yes(r.get(name)):
                            setattr(target,field,True)
                db.add(FormResponse(source_type='historico-'+formtype,source_row=rn,
                    submitted_at=to_dt(r.get('Marca temporal')),case_id=case_id,
                    raw_case_id=raw_id,submitted_by=str_clean(r.get('Dirección de correo electrónico')),
                    values={k:value_json(v) for k,v in r.items()}))
        db.flush()
        issue_summary={name:n for name,n in db.execute(select(ImportIssue.category,func.count(ImportIssue.id)).where(ImportIssue.batch_id==batch.id).group_by(ImportIssue.category)).all()}
        summary={'source_file_sha256':sha,'source_rows_by_sheet':counts,'cases':case_count,
            'events':len(all_rows.get('EVENTOS',[])),
            'subtasks':len(all_rows.get('SUBTAREAS',[])),
            'rad_contracts':len(all_rows.get('SEG RADICACION',[])),
            'form_responses':sum(len(all_rows.get(x,[])) for x in FORM_SHEETS),
            'omitted_technical_sheets':excluded_counts,
            'issues_by_category':issue_summary,'raw_rows_total':sum(counts.values())}
        batch.status='COMPLETADA';batch.summary=summary
        if dry_run:db.rollback()
        else:db.commit()
        return summary
