import os
import re
import uuid
os.environ['DATABASE_URL']='sqlite:////tmp/radica_bootstrap_'+uuid.uuid4().hex+'.sqlite3'
os.environ['APP_ENV']='development'
os.environ['ALLOWED_HOSTS']='testserver,localhost,127.0.0.1'
os.environ['BOOTSTRAP_EMAIL']='admin-setup@example.org'
os.environ['BOOTSTRAP_TOKEN']='test-only-a-unique-secret-that-must-never-be-published'
from fastapi.testclient import TestClient
from app.main import app
from app.database import Base, engine, SessionLocal
from app.models import User
from sqlalchemy import select
Base.metadata.create_all(engine)

def csrf(html):
    return re.search(r'name="csrf"\s+value="([^"]+)"',html).group(1)

def test_initial_admin_can_be_created_through_web_only_once():
    c=TestClient(app,base_url='http://testserver',follow_redirects=False)
    page=c.get('/configuracion-inicial')
    assert page.status_code==200,page.text[:200]
    data={'csrf':csrf(page.text),'name':'Administrador Principal',
          'email':'admin-setup@example.org','code':'incorrecto',
          'password':'PasswordAdmin-2026!','confirm_password':'PasswordAdmin-2026!'}
    assert c.post('/configuracion-inicial',data=data).status_code==400
    data['code']='test-only-a-unique-secret-that-must-never-be-published'
    created=c.post('/configuracion-inicial',data=data)
    assert created.status_code==303
    assert created.headers['location']=='/ingresar?configurado=1'
    with SessionLocal() as db:
        u=db.scalar(select(User).where(User.email=='admin-setup@example.org'))
        assert u.role=='admin' and u.active
    assert c.get('/configuracion-inicial').status_code==404
    assert c.post('/configuracion-inicial',data=data).status_code==404
    login_page=c.get('/ingresar')
    assert c.post('/ingresar',data={'csrf':csrf(login_page.text),
          'email':'admin-setup@example.org','password':'PasswordAdmin-2026!'}).status_code==303
    assert c.get('/usuarios').status_code==200
