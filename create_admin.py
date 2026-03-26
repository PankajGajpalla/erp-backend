from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from models import UserDB, Base
from main import hash_password

DATABASE_URL = "postgresql://erp_db_gj3i_user:95nvnygizVRoOtdiyWRTUwRAZBWVLcSC@dpg-d71pl0vkijhs73cqpg30-a/erp_db_gj3i"

engine = create_engine(DATABASE_URL)
db = Session(engine)

admin = UserDB(
    username="admin",
    password=hash_password("admin123"),
    role="admin",
    student_id=None
)
db.add(admin)
db.commit()
db.close()
print("✅ Admin created!")