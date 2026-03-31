from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

if not SQLALCHEMY_DATABASE_URL:
    # fallback for local development
    SQLALCHEMY_DATABASE_URL = "postgresql://postgres:yourpassword@localhost:5432/erp_db"
    print("⚠️  WARNING: DATABASE_URL not set. Using local database.")

# Render gives URLs starting with postgres:// but SQLAlchemy needs postgresql://
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_size=5,           # number of connections to keep open
    max_overflow=10,       # extra connections allowed beyond pool_size
    pool_pre_ping=True,    # ✅ test connection before using it (prevents stale connections)
    pool_recycle=300,      # recycle connections every 5 minutes
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()