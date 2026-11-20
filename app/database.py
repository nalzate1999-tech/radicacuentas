from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import get_settings

class Base(DeclarativeBase):
    pass

url = get_settings().database_url
if url.startswith('postgresql://'):
    url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
connect_args = {'check_same_thread': False} if url.startswith('sqlite') else {}
engine = create_engine(url, pool_pre_ping=True, pool_size=8 if not connect_args else 5,
                       max_overflow=12 if not connect_args else 10,
                       connect_args=connect_args, future=True)
SessionLocal = sessionmaker(engine, autoflush=False, expire_on_commit=False)

def db_session():
    with SessionLocal() as session:
        yield session
