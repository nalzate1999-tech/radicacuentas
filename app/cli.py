"""Operaciones de instalación, administración e importación para el equipo técnico."""
import argparse
import getpass
import os
import smtplib
import time
from email.message import EmailMessage
from datetime import datetime, timezone
from sqlalchemy import select, func
from .database import Base, engine, SessionLocal
from .models import User, Notification, Case, Event, RawRow, SourceSheet, ImportIssue
from .config import get_settings
from .importer import migrate_workbook
from .services import audit, enqueue_due_alerts
from argon2 import PasswordHasher


def init():
    Base.metadata.create_all(engine)
    print('Esquema de base de datos inicializado. Para producción use alembic upgrade head.')


def create_admin(args):
    pwd=getpass.getpass('Contraseña segura (mínimo 12 caracteres): ')
    if len(pwd)<12:raise SystemExit('La contraseña debe tener al menos 12 caracteres.')
    email=args.email.strip().lower()
    if '@' not in email:raise SystemExit('Correo inválido.')
    with SessionLocal.begin() as db:
        existing=db.scalar(select(User).where(User.email==email))
        if existing:raise SystemExit('El usuario ya existe; no se cambió su contraseña.')
        db.add(User(name=args.name.strip(),email=email,password_hash=PasswordHasher().hash(pwd),role='admin',active=True))
        audit(db,'cli','CREAR_ADMIN',email)
    print('Administrador creado. La contraseña NO se incluyó en archivos de configuración.')


def verify():
    with SessionLocal() as db:
        for name,table in [('casos',Case),('eventos',Event),('filas_raw',RawRow),('novedades',ImportIssue)]:
            print(f'{name}: {db.scalar(select(func.count()).select_from(table)) or 0}')


def process_notifications(limit=30):
    settings=get_settings()
    if not(settings.notifications_enabled and settings.smtp_host and settings.smtp_from):return 0
    with SessionLocal.begin() as db:
        enqueue_due_alerts(db)
    sent=0
    with SessionLocal() as db:
        stmt=select(Notification).where(Notification.status=='PENDIENTE',Notification.attempts<4).order_by(Notification.id).limit(limit)
        if db.bind.dialect.name=='postgresql':stmt=stmt.with_for_update(skip_locked=True)
        notices=db.scalars(stmt).all()
        for n in notices:
            n.status='PROCESANDO';n.attempts+=1
        db.commit()
    for n in notices:
        try:
            msg=EmailMessage();msg['To']=n.to_email;msg['From']=settings.smtp_from
            msg['Subject']=n.subject;msg.set_content(n.body)
            with smtplib.SMTP(settings.smtp_host,settings.smtp_port,timeout=20) as client:
                if settings.smtp_tls:client.starttls()
                if settings.smtp_user:client.login(settings.smtp_user,settings.smtp_password)
                client.send_message(msg)
            with SessionLocal.begin() as db:
                row=db.get(Notification,n.id)
                row.status='ENVIADA';row.sent_at=datetime.now(timezone.utc);row.last_error=None
            sent+=1
        except Exception as exc:
            with SessionLocal.begin() as db:
                row=db.get(Notification,n.id)
                row.status='PENDIENTE' if row.attempts<4 else 'FALLIDA'
                row.last_error=f'{type(exc).__name__}: {str(exc)[:200]}'
    return sent


def run():
    parser=argparse.ArgumentParser(description='Gestión RadicaCuentas')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init');sub.add_parser('verify')
    a=sub.add_parser('admin');a.add_argument('--email',required=True);a.add_argument('--name',required=True)
    i=sub.add_parser('import');i.add_argument('file');i.add_argument('--dry-run',action='store_true')
    sub.add_parser('notifications');sub.add_parser('worker')
    args=parser.parse_args()
    if args.command=='init':init()
    elif args.command=='admin':create_admin(args)
    elif args.command=='verify':verify()
    elif args.command=='import':
        result=migrate_workbook(args.file,dry_run=args.dry_run,verbose=True)
        import json
        print(json.dumps(result,indent=2,ensure_ascii=False))
    elif args.command=='notifications':print('Notificaciones enviadas:',process_notifications())
    elif args.command=='worker':
        print('Worker de notificaciones iniciado.',flush=True)
        while True:
            try:process_notifications()
            except Exception as exc:print('Error de envío de notificaciones:',type(exc).__name__,flush=True)
            time.sleep(60)

if __name__=='__main__':run()
