from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import StaticPool
from app.core.config import settings

def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()

_db_url = settings.database_url
_is_sqlite = _db_url.startswith("sqlite")
engine = create_engine(
    _db_url,
    **( {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool} if _is_sqlite else {} ),
    echo=settings.DEBUG,
)
if _is_sqlite:
    event.listen(engine, "connect", _set_sqlite_pragma)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_tables():
    from app.models import models  # noqa
    Base.metadata.create_all(bind=engine, checkfirst=True)