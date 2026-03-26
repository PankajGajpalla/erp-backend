from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from models import UserDB, Base

# Render database URL
DATABASE_URL = "postgresql://erp_db_gj3i_user:95nvnygizVRoOtdiyWRTUwRAZBWVLcSC@dpg-d71pl0vkijhs73cqpg30-a.oregon-postgres.render.com/erp_db_gj3i"

engine = create_engine(DATABASE_URL)

# Create all tables on Render DB
Base.metadata.create_all(bind=engine)

# Hash password directly here
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

db = Session(engine)

# Check if admin already exists
existing = db.query(UserDB).filter(UserDB.username == "admin").first()

if existing:
    print("Admin already exists!")
else:
    admin = UserDB(
        username="admin",
        password=pwd_context.hash("admin123"),
        role="admin",
        student_id=None
    )
    db.add(admin)
    db.commit()
    print("✅ Admin created!")

db.close()
