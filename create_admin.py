from database import SessionLocal
from models import UserDB
from main import hash_password

db = SessionLocal()

# Check if admin already exists
existing = db.query(UserDB).filter(UserDB.username == "admin").first()

if existing:
    print("Admin already exists!")
else:
    admin = UserDB(
        username="admin",
        password=hash_password("admin123"),
        role="admin"
    )
    db.add(admin)
    db.commit()
    print("✅ Admin created! Username: admin | Password: admin123")

db.close()


# This directly inserts an admin into your database. Run it **once only**.
