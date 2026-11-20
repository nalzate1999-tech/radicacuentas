"""Test initial setup as an independent app process; no test DB cross-contamination."""
import os
import subprocess
import sys
import uuid
from pathlib import Path


def test_initial_admin_can_be_created_through_web_only_once():
    env=os.environ.copy()
    env.update({
        'DATABASE_URL': 'sqlite:////tmp/radica_bootstrap_'+uuid.uuid4().hex+'.sqlite3',
        'APP_ENV':'development',
        'ALLOWED_HOSTS':'testserver,localhost,127.0.0.1',
        'BOOTSTRAP_EMAIL':'admin-setup@example.org',
        'BOOTSTRAP_TOKEN':'test-only-a-unique-secret-that-must-never-be-published',
    })
    code = r'''
import re
from fastapi.testclient import TestClient
from app.main import app
from app.database import Base, engine, SessionLocal
from app.models import User
from sqlalchemy import select
Base.metadata.create_all(engine)

def csrf(html):
    return re.search(r'name="csrf"\s+value="([^"]+)"',html).group(1)

c=TestClient(app,base_url='http://testserver',follow_redirects=False)
page=c.get('/configuracion-inicial')
assert page.status_code==200,page.text[:200]
assert 'Código único' not in page.text
assert 'name="code"' not in page.text
assert 'name="password"' not in page.text  # Formulario de creación cerrado sin enlace.
data={'csrf':csrf(page.text),'name':'Administrador Principal',
      'email':'admin-setup@example.org',
      'password':'PasswordAdmin-2026!','confirm_password':'PasswordAdmin-2026!'}
assert c.post('/configuracion-inicial',data=data).status_code==403
assert c.post('/configuracion-inicial/validar',data={'csrf':csrf(page.text),'token':'incorrecto'}).status_code==403
assert c.post('/configuracion-inicial',data=data).status_code==403
activation=c.post('/configuracion-inicial/validar',data={'csrf':csrf(page.text),
   'token':'test-only-a-unique-secret-that-must-never-be-published'})
assert activation.status_code==200,activation.text
assert activation.json()['ok'] is True
page=c.get('/configuracion-inicial')
assert page.status_code==200
assert 'name="password"' in page.text
assert 'name="code"' not in page.text
assert 'Código único' not in page.text
data['csrf']=csrf(page.text)
created=c.post('/configuracion-inicial',data=data)
assert created.status_code==303,created.text[:400]
assert created.headers['location']=='/ingresar?configurado=1'
with SessionLocal() as db:
    u=db.scalar(select(User).where(User.email=='admin-setup@example.org'))
    assert u.role=='admin' and u.active
assert c.get('/configuracion-inicial').status_code==303
assert c.get('/configuracion-inicial').headers['location']=='/ingresar?configuracion=lista'
assert c.post('/configuracion-inicial',data=data).status_code in (403, 404)
assert c.post('/configuracion-inicial/validar',data={'csrf':data['csrf'],
    'token':'test-only-a-unique-secret-that-must-never-be-published'}).status_code in (403, 404)
login_page=c.get('/ingresar')
assert c.post('/ingresar',data={'csrf':csrf(login_page.text),
      'email':'admin-setup@example.org','password':'PasswordAdmin-2026!'}).status_code==303
assert c.get('/usuarios').status_code==200

'''
    result=subprocess.run([sys.executable,'-c',code],cwd=str(Path(__file__).resolve().parents[1]),
                          env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=40)
    assert result.returncode==0, result.stdout+'\n'+result.stderr
