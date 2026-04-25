from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text, func
from database import SessionLocal, engine, Base
from models import StudentDB, UserDB, AttendanceDB, FeesDB, FeePaymentDB, TeacherDB, NoticeDB, GradeDB, TimetableDB, CourseDB, SubjectDB, StudentAdditionalCourseDB, FeeTemplateDB, ExamScheduleDB, AuditLogDB, NoticeReadDB
from pydantic import BaseModel
from fastapi import HTTPException
from passlib.context import CryptContext
import os
from datetime import date, datetime, timedelta, timezone
from jose import JWTError, jwt
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Literal, Optional, List

from fastapi.middleware.cors import CORSMiddleware

import httpx

# ── SMS / DLT configuration ───────────────────────────────────────────────────
# Set these in your Render (or any hosting) environment variables.
#
#   FAST2SMS_API_KEY  — your Fast2SMS API key
#   DLT_SENDER_ID     — approved DLT header  (default: ABSFND)
#   DLT_TEMPLATE_ID   — Fast2SMS internal template ID (shown in Fast2SMS DLT panel)
#                       This is NOT the TRAI template ID — it is the short numeric
#                       ID Fast2SMS assigns after you register the template there.
#                       Default: 213770
#
# Approved template (3 variables — name | status | date):
#   "Dear Parent, this is to inform you that student {#VAR#} was marked as
#    {#VAR#} on date {#VAR#}. Regards, -ABS Foundation"
#
# API format used: message=<fast2sms_template_id> + variables_values=V1|V2|V3
# ─────────────────────────────────────────────────────────────────────────────
FAST2SMS_API_KEY = os.getenv("FAST2SMS_API_KEY", "")
DLT_SENDER_ID    = os.getenv("DLT_SENDER_ID",  "ABSFND")
DLT_TEMPLATE_ID  = os.getenv("DLT_TEMPLATE_ID", "213770")


async def send_sms(phone: str, variables_values: str) -> bool:
    """Send a DLT-compliant transactional SMS via Fast2SMS.

    variables_values: pipe-separated variable substitutions matching the approved
    template order, e.g. "Rahul Kumar|PRESENT|2025-04-17"

    Returns True if Fast2SMS accepted the request, False otherwise.
    All failures are logged but never raise — SMS is non-critical.
    """
    if not FAST2SMS_API_KEY:
        print("❌ SMS: FAST2SMS_API_KEY not set — skipping")
        return False
    if not DLT_TEMPLATE_ID:
        print("❌ SMS: DLT_TEMPLATE_ID not set — skipping (set it in Render env vars)")
        return False
    if not phone:
        print("❌ SMS: no phone number — skipping")
        return False

    # Handle multiple numbers in one field (e.g. "9876543210, 9898989898")
    # Split by comma, slash, semicolon, or whitespace and pick the first valid number
    import re as _re
    parts = _re.split(r"[,/;\s]+", phone.strip())
    clean_phone = ""
    for part in parts:
        digits = "".join(c for c in part if c.isdigit())
        if digits.startswith("91") and len(digits) == 12:
            digits = digits[2:]          # strip leading country code
        if len(digits) == 10:
            clean_phone = digits
            break                        # use the first valid number found

    if not clean_phone:
        print(f"❌ SMS: no valid 10-digit number found in '{phone}' — skipping")
        return False

    try:
        print(f"📱 Sending DLT SMS to {clean_phone} | vars: {variables_values}…")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://www.fast2sms.com/dev/bulkV2",
                headers={"authorization": FAST2SMS_API_KEY},
                json={
                    "route":            "dlt",
                    "sender_id":        DLT_SENDER_ID,
                    "message":          DLT_TEMPLATE_ID,
                    "variables_values": variables_values,
                    "numbers":          clean_phone,
                    "flash":            "0",
                },
                timeout=10,
            )
            data = response.json()
            print(f"📱 Fast2SMS DLT response: {data}")
            return bool(data.get("return", False))
    except Exception as exc:
        print(f"❌ SMS error: {exc}")
        return False


# App Creation
app = FastAPI(title="ERP System", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create tables
Base.metadata.create_all(bind=engine)

# Auto-migrate: add new columns to existing tables (safe, idempotent)
# Each statement runs in its own connection so a failure doesn't abort others.
def run_migrations():
    statements = [
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS father_name VARCHAR(100)",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS dob DATE",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS permanent_address VARCHAR(500)",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS local_address VARCHAR(500)",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS school_college_name VARCHAR(200)",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS medium VARCHAR(20)",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS admission_date DATE",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS photo TEXT",
        "ALTER TABLE fees ADD COLUMN IF NOT EXISTS due_date DATE",
        "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS subject_id INTEGER REFERENCES subjects(id)",
        # Drop both old constraints before re-evaluating (safe if they don't exist)
        "ALTER TABLE attendance DROP CONSTRAINT IF EXISTS unique_student_date",
        "ALTER TABLE attendance DROP CONSTRAINT IF EXISTS unique_student_date_subject",
        "ALTER TABLE grades ADD COLUMN IF NOT EXISTS test_title VARCHAR(200)",
        "ALTER TABLE students DROP COLUMN IF EXISTS age",
        "ALTER TABLE students DROP COLUMN IF EXISTS address",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS student_code VARCHAR(20)",
        "UPDATE students SET student_code = CONCAT('STU', LPAD(id::text, 4, '0')) WHERE student_code IS NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_students_student_code ON students(student_code)",
        "ALTER TABLE timetable ADD COLUMN IF NOT EXISTS course_id INTEGER REFERENCES courses(id)",
        "ALTER TABLE students ALTER COLUMN email DROP NOT NULL",
        "ALTER TABLE students ALTER COLUMN phone SET NOT NULL",
        # Widen phone columns — VARCHAR(20) too short for multiple numbers
        "ALTER TABLE students ALTER COLUMN phone TYPE VARCHAR(50)",
        "ALTER TABLE students ALTER COLUMN parent_phone TYPE VARCHAR(100)",
        "ALTER TABLE teachers ALTER COLUMN phone TYPE VARCHAR(50)",
        # Additional courses junction table
        """CREATE TABLE IF NOT EXISTS student_additional_courses (
            id SERIAL PRIMARY KEY,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            course_id  INTEGER NOT NULL REFERENCES courses(id)  ON DELETE CASCADE,
            CONSTRAINT uq_student_additional_course UNIQUE (student_id, course_id)
        )""",
        # ── Attendance constraint change ──────────────────────────────────────────
        # Old design: one record per (student, date, subject) — subject is no longer
        # part of attendance. Collapse to one record per (student, date).
        # Step 1: deduplicate — keep only the row with the highest id per pair
        """DELETE FROM attendance WHERE id NOT IN (
            SELECT MAX(id) FROM attendance GROUP BY student_id, date
        )""",
        # Step 2: drop the old three-column unique constraint
        "ALTER TABLE attendance DROP CONSTRAINT IF EXISTS unique_student_date_subject",
        # Step 3: add (or restore) the simpler two-column constraint
        # NOTE: PostgreSQL does NOT support ADD CONSTRAINT IF NOT EXISTS — use a DO block instead
        """DO $$ BEGIN
            ALTER TABLE attendance ADD CONSTRAINT unique_student_date UNIQUE (student_id, date);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$""",
        "ALTER TABLE notices ADD COLUMN IF NOT EXISTS course VARCHAR(100)",
        """CREATE TABLE IF NOT EXISTS fee_templates (
            id SERIAL PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            course_id INTEGER REFERENCES courses(id),
            amount FLOAT NOT NULL,
            description VARCHAR(200)
        )""",
        """CREATE TABLE IF NOT EXISTS exam_schedule (
            id SERIAL PRIMARY KEY,
            course_id INTEGER NOT NULL REFERENCES courses(id),
            title VARCHAR(200) NOT NULL,
            subject VARCHAR(200) NOT NULL,
            exam_date DATE NOT NULL,
            exam_time VARCHAR(50),
            duration VARCHAR(50),
            syllabus TEXT,
            total_marks FLOAT
        )""",
        """CREATE TABLE IF NOT EXISTS audit_logs (
            id SERIAL PRIMARY KEY,
            performed_by VARCHAR(100) NOT NULL,
            action VARCHAR(50) NOT NULL,
            entity VARCHAR(100) NOT NULL,
            entity_id INTEGER,
            details TEXT,
            timestamp VARCHAR(50) NOT NULL
        )""",
        "ALTER TABLE fee_payments ADD COLUMN IF NOT EXISTS payment_mode VARCHAR(50)",
        """CREATE TABLE IF NOT EXISTS notice_reads (
            id SERIAL PRIMARY KEY,
            notice_id INTEGER REFERENCES notices(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            read_at VARCHAR(50) NOT NULL,
            CONSTRAINT uq_notice_read UNIQUE (notice_id, user_id)
        )""",
    ]
    for sql in statements:
        try:
            with engine.connect() as conn:
                conn.execute(text(sql))
                conn.commit()
        except Exception as e:
            print(f"Migration skipped ({sql[:60]}...): {e}")
    print("✅ Migrations complete")

run_migrations()

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Password Hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str):
    return pwd_context.hash(password[:72])

def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)

def log_audit(db: Session, performed_by: str, action: str, entity: str, entity_id: int = None, details: str = None):
    """Log an admin action to the audit trail."""
    try:
        db.add(AuditLogDB(
            performed_by=performed_by,
            action=action,
            entity=entity,
            entity_id=entity_id,
            details=details,
            timestamp=datetime.now().isoformat()
        ))
        db.commit()
    except Exception as e:
        print(f"Audit log error: {e}")

# JWT CONFIG
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60  # increased to 1 hour

if SECRET_KEY == "dev-secret":
    print("⚠️  WARNING: Using default SECRET_KEY. Set SECRET_KEY env variable in production!")

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# ----------------------------------------------------------------------------------------------------
# SECURITY

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return {
            "username": payload.get("sub"),
            "role": payload.get("role"),
            "student_id": payload.get("student_id"),
            "teacher_id": payload.get("teacher_id"),
        }
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def require_role(required_role: str):
    def role_checker(user: dict = Depends(get_current_user)):
        if user["role"] != required_role:
            raise HTTPException(status_code=403, detail="Access denied")
        return user
    return role_checker

def require_roles(allowed_roles: list):
    def role_checker(user: dict = Depends(get_current_user)):
        if user["role"] not in allowed_roles:
            raise HTTPException(status_code=403, detail="Access denied")
        return user
    return role_checker

# ----------------------------------------------------------------------------------------------------
# PYDANTIC MODELS

class UserCreate(BaseModel):
    username: str
    password: str
    student_id: int

class AdminCreate(BaseModel):
    username: str
    password: str

class TeacherUserCreate(BaseModel):
    username: str
    password: str
    teacher_id: int

class UserLogin(BaseModel):
    username: str
    password: str

class Student(BaseModel):
    name: str                               # required
    phone: str                              # required
    father_name: Optional[str] = None
    dob: Optional[date] = None
    email: Optional[str] = None
    parent_phone: Optional[str] = None
    permanent_address: Optional[str] = None
    local_address: Optional[str] = None
    course: Optional[str] = None
    fees: Optional[float] = None
    fees_paid: Optional[float] = None      # amount already paid at time of import
    school_college_name: Optional[str] = None
    medium: Optional[str] = None
    admission_date: Optional[date] = None
    photo: Optional[str] = None

class StudentBulk(BaseModel):
    students: List[Student]

class BulkUpdateCourse(BaseModel):
    student_ids: List[int]
    course: str

class AttendanceCreate(BaseModel):
    student_id: int
    date: date
    status: Literal["present", "absent"]

class AttendanceCheckRequest(BaseModel):
    date: date
    student_ids: List[int]

class FeesCreate(BaseModel):
    student_id: int
    amount: float
    description: Optional[str] = None
    due_date: Optional[date] = None

class FeesPayment(BaseModel):
    pay_amount: float
    paid_date: Optional[date] = None
    note: Optional[str] = None
    payment_mode: Optional[str] = None

class FeesUpdate(BaseModel):
    amount: Optional[float] = None
    description: Optional[str] = None
    due_date: Optional[date] = None

class TeacherCreate(BaseModel):
    name: str
    email: str
    subject: Optional[str] = ""
    phone: Optional[str] = None

class TeacherSubjectsUpdate(BaseModel):
    subject_ids: List[int]

class StudentAdditionalCoursesUpdate(BaseModel):
    course_ids: List[int]

class GradeCreate(BaseModel):
    student_id: int
    subject: str
    marks: float
    total_marks: float
    test_title: Optional[str] = None

class TimetableCreate(BaseModel):
    course_id: int
    day: str
    subject: str
    teacher: str
    time_slot: str

class NoticeCreate(BaseModel):
    title: str
    content: str
    date: Optional[date] = None
    course: Optional[str] = None   # null = all courses

class CourseCreate(BaseModel):
    name: str
    description: Optional[str] = None
    duration: Optional[str] = None
    fees: Optional[float] = None

class SubjectCreate(BaseModel):
    course_id: int
    name: str
    teacher_id: Optional[int] = None

class CredentialsUpdate(BaseModel):
    username: str
    password: Optional[str] = None   # if None, keep existing password

# ----------------------------------------------------------------------------------------------------

@app.get("/")
def home():
    return {"message": "ERP System Running 🚀"}

# ----------------------------------------------------------------------------------------------------
# AUTH

@app.post("/register")
def register(user: UserCreate, db: Session = Depends(get_db)):
    if db.query(UserDB).filter(UserDB.username == user.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")

    student = db.query(StudentDB).filter(StudentDB.id == user.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student ID not found. Contact admin.")

    if db.query(UserDB).filter(UserDB.student_id == user.student_id).first():
        raise HTTPException(status_code=400, detail="This Student ID already has an account")

    new_user = UserDB(
        username=user.username,
        password=hash_password(user.password),
        role="student",
        student_id=user.student_id
    )
    db.add(new_user)
    db.commit()
    return {"message": f"Account created! Welcome {student.name}"}


@app.post("/setup_first_admin")
def setup_first_admin(user: AdminCreate, db: Session = Depends(get_db)):
    """Only works when zero admins exist — use this to bootstrap after a DB reset."""
    existing = db.query(UserDB).filter(UserDB.role == "admin").first()
    if existing:
        raise HTTPException(status_code=403, detail="Admin already exists. Use /create_admin instead.")
    new_admin = UserDB(username=user.username, password=hash_password(user.password), role="admin")
    db.add(new_admin)
    db.commit()
    return {"message": f"Admin '{user.username}' created successfully"}


@app.post("/create_admin")
def create_admin(
    user: AdminCreate,
    db: Session = Depends(get_db),
    current: dict = Depends(require_role("admin"))
):
    if db.query(UserDB).filter(UserDB.username == user.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")

    new_admin = UserDB(
        username=user.username,
        password=hash_password(user.password),
        role="admin",
        student_id=None
    )
    db.add(new_admin)
    db.commit()
    return {"message": f"Admin '{user.username}' created successfully"}


# ── List all admin accounts ────────────────────────────────────
@app.get("/admins")
def list_admins(
    db: Session = Depends(get_db),
    current: dict = Depends(require_role("admin"))
):
    admins = db.query(UserDB).filter(UserDB.role == "admin").all()
    return {"admins": [{"id": a.id, "username": a.username} for a in admins]}


# ── Update admin username / password ──────────────────────────
@app.put("/admins/{admin_id}")
def update_admin(
    admin_id: int,
    data: CredentialsUpdate,
    db: Session = Depends(get_db),
    current: dict = Depends(require_role("admin"))
):
    admin = db.query(UserDB).filter(UserDB.id == admin_id, UserDB.role == "admin").first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    # Username uniqueness check (exclude self)
    clash = db.query(UserDB).filter(UserDB.username == data.username, UserDB.id != admin_id).first()
    if clash:
        raise HTTPException(status_code=400, detail="Username already taken by another account")

    admin.username = data.username
    if data.password:
        admin.password = hash_password(data.password)
    db.commit()
    return {"message": f"Admin '{data.username}' updated successfully"}


# ── Delete an admin account (cannot delete self) ───────────────
@app.delete("/admins/{admin_id}")
def delete_admin(
    admin_id: int,
    db: Session = Depends(get_db),
    current: dict = Depends(require_role("admin"))
):
    admin = db.query(UserDB).filter(UserDB.id == admin_id, UserDB.role == "admin").first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    # Prevent deleting yourself
    if admin.username == current["username"]:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")

    db.delete(admin)
    db.commit()
    return {"message": f"Admin '{admin.username}' deleted"}


@app.post("/create_staff")
def create_staff(
    user: AdminCreate,
    db: Session = Depends(get_db),
    current: dict = Depends(require_role("admin"))
):
    if db.query(UserDB).filter(UserDB.username == user.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    new_staff = UserDB(
        username=user.username,
        password=hash_password(user.password),
        role="staff"
    )
    db.add(new_staff)
    db.commit()
    db.refresh(new_staff)
    log_audit(db, current["username"], "CREATE", "Staff", new_staff.id, f"Staff account: {user.username}")
    return {"message": "Staff account created", "id": new_staff.id, "username": new_staff.username}

@app.get("/staff")
def get_staff(
    db: Session = Depends(get_db),
    current: dict = Depends(require_role("admin"))
):
    staff = db.query(UserDB).filter(UserDB.role == "staff").all()
    return {"staff": [{"id": s.id, "username": s.username, "role": s.role} for s in staff]}

@app.put("/staff/{staff_id}")
def update_staff(
    staff_id: int,
    data: CredentialsUpdate,
    db: Session = Depends(get_db),
    current: dict = Depends(require_role("admin"))
):
    staff = db.query(UserDB).filter(UserDB.id == staff_id, UserDB.role == "staff").first()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff not found")
    if data.username and data.username != staff.username:
        if db.query(UserDB).filter(UserDB.username == data.username).first():
            raise HTTPException(status_code=400, detail="Username already taken")
        staff.username = data.username
    if data.password:
        staff.password = hash_password(data.password)
    db.commit()
    log_audit(db, current["username"], "UPDATE", "Staff", staff_id, f"Updated staff: {staff.username}")
    return {"message": "Staff updated"}

@app.delete("/staff/{staff_id}")
def delete_staff(
    staff_id: int,
    db: Session = Depends(get_db),
    current: dict = Depends(require_role("admin"))
):
    staff = db.query(UserDB).filter(UserDB.id == staff_id, UserDB.role == "staff").first()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff not found")
    db.delete(staff)
    db.commit()
    log_audit(db, current["username"], "DELETE", "Staff", staff_id, f"Deleted staff: {staff.username}")
    return {"message": "Staff deleted"}


@app.post("/login")
def login(user: UserLogin, db: Session = Depends(get_db)):
    db_user = db.query(UserDB).filter(UserDB.username == user.username).first()

    if not db_user or not verify_password(user.password, db_user.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_access_token({
        "sub": db_user.username,
        "role": db_user.role,
        "student_id": db_user.student_id,
        "teacher_id": db_user.teacher_id
    })
    return {"access_token": token, "token_type": "bearer"}

# ----------------------------------------------------------------------------------------------------
# DASHBOARD

@app.get("/dashboard/summary")
def dashboard_summary(
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    total_students = db.query(StudentDB).count()
    total_attendance = db.query(AttendanceDB).count()
    fees = db.query(FeesDB).all()
    total_fees = sum(f.amount or 0 for f in fees)
    total_paid = sum(f.paid or 0 for f in fees)

    # Course-wise student count — group directly from students table
    # so ALL students are counted regardless of CourseDB entries
    from sqlalchemy import func as sqlfunc
    course_rows = (
        db.query(StudentDB.course, sqlfunc.count(StudentDB.id).label("cnt"))
        .filter(StudentDB.course != None, StudentDB.course != "")
        .group_by(StudentDB.course)
        .all()
    )
    course_stats = [{"name": row.course, "students": row.cnt} for row in course_rows]
    # Students with no course assigned
    no_course_count = db.query(StudentDB).filter(
        (StudentDB.course == None) | (StudentDB.course == "")
    ).count()
    if no_course_count > 0:
        course_stats.append({"name": "No Course", "students": no_course_count})

    # Overdue fees count
    today = date.today()
    overdue_count = db.query(FeesDB).filter(
        FeesDB.due_date != None,
        FeesDB.due_date < today,
        FeesDB.amount > FeesDB.paid
    ).count()

    # Total teachers
    total_teachers = db.query(TeacherDB).count()

    # Today's attendance percentage
    today_records = db.query(AttendanceDB).filter(AttendanceDB.date == today).all()
    att_present = sum(1 for r in today_records if r.status == "present")
    att_absent = sum(1 for r in today_records if r.status == "absent")
    att_total = att_present + att_absent
    att_pct = round((att_present / att_total * 100) if att_total > 0 else 0, 1)
    attendance_today_pct = {"present": att_present, "absent": att_absent, "pct": att_pct}

    # Upcoming exams (next 7 days)
    week_later = today + timedelta(days=7)
    upcoming_exams_q = db.query(ExamScheduleDB).filter(
        ExamScheduleDB.exam_date >= today,
        ExamScheduleDB.exam_date <= week_later
    ).order_by(ExamScheduleDB.exam_date).limit(5).all()
    upcoming_exams = [
        {"id": e.id, "title": e.title, "subject": e.subject,
         "exam_date": str(e.exam_date), "exam_time": e.exam_time, "course_id": e.course_id}
        for e in upcoming_exams_q
    ]

    # Recent notices (last 5)
    recent_notices_q = db.query(NoticeDB).order_by(NoticeDB.date.desc()).limit(5).all()
    recent_notices = [
        {"id": n.id, "title": n.title, "date": str(n.date), "course": n.course}
        for n in recent_notices_q
    ]

    return {
        "total_students": total_students,
        "total_attendance": total_attendance,
        "total_fees": total_fees,
        "total_paid": total_paid,
        "total_pending": total_fees - total_paid,
        "overdue_fees_count": overdue_count,
        "course_stats": course_stats,
        "total_teachers": total_teachers,
        "attendance_today_pct": attendance_today_pct,
        "upcoming_exams": upcoming_exams,
        "recent_notices": recent_notices,
    }


@app.get("/dashboard/staff-summary")
def staff_dashboard_summary(
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles(["admin", "staff"]))
):
    total_students = db.query(StudentDB).count()
    total_courses = db.query(CourseDB).count()
    today = date.today()
    today_records = db.query(AttendanceDB).filter(AttendanceDB.date == today).all()
    att_present = sum(1 for r in today_records if r.status == "present")
    att_absent = sum(1 for r in today_records if r.status == "absent")
    att_total = att_present + att_absent
    att_pct = round((att_present / att_total * 100) if att_total > 0 else 0, 1)

    recent_notices = db.query(NoticeDB).order_by(NoticeDB.date.desc()).limit(5).all()

    week_later = today + timedelta(days=7)
    upcoming_exams = db.query(ExamScheduleDB).filter(
        ExamScheduleDB.exam_date >= today,
        ExamScheduleDB.exam_date <= week_later
    ).order_by(ExamScheduleDB.exam_date.asc()).limit(5).all()

    return {
        "total_students": total_students,
        "total_courses": total_courses,
        "attendance_today": {"present": att_present, "absent": att_absent, "pct": att_pct},
        "recent_notices": [{"id": n.id, "title": n.title, "date": str(n.date), "course": n.course} for n in recent_notices],
        "upcoming_exams": [{"id": e.id, "title": e.title, "subject": e.subject, "exam_date": str(e.exam_date), "exam_time": e.exam_time} for e in upcoming_exams],
    }


@app.get("/fees/overdue")
def get_overdue_fees(
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    today = date.today()
    overdue_fees = db.query(FeesDB).filter(
        FeesDB.due_date != None,
        FeesDB.due_date < today,
        FeesDB.amount > FeesDB.paid
    ).all()

    if not overdue_fees:
        return []

    # Batch-load students to avoid N+1
    student_ids = list({f.student_id for f in overdue_fees})
    students = {s.id: s for s in db.query(StudentDB).filter(StudentDB.id.in_(student_ids)).all()}

    result = []
    for f in overdue_fees:
        student = students.get(f.student_id)
        result.append({
            "fee_id": f.id,
            "student_id": f.student_id,
            "student_name": student.name if student else "Unknown",
            "student_code": student.student_code if student else None,
            "course": student.course if student else None,
            "description": f.description,
            "amount": f.amount,
            "paid": f.paid,
            "pending": (f.amount or 0) - (f.paid or 0),
            "due_date": str(f.due_date),
            "days_overdue": (today - f.due_date).days
        })
    result.sort(key=lambda x: x["days_overdue"], reverse=True)
    return result

# ----------------------------------------------------------------------------------------------------
# STUDENTS

@app.post("/add_student")
def add_student(
    student: Student,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles(["admin", "staff"]))
):
    if student.email and db.query(StudentDB).filter(StudentDB.email == student.email).first():
        raise HTTPException(status_code=400, detail="Email already exists")

    new_student = StudentDB(
        name=student.name,
        father_name=student.father_name,
        dob=student.dob,
        email=student.email,
        phone=student.phone,
        parent_phone=student.parent_phone,
        permanent_address=student.permanent_address,
        local_address=student.local_address,
        course=student.course,
        fees=student.fees,
        school_college_name=student.school_college_name,
        medium=student.medium,
        admission_date=student.admission_date,
        photo=student.photo,
    )
    db.add(new_student)
    db.flush()
    new_student.student_code = f"STU{new_student.id:04d}"

    if student.fees:
        db.add(FeesDB(
            student_id=new_student.id,
            amount=student.fees, paid=0.0,
            description="Initial Fees"
        ))

    db.commit()
    db.refresh(new_student)
    try:
        log_audit(db, user["username"], "CREATE", "Student", new_student.id, new_student.name)
    except Exception as e:
        print(f"Audit log error: {e}")
    return {"message": "Student saved", "student": new_student}


# ── Helper: get additional courses for a single student (used in single-student endpoints) ──
def _get_additional_courses(student_id: int, db: Session):
    rows = (
        db.query(CourseDB.id, CourseDB.name)
        .join(StudentAdditionalCourseDB, StudentAdditionalCourseDB.course_id == CourseDB.id)
        .filter(StudentAdditionalCourseDB.student_id == student_id)
        .all()
    )
    return [{"id": cid, "name": cname} for cid, cname in rows]

# ── Batch helper: load additional courses for ALL students in one query ──
def _build_additional_courses_map(db: Session, student_ids: list = None) -> dict:
    """Returns {student_id: [{"id": course_id, "name": course_name}, ...]}
    Pass student_ids to scope to a subset; omit to load for all students."""
    query = (
        db.query(StudentAdditionalCourseDB.student_id, CourseDB.id, CourseDB.name)
        .join(CourseDB, CourseDB.id == StudentAdditionalCourseDB.course_id)
    )
    if student_ids is not None:
        query = query.filter(StudentAdditionalCourseDB.student_id.in_(student_ids))
    result: dict = {}
    for sid, cid, cname in query.all():
        result.setdefault(sid, []).append({"id": cid, "name": cname})
    return result

def _student_dict_from_map(s, ac_map: dict):
    """Build a student dict using a pre-built additional-courses map (no extra DB call)."""
    return {
        "id": s.id,
        "student_code": s.student_code,
        "name": s.name,
        "father_name": s.father_name,
        "dob": str(s.dob) if s.dob else None,
        "email": s.email,
        "phone": s.phone,
        "parent_phone": s.parent_phone,
        "permanent_address": s.permanent_address,
        "local_address": s.local_address,
        "course": s.course,
        "fees": s.fees,
        "school_college_name": s.school_college_name,
        "medium": s.medium,
        "admission_date": str(s.admission_date) if s.admission_date else None,
        "photo": s.photo,
        "additional_courses": ac_map.get(s.id, []),
    }

def _student_dict(s, db: Session):
    """Return a student as a dict including additional_courses (single-student use only)."""
    return _student_dict_from_map(s, {s.id: _get_additional_courses(s.id, db)})

@app.get("/students")
def get_students(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    students = db.query(StudentDB).all()
    if not students:
        return {"students": []}
    ac_map = _build_additional_courses_map(db, [s.id for s in students])
    return {"students": [_student_dict_from_map(s, ac_map) for s in students]}


@app.get("/students/search")
def search_students(
    q: str = "",
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles(["admin", "teacher"]))
):
    """Search students by name, student_code, email, or phone. Returns up to 20 matches."""
    q = q.strip()
    if not q:
        return {"students": []}
    like = f"%{q}%"
    results = (
        db.query(StudentDB)
        .filter(
            (func.lower(StudentDB.name).contains(func.lower(q))) |
            (StudentDB.student_code.ilike(like)) |
            (StudentDB.email.ilike(like)) |
            (StudentDB.phone.ilike(like)) |
            (StudentDB.parent_phone.ilike(like))
        )
        .limit(20)
        .all()
    )
    return {"students": [
        {
            "id": s.id,
            "name": s.name,
            "phone": s.phone,
            "student_code": s.student_code,
            "course": s.course,
            "email": s.email,
        }
        for s in results
    ]}


@app.get("/student/{student_id}")
def get_student(student_id: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    student = db.query(StudentDB).filter(StudentDB.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return _student_dict(student, db)


@app.get("/student/{student_id}/additional-courses")
def get_additional_courses(
    student_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    return {"additional_courses": _get_additional_courses(student_id, db)}


@app.put("/student/{student_id}/additional-courses")
def set_additional_courses(
    student_id: int,
    data: StudentAdditionalCoursesUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    student = db.query(StudentDB).filter(StudentDB.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    # Remove existing additional courses for this student
    db.query(StudentAdditionalCourseDB).filter(
        StudentAdditionalCourseDB.student_id == student_id
    ).delete(synchronize_session=False)

    # Add new ones (skip primary course)
    seen = set()
    for course_id in data.course_ids:
        if course_id in seen:
            continue
        seen.add(course_id)
        course = db.query(CourseDB).filter(CourseDB.id == course_id).first()
        if course and course.name != student.course:
            db.add(StudentAdditionalCourseDB(student_id=student_id, course_id=course_id))

    db.commit()
    return {"message": "Additional courses updated", "additional_courses": _get_additional_courses(student_id, db)}


@app.put("/update_student/{student_id}")
def update_student(
    student_id: int,
    updated_data: Student,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles(["admin", "staff"]))
):
    student = db.query(StudentDB).filter(StudentDB.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    student.name = updated_data.name
    student.father_name = updated_data.father_name
    student.dob = updated_data.dob
    student.email = updated_data.email
    student.phone = updated_data.phone
    student.parent_phone = updated_data.parent_phone
    student.permanent_address = updated_data.permanent_address
    student.local_address = updated_data.local_address
    student.course = updated_data.course
    student.fees = updated_data.fees
    student.school_college_name = updated_data.school_college_name
    student.medium = updated_data.medium
    student.admission_date = updated_data.admission_date
    if updated_data.photo:
        student.photo = updated_data.photo
    student.address = updated_data.permanent_address

    if updated_data.fees is not None:
        fee_record = db.query(FeesDB).filter(FeesDB.student_id == student_id).first()
        if fee_record:
            fee_record.amount = updated_data.fees
        else:
            db.add(FeesDB(
                student_id=student_id,
                amount=updated_data.fees,
                paid=0.0, description="Updated Fees"
            ))

    db.commit()
    db.refresh(student)
    return {"message": "Student updated", "student": student}


@app.put("/students/bulk-update-course")
def bulk_update_course(
    data: BulkUpdateCourse,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    updated = 0
    for sid in data.student_ids:
        student = db.query(StudentDB).filter(StudentDB.id == sid).first()
        if student:
            student.course = data.course
            updated += 1
    db.commit()
    return {"message": f"{updated} student(s) updated", "updated": updated}


@app.delete("/delete_student/{student_id}")
def delete_student(
    student_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    student = db.query(StudentDB).filter(StudentDB.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    try:
        log_audit(db, user["username"], "DELETE", "Student", student_id, student.name)
    except Exception as e:
        print(f"Audit log error: {e}")

    # Delete in FK-safe order: child rows first, then parent rows
    # 1. fee_payments references fees → must go before fees
    fee_ids = [f.id for f in db.query(FeesDB.id).filter(FeesDB.student_id == student_id).all()]
    if fee_ids:
        db.query(FeePaymentDB).filter(FeePaymentDB.fee_id.in_(fee_ids)).delete(synchronize_session=False)
    db.query(FeesDB).filter(FeesDB.student_id == student_id).delete(synchronize_session=False)
    # 2. Other child tables
    db.query(AttendanceDB).filter(AttendanceDB.student_id == student_id).delete(synchronize_session=False)
    db.query(GradeDB).filter(GradeDB.student_id == student_id).delete(synchronize_session=False)
    db.query(UserDB).filter(UserDB.student_id == student_id).delete(synchronize_session=False)
    # 3. Finally delete the student
    db.delete(student)
    db.commit()
    return {"message": "Student and all related data deleted"}


@app.post("/import_students")
def import_students(
    data: StudentBulk,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles(["admin", "staff"]))
):
    imported = 0
    skipped_duplicate = 0
    skipped_error = 0

    for student in data.students:
        try:
            # ── Normalise fields ──────────────────────────────────
            # Treat empty strings as None so the unique constraint on
            # email doesn't fire for students that have no email at all
            email = student.email.strip().lower() if student.email and student.email.strip() else None
            phone = student.phone.strip() if student.phone else None

            # ── Duplicate check ───────────────────────────────────
            # Base: name (case-insensitive) + phone must both match.
            # If the incoming row also has dob / email, those must
            # match too — the more fields provided, the more precise
            # the match, reducing false positives.
            dup_query = db.query(StudentDB).filter(
                func.lower(StudentDB.name) == student.name.strip().lower(),
                StudentDB.phone == phone
            )
            if student.dob:
                dup_query = dup_query.filter(StudentDB.dob == student.dob)
            if email:
                dup_query = dup_query.filter(StudentDB.email == email)

            if dup_query.first():
                skipped_duplicate += 1
                continue

            # Standalone email uniqueness — if a *different* student
            # already owns this email address, reject to keep email unique.
            if email and db.query(StudentDB).filter(StudentDB.email == email).first():
                skipped_duplicate += 1
                continue

            # ── Insert ────────────────────────────────────────────
            new_student = StudentDB(
                name=student.name,
                father_name=student.father_name,
                dob=student.dob,
                email=email,           # normalised (None if empty)
                phone=phone,
                parent_phone=student.parent_phone,
                permanent_address=student.permanent_address,
                local_address=student.local_address,
                course=student.course,
                fees=student.fees,
                school_college_name=student.school_college_name,
                medium=student.medium,
                admission_date=student.admission_date,
                photo=student.photo,
            )
            db.add(new_student)
            db.flush()
            new_student.student_code = f"STU{new_student.id:04d}"

            if student.fees:
                paid_amount = float(student.fees_paid) if student.fees_paid and student.fees_paid > 0 else 0.0
                # Clamp paid amount so it never exceeds total fees
                paid_amount = min(paid_amount, float(student.fees))

                fee_record = FeesDB(
                    student_id=new_student.id,
                    amount=student.fees,
                    paid=paid_amount,
                    description="Imported Fees"
                )
                db.add(fee_record)
                db.flush()  # get fee_record.id

                # Record the paid amount as a payment history entry
                if paid_amount > 0:
                    payment_date = student.admission_date if student.admission_date else date.today()
                    db.add(FeePaymentDB(
                        fee_id=fee_record.id,
                        amount=paid_amount,
                        paid_date=payment_date,
                        note="Imported payment"
                    ))

            # Commit each student individually so one failure never
            # rolls back students that were already successfully saved
            db.commit()
            imported += 1

        except Exception:
            db.rollback()   # only rolls back this one student
            skipped_error += 1

    return {
        "message": f"{imported} imported, {skipped_duplicate} duplicate(s) skipped, {skipped_error} error(s)",
        "imported": imported,
        "skipped": skipped_duplicate,
        "skipped_errors": skipped_error,
    }

# ----------------------------------------------------------------------------------------------------
# ATTENDANCE

@app.get("/attendance/summary/{student_id}")
def attendance_summary(
    student_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    records = db.query(AttendanceDB).filter(AttendanceDB.student_id == student_id).all()
    total = len(records)
    present = sum(1 for r in records if r.status == "present")
    return {
        "student_id": student_id,
        "total_classes": total,
        "present": present,
        "attendance_percentage": round((present / total * 100) if total > 0 else 0, 2)
    }


@app.post("/attendance/check")
def check_attendance_bulk(
    data: AttendanceCheckRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles(["admin", "teacher", "staff"]))
):
    """Return existing attendance records for a list of students on a given date."""
    if not data.student_ids:
        return {"records": []}
    records = db.query(AttendanceDB).filter(
        AttendanceDB.date == data.date,
        AttendanceDB.student_id.in_(data.student_ids)
    ).all()
    return {"records": [
        {"id": r.id, "student_id": r.student_id, "status": r.status}
        for r in records
    ]}


@app.post("/mark_attendance")
def mark_attendance(
    attendance: AttendanceCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles(["admin", "teacher", "staff"]))
):
    if not db.query(StudentDB).filter(StudentDB.id == attendance.student_id).first():
        raise HTTPException(status_code=404, detail="Student not found")

    existing = db.query(AttendanceDB).filter(
        AttendanceDB.student_id == attendance.student_id,
        AttendanceDB.date == attendance.date,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Attendance already marked for this date")

    db.add(AttendanceDB(
        student_id=attendance.student_id,
        date=attendance.date,
        status=attendance.status,
    ))
    db.commit()
    return {"message": "Attendance marked"}


@app.get("/attendance")
def get_attendance(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    if user["role"] not in ["admin", "teacher"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return {"attendance": db.query(AttendanceDB).all()}


@app.get("/attendance/{student_id}")
def get_student_attendance(
    student_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    if user["role"] == "student":
        db_user = db.query(UserDB).filter(UserDB.username == user["username"]).first()
        if not db_user or db_user.student_id != student_id:
            raise HTTPException(status_code=403, detail="Access denied")

    query = db.query(AttendanceDB).filter(AttendanceDB.student_id == student_id)
    if start_date:
        query = query.filter(AttendanceDB.date >= start_date)
    if end_date:
        query = query.filter(AttendanceDB.date <= end_date)
    return {"attendance": query.order_by(AttendanceDB.date.desc()).all()}


@app.post("/mark_attendance_bulk")
async def mark_attendance_bulk(
    records: List[AttendanceCreate],
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles(["admin", "teacher", "staff"]))
):
    """
    Mark or update attendance for multiple students.

    - One record per (student, date) — subject is no longer tracked.
    - SMS is sent ONLY on the very first mark for a student on a given date.
      Subsequent edits/updates never trigger an SMS.
    - The frontend is expected to send only changed records (new + edited),
      so unchanged already-marked students are never sent here.
    """
    if not records:
        return {"message": "No records provided", "marked": 0, "updated": 0, "sms_sent": 0, "sms_failed": 0}

    print(f"📋 Bulk attendance: {len(records)} records")

    # Batch-load existing records to avoid N+1
    student_ids = [r.student_id for r in records]
    date_val    = records[0].date
    existing_map = {
        row.student_id: row
        for row in db.query(AttendanceDB).filter(
            AttendanceDB.date == date_val,
            AttendanceDB.student_id.in_(student_ids)
        ).all()
    }

    marked = updated = sms_sent = sms_failed = 0
    students_to_sms: list[StudentDB] = []   # only first-time marks get SMS

    for record in records:
        existing = existing_map.get(record.student_id)
        if existing:
            # Update existing record — no SMS
            existing.status = record.status
            updated += 1
        else:
            # New record — queue SMS
            db.add(AttendanceDB(
                student_id=record.student_id,
                date=record.date,
                status=record.status,
            ))
            marked += 1
            # Collect student for SMS (batch query after commit)
            students_to_sms.append(record)

    db.commit()

    # Send SMS only for newly-inserted records
    if students_to_sms:
        new_ids = [r.student_id for r in students_to_sms]
        student_objs = {
            s.id: s for s in db.query(StudentDB).filter(StudentDB.id.in_(new_ids)).all()
        }
        for record in students_to_sms:
            student = student_objs.get(record.student_id)
            if student and student.parent_phone:
                # Template variables (pipe-separated) matching approved DLT template:
                # {#VAR#1} = student name | {#VAR#2} = status | {#VAR#3} = date
                status_str       = "PRESENT" if record.status == "present" else "ABSENT"
                variables_values = f"{student.name}|{status_str}|{record.date}"
                sent = await send_sms(student.parent_phone, variables_values)
                if sent:
                    sms_sent += 1
                else:
                    sms_failed += 1

    return {
        "message": "Attendance saved",
        "marked": marked,
        "updated": updated,
        "sms_sent": sms_sent,
        "sms_failed": sms_failed,
    }
# ----------------------------------------------------------------------------------------------------
# FEES

@app.post("/add_fees")
def add_fees(
    fees: FeesCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    if not db.query(StudentDB).filter(StudentDB.id == fees.student_id).first():
        raise HTTPException(status_code=404, detail="Student not found")

    new_fee = FeesDB(
        student_id=fees.student_id,
        amount=fees.amount, paid=0.0,
        description=fees.description,
        due_date=fees.due_date
    )
    db.add(new_fee)
    db.commit()
    db.refresh(new_fee)
    try:
        log_audit(db, user["username"], "CREATE", "Fee", new_fee.id, f"Rs.{fees.amount} for student {fees.student_id}")
    except Exception as e:
        print(f"Audit log error: {e}")
    return {"message": "Fees added", "data": new_fee}


@app.get("/fees/summary/{student_id}")
def fee_summary(
    student_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    fees = db.query(FeesDB).filter(FeesDB.student_id == student_id).all()
    total = sum(f.amount for f in fees)
    paid = sum(f.paid for f in fees)
    return {
        "student_id": student_id,
        "total_fees": total,
        "paid": paid,
        "pending": total - paid
    }


@app.get("/fees/{student_id}")
def get_student_fees(
    student_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    if user["role"] == "student":
        db_user = db.query(UserDB).filter(UserDB.username == user["username"]).first()
        if not db_user or db_user.student_id != student_id:
            raise HTTPException(status_code=403, detail="Access denied")

    return {"fees": db.query(FeesDB).filter(FeesDB.student_id == student_id).all()}


@app.put("/pay_fees/{fee_id}")
def pay_fees(
    fee_id: int,
    payment: FeesPayment,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    fee = db.query(FeesDB).filter(FeesDB.id == fee_id).first()
    if not fee:
        raise HTTPException(status_code=404, detail="Fee record not found")

    pending = fee.amount - fee.paid

    if payment.pay_amount <= 0:
        raise HTTPException(status_code=400, detail="Payment amount must be greater than 0")

    if payment.pay_amount > pending:
        raise HTTPException(status_code=400, detail=f"Payment exceeds pending amount of ₹{pending}")

    fee.paid += payment.pay_amount
    db.add(FeePaymentDB(
        fee_id=fee_id,
        amount=payment.pay_amount,
        paid_date=payment.paid_date or date.today(),
        note=payment.note,
        payment_mode=payment.payment_mode
    ))
    db.commit()
    db.refresh(fee)
    return {"message": "Payment updated", "data": fee, "remaining": fee.amount - fee.paid}

@app.get("/fee_payments/{fee_id}")
def get_fee_payments(
    fee_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    fee = db.query(FeesDB).filter(FeesDB.id == fee_id).first()
    if not fee:
        raise HTTPException(status_code=404, detail="Fee record not found")
    # Students can only view their own payments
    if user["role"] == "student":
        db_user = db.query(UserDB).filter(UserDB.username == user["username"]).first()
        if not db_user or db_user.student_id != fee.student_id:
            raise HTTPException(status_code=403, detail="Access denied")
    payments = db.query(FeePaymentDB).filter(FeePaymentDB.fee_id == fee_id).order_by(FeePaymentDB.paid_date.desc()).all()
    return {"payments": payments}


@app.put("/fees/record/{fee_id}")
def update_fee_record(
    fee_id: int,
    data: FeesUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    fee = db.query(FeesDB).filter(FeesDB.id == fee_id).first()
    if not fee:
        raise HTTPException(status_code=404, detail="Fee record not found")
    if data.amount is not None:
        if data.amount < 0:
            raise HTTPException(status_code=400, detail="Amount cannot be negative")
        if data.amount < fee.paid:
            raise HTTPException(
                status_code=400,
                detail=f"Amount cannot be less than already paid amount (Rs. {fee.paid:.2f})"
            )
        fee.amount = data.amount
    if data.description is not None:
        fee.description = data.description
    if data.due_date is not None:
        fee.due_date = data.due_date
    db.commit()
    db.refresh(fee)
    return {"message": "Fee record updated", "data": fee}


@app.delete("/fees/record/{fee_id}")
def delete_fee_record(
    fee_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    fee = db.query(FeesDB).filter(FeesDB.id == fee_id).first()
    if not fee:
        raise HTTPException(status_code=404, detail="Fee record not found")
    try:
        log_audit(db, user["username"], "DELETE", "Fee", fee_id, f"Rs.{fee.amount}")
    except Exception as e:
        print(f"Audit log error: {e}")
    # Delete child payments first to avoid FK violation
    db.query(FeePaymentDB).filter(FeePaymentDB.fee_id == fee_id).delete(synchronize_session=False)
    db.delete(fee)
    db.commit()
    return {"message": "Fee record and all its payments deleted"}


# ----------------------------------------------------------------------------------------------------
# TEACHERS

@app.post("/add_teacher")
def add_teacher(
    teacher: TeacherCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    if db.query(TeacherDB).filter(TeacherDB.email == teacher.email).first():
        raise HTTPException(status_code=400, detail="Email already exists")

    new_teacher = TeacherDB(
        name=teacher.name, email=teacher.email,
        subject=teacher.subject or "", phone=teacher.phone
    )
    db.add(new_teacher)
    db.commit()
    db.refresh(new_teacher)
    try:
        log_audit(db, user["username"], "CREATE", "Teacher", new_teacher.id, teacher.name)
    except Exception as e:
        print(f"Audit log error: {e}")
    return {"message": "Teacher added", "data": new_teacher}


def _build_teacher_subjects_map(db: Session) -> dict:
    """Returns {teacher_id: [{"id", "name", "course_id", "course_name"}, ...]}
    Loads all subject→course data in two queries instead of N*M."""
    rows = (
        db.query(SubjectDB.teacher_id, SubjectDB.id, SubjectDB.name, SubjectDB.course_id, CourseDB.name)
        .join(CourseDB, CourseDB.id == SubjectDB.course_id, isouter=True)
        .filter(SubjectDB.teacher_id != None)
        .all()
    )
    result: dict = {}
    for teacher_id, subj_id, subj_name, course_id, course_name in rows:
        if teacher_id:
            result.setdefault(teacher_id, []).append({
                "id": subj_id,
                "name": subj_name,
                "course_id": course_id,
                "course_name": course_name,
            })
    return result

@app.get("/teachers")
def get_teachers(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    teachers = db.query(TeacherDB).all()
    subjects_map = _build_teacher_subjects_map(db)
    result = [
        {
            "id": t.id,
            "name": t.name,
            "email": t.email,
            "subject": t.subject or "",
            "phone": t.phone,
            "subjects": subjects_map.get(t.id, []),
        }
        for t in teachers
    ]
    return {"teachers": result}


@app.put("/update_teacher/{teacher_id}")
def update_teacher(
    teacher_id: int,
    updated: TeacherCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    teacher = db.query(TeacherDB).filter(TeacherDB.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")

    teacher.name = updated.name
    teacher.email = updated.email
    teacher.subject = updated.subject or ""
    teacher.phone = updated.phone
    db.commit()
    db.refresh(teacher)
    return {"message": "Teacher updated", "data": teacher}


@app.delete("/delete_teacher/{teacher_id}")
def delete_teacher(
    teacher_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    teacher = db.query(TeacherDB).filter(TeacherDB.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")

    try:
        log_audit(db, user["username"], "DELETE", "Teacher", teacher_id, teacher.name)
    except Exception as e:
        print(f"Audit log error: {e}")

    # ✅ Also delete teacher's user account
    db.query(UserDB).filter(UserDB.teacher_id == teacher_id).delete()
    db.delete(teacher)
    db.commit()
    return {"message": "Teacher deleted"}


@app.post("/create_teacher_login")
def create_teacher_login(
    data: TeacherUserCreate,
    db: Session = Depends(get_db),
    current: dict = Depends(require_role("admin"))
):
    teacher = db.query(TeacherDB).filter(TeacherDB.id == data.teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")

    if db.query(UserDB).filter(UserDB.teacher_id == data.teacher_id).first():
        raise HTTPException(status_code=400, detail="Teacher already has an account")

    if db.query(UserDB).filter(UserDB.username == data.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")

    db.add(UserDB(
        username=data.username,
        password=hash_password(data.password),
        role="teacher",
        teacher_id=data.teacher_id,
        student_id=None
    ))
    db.commit()
    return {"message": f"Teacher login created for {teacher.name}"}


# ── Get teacher credentials (username only) ────────────────────────────────
@app.get("/teacher/{teacher_id}/credentials")
def get_teacher_credentials(
    teacher_id: int,
    db: Session = Depends(get_db),
    current: dict = Depends(require_role("admin"))
):
    user = db.query(UserDB).filter(UserDB.teacher_id == teacher_id).first()
    if not user:
        return {"has_login": False, "username": None}
    return {"has_login": True, "username": user.username}


class BulkFeeCreate(BaseModel):
    course_name: str
    amount: float
    description: Optional[str] = None
    due_date: Optional[date] = None

class FeeTemplateCreate(BaseModel):
    name: str
    course_id: Optional[int] = None
    amount: float
    description: Optional[str] = None

class ExamScheduleCreate(BaseModel):
    course_id: int
    title: str
    subject: str
    exam_date: date
    exam_time: Optional[str] = None
    duration: Optional[str] = None
    syllabus: Optional[str] = None
    total_marks: Optional[float] = None


# ── Update (or create) teacher login ──────────────────────────────────────
@app.put("/teacher/{teacher_id}/credentials")
def update_teacher_credentials(
    teacher_id: int,
    data: CredentialsUpdate,
    db: Session = Depends(get_db),
    current: dict = Depends(require_role("admin"))
):
    teacher = db.query(TeacherDB).filter(TeacherDB.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")

    # Check username uniqueness (exclude current user)
    existing = db.query(UserDB).filter(
        UserDB.username == data.username,
        UserDB.teacher_id != teacher_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already taken by another account")

    user = db.query(UserDB).filter(UserDB.teacher_id == teacher_id).first()
    if user:
        user.username = data.username
        if data.password:
            user.password = hash_password(data.password)
    else:
        if not data.password:
            raise HTTPException(status_code=400, detail="Password required to create new login")
        db.add(UserDB(
            username=data.username,
            password=hash_password(data.password),
            role="teacher",
            teacher_id=teacher_id,
            student_id=None
        ))
    db.commit()
    return {"message": f"Login updated for {teacher.name}"}


# ── Get student credentials (username only) ───────────────────────────────
@app.get("/student/{student_id}/credentials")
def get_student_credentials(
    student_id: int,
    db: Session = Depends(get_db),
    current: dict = Depends(require_role("admin"))
):
    user = db.query(UserDB).filter(UserDB.student_id == student_id).first()
    if not user:
        return {"has_login": False, "username": None}
    return {"has_login": True, "username": user.username}


# ── Update (or create) student login ──────────────────────────────────────
@app.put("/student/{student_id}/credentials")
def update_student_credentials(
    student_id: int,
    data: CredentialsUpdate,
    db: Session = Depends(get_db),
    current: dict = Depends(require_role("admin"))
):
    student = db.query(StudentDB).filter(StudentDB.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    existing = db.query(UserDB).filter(
        UserDB.username == data.username,
        UserDB.student_id != student_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already taken by another account")

    user = db.query(UserDB).filter(UserDB.student_id == student_id).first()
    if user:
        user.username = data.username
        if data.password:
            user.password = hash_password(data.password)
    else:
        if not data.password:
            raise HTTPException(status_code=400, detail="Password required to create new login")
        db.add(UserDB(
            username=data.username,
            password=hash_password(data.password),
            role="student",
            student_id=student_id,
            teacher_id=None
        ))
    db.commit()
    return {"message": f"Login updated for {student.name}"}


@app.get("/students/course/{course}")
def get_students_by_course(
    course: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles(["admin", "teacher"]))
):
    # Primary students
    primary = db.query(StudentDB).filter(StudentDB.course == course).all()
    primary_ids = {s.id for s in primary}

    # Students who have this course as an additional course (batch load)
    additional: list = []
    course_obj = db.query(CourseDB).filter(CourseDB.name == course).first()
    if course_obj:
        add_rows = db.query(StudentAdditionalCourseDB).filter(
            StudentAdditionalCourseDB.course_id == course_obj.id
        ).all()
        add_ids = [r.student_id for r in add_rows if r.student_id not in primary_ids]
        if add_ids:
            additional = db.query(StudentDB).filter(StudentDB.id.in_(add_ids)).all()

    all_students = primary + additional
    if not all_students:
        return {"students": []}

    # Single batch query for all additional-course data
    ac_map = _build_additional_courses_map(db, [s.id for s in all_students])

    result = []
    for s in primary:
        d = _student_dict_from_map(s, ac_map)
        d["is_additional"] = False
        result.append(d)
    for s in additional:
        d = _student_dict_from_map(s, ac_map)
        d["is_additional"] = True
        result.append(d)
    return {"students": result}


@app.get("/teacher/me")
def get_teacher_me(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    if user["role"] != "teacher":
        raise HTTPException(status_code=403, detail="Access denied")
    db_user = db.query(UserDB).filter(UserDB.username == user["username"]).first()
    teacher = db.query(TeacherDB).filter(TeacherDB.id == db_user.teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher profile not found")

    # Include assigned subjects in one join query
    rows = (
        db.query(SubjectDB.id, SubjectDB.name, SubjectDB.course_id, CourseDB.name)
        .join(CourseDB, CourseDB.id == SubjectDB.course_id, isouter=True)
        .filter(SubjectDB.teacher_id == teacher.id)
        .all()
    )
    subjects = [{"id": sid, "name": sname, "course_id": cid, "course_name": cname} for sid, sname, cid, cname in rows]

    return {
        "id": teacher.id,
        "name": teacher.name,
        "email": teacher.email,
        "subject": teacher.subject or "",
        "phone": teacher.phone,
        "subjects": subjects,
    }


@app.get("/subjects/teacher/{teacher_id}")
def get_subjects_by_teacher(
    teacher_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    rows = (
        db.query(SubjectDB.id, SubjectDB.name, SubjectDB.course_id, CourseDB.name)
        .join(CourseDB, CourseDB.id == SubjectDB.course_id, isouter=True)
        .filter(SubjectDB.teacher_id == teacher_id)
        .all()
    )
    return {"subjects": [
        {"id": sid, "name": sname, "course_id": cid, "course_name": cname}
        for sid, sname, cid, cname in rows
    ]}


@app.put("/teachers/{teacher_id}/subjects")
def assign_subjects_to_teacher(
    teacher_id: int,
    data: TeacherSubjectsUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    teacher = db.query(TeacherDB).filter(TeacherDB.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")

    # Unassign all subjects currently linked to this teacher
    db.query(SubjectDB).filter(SubjectDB.teacher_id == teacher_id).update(
        {"teacher_id": None}, synchronize_session=False
    )

    # Assign the selected subjects
    if data.subject_ids:
        db.query(SubjectDB).filter(SubjectDB.id.in_(data.subject_ids)).update(
            {"teacher_id": teacher_id}, synchronize_session=False
        )

    db.commit()
    assigned_count = db.query(SubjectDB).filter(SubjectDB.teacher_id == teacher_id).count()
    return {"message": "Subjects assigned successfully", "count": assigned_count}

# ----------------------------------------------------------------------------------------------------
# GRADES

@app.post("/add_grade")
def add_grade(
    grade: GradeCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles(["admin", "teacher", "staff"]))
):
    if not db.query(StudentDB).filter(StudentDB.id == grade.student_id).first():
        raise HTTPException(status_code=404, detail="Student not found")

    percentage = (grade.marks / grade.total_marks) * 100
    g = "A+" if percentage >= 90 else "A" if percentage >= 80 else "B" if percentage >= 70 else "C" if percentage >= 60 else "D" if percentage >= 50 else "F"

    new_grade = GradeDB(
        student_id=grade.student_id,
        subject=grade.subject,
        marks=grade.marks,
        total_marks=grade.total_marks,
        grade=g,
        test_title=grade.test_title
    )
    db.add(new_grade)
    db.commit()
    db.refresh(new_grade)
    return {"message": "Grade added", "data": new_grade}


@app.get("/grades/{student_id}")
def get_grades(
    student_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    # Students can only see their own grades
    if user["role"] == "student":
        db_user = db.query(UserDB).filter(UserDB.username == user["username"]).first()
        if not db_user or db_user.student_id != student_id:
            raise HTTPException(status_code=403, detail="Access denied")

    return {"grades": db.query(GradeDB).filter(GradeDB.student_id == student_id).all()}


@app.delete("/delete_grade/{grade_id}")
def delete_grade(
    grade_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles(["admin", "teacher", "staff"]))
):
    grade = db.query(GradeDB).filter(GradeDB.id == grade_id).first()
    if not grade:
        raise HTTPException(status_code=404, detail="Grade not found")
    db.delete(grade)
    db.commit()
    return {"message": "Grade deleted"}

# ----------------------------------------------------------------------------------------------------
# TIMETABLE

@app.post("/add_timetable")
def add_timetable(
    entry: TimetableCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    new_entry = TimetableDB(
        course_id=entry.course_id,
        day=entry.day, subject=entry.subject,
        teacher=entry.teacher, time_slot=entry.time_slot
    )
    db.add(new_entry)
    db.commit()
    db.refresh(new_entry)
    return {"message": "Timetable entry added", "data": new_entry}


@app.get("/timetable/course/{course_id}")
def get_timetable_by_course(
    course_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    return {"timetable": db.query(TimetableDB).filter(TimetableDB.course_id == course_id).all()}


@app.get("/timetable")
def get_timetable(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return {"timetable": db.query(TimetableDB).all()}


@app.delete("/delete_timetable/{entry_id}")
def delete_timetable(
    entry_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    entry = db.query(TimetableDB).filter(TimetableDB.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    db.delete(entry)
    db.commit()
    return {"message": "Timetable entry deleted"}

# ----------------------------------------------------------------------------------------------------
# NOTICES

@app.post("/add_notice")
def add_notice(
    data: NoticeCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles(["admin", "staff"]))
):
    new_notice = NoticeDB(
        title=data.title,
        content=data.content,
        date=data.date or date.today(),
        course=data.course,
    )
    db.add(new_notice)
    db.commit()
    db.refresh(new_notice)
    try:
        log_audit(db, user["username"], "CREATE", "Notice", new_notice.id, f"Title: {data.title}")
    except Exception as e:
        print(f"Audit log error: {e}")
    return {"message": "Notice added", "data": {
        "id": new_notice.id,
        "title": new_notice.title,
        "content": new_notice.content,
        "date": str(new_notice.date),
        "course": new_notice.course,
    }}


@app.get("/notices")
def get_notices(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    q = db.query(NoticeDB)
    if user["role"] == "student":
        # Students see notices that are global (course IS NULL) or targeted at their course
        student_course = None
        if user.get("student_id"):
            student = db.query(StudentDB).filter(StudentDB.id == user["student_id"]).first()
            if student:
                student_course = student.course
        if student_course:
            q = q.filter(
                (NoticeDB.course == None) | (NoticeDB.course == student_course)
            )
        else:
            q = q.filter(NoticeDB.course == None)
    notices = q.order_by(NoticeDB.date.desc()).all()

    # Batch load read counts for admin
    read_counts = {}
    if user["role"] == "admin":
        counts = db.query(NoticeReadDB.notice_id, func.count(NoticeReadDB.id).label("cnt")).group_by(NoticeReadDB.notice_id).all()
        read_counts = {c.notice_id: c.cnt for c in counts}

    result = []
    for n in notices:
        item = {"id": n.id, "title": n.title, "content": n.content, "date": str(n.date), "course": n.course, "read_count": read_counts.get(n.id, 0)}
        result.append(item)
    return {"notices": result}


@app.delete("/delete_notice/{notice_id}")
def delete_notice(
    notice_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    notice = db.query(NoticeDB).filter(NoticeDB.id == notice_id).first()
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    db.delete(notice)
    db.commit()
    return {"message": "Notice deleted"}


@app.post("/notices/{notice_id}/read")
def mark_notice_read(notice_id: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    db_user = db.query(UserDB).filter(UserDB.username == user["username"]).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    # Upsert — ignore if already exists
    existing = db.query(NoticeReadDB).filter(NoticeReadDB.notice_id == notice_id, NoticeReadDB.user_id == db_user.id).first()
    if not existing:
        from datetime import datetime
        db.add(NoticeReadDB(notice_id=notice_id, user_id=db_user.id, read_at=datetime.utcnow().isoformat()))
        db.commit()
    return {"message": "Marked as read"}


@app.get("/notices/{notice_id}/reads")
def get_notice_reads(notice_id: int, db: Session = Depends(get_db), user: dict = Depends(require_role("admin"))):
    reads = db.query(NoticeReadDB).filter(NoticeReadDB.notice_id == notice_id).all()
    user_ids = [r.user_id for r in reads]
    users = {u.id: u for u in db.query(UserDB).filter(UserDB.id.in_(user_ids)).all()}
    return {
        "count": len(reads),
        "reads": [{"username": users[r.user_id].username, "role": users[r.user_id].role, "read_at": r.read_at} for r in reads if r.user_id in users]
    }


# ----------------------------------------------------------------------------------------------------
# COURSES

@app.post("/add_course")
def add_course(
    course: CourseCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    if db.query(CourseDB).filter(CourseDB.name == course.name).first():
        raise HTTPException(status_code=400, detail="Course already exists")
    new_course = CourseDB(
        name=course.name,
        description=course.description,
        duration=course.duration,
        fees=course.fees
    )
    db.add(new_course)
    db.commit()
    db.refresh(new_course)
    return {"message": "Course added", "data": new_course}


@app.get("/courses")
def get_courses(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return {"courses": db.query(CourseDB).order_by(CourseDB.name).all()}


@app.put("/update_course/{course_id}")
def update_course(
    course_id: int,
    updated: CourseCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    course = db.query(CourseDB).filter(CourseDB.id == course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    existing = db.query(CourseDB).filter(CourseDB.name == updated.name, CourseDB.id != course_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Another course with this name already exists")
    course.name = updated.name
    course.description = updated.description
    course.duration = updated.duration
    course.fees = updated.fees
    db.commit()
    db.refresh(course)
    return {"message": "Course updated", "data": course}


@app.delete("/delete_course/{course_id}")
def delete_course(
    course_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    course = db.query(CourseDB).filter(CourseDB.id == course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    db.delete(course)
    db.commit()
    return {"message": "Course deleted"}

# ----------------------------------------------------------------------------------------------------
# SUBJECTS

@app.post("/add_subject")
def add_subject(
    subject: SubjectCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    course = db.query(CourseDB).filter(CourseDB.id == subject.course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if db.query(SubjectDB).filter(
        SubjectDB.course_id == subject.course_id,
        SubjectDB.name == subject.name
    ).first():
        raise HTTPException(status_code=400, detail="Subject already exists in this course")
    new_subject = SubjectDB(
        course_id=subject.course_id,
        name=subject.name,
        teacher_id=subject.teacher_id
    )
    db.add(new_subject)
    db.commit()
    db.refresh(new_subject)
    return {"message": "Subject added", "data": new_subject}


@app.get("/subjects")
def get_all_subjects(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    rows = (
        db.query(SubjectDB.id, SubjectDB.name, SubjectDB.course_id, CourseDB.name, SubjectDB.teacher_id)
        .join(CourseDB, CourseDB.id == SubjectDB.course_id, isouter=True)
        .all()
    )
    return {"subjects": [
        {"id": sid, "name": sname, "course_id": cid, "course_name": cname, "teacher_id": tid}
        for sid, sname, cid, cname, tid in rows
    ]}


@app.get("/subjects/course/{course_id}")
def get_subjects_by_course(
    course_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    subjects = db.query(SubjectDB).filter(SubjectDB.course_id == course_id).all()
    result = []
    for s in subjects:
        teacher = db.query(TeacherDB).filter(TeacherDB.id == s.teacher_id).first() if s.teacher_id else None
        result.append({
            "id": s.id,
            "course_id": s.course_id,
            "name": s.name,
            "teacher_id": s.teacher_id,
            "teacher_name": teacher.name if teacher else None
        })
    return {"subjects": result}


@app.put("/update_subject/{subject_id}")
def update_subject(
    subject_id: int,
    updated: SubjectCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    subject = db.query(SubjectDB).filter(SubjectDB.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    subject.name = updated.name
    subject.teacher_id = updated.teacher_id
    db.commit()
    db.refresh(subject)
    return {"message": "Subject updated", "data": subject}


@app.delete("/delete_subject/{subject_id}")
def delete_subject(
    subject_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    subject = db.query(SubjectDB).filter(SubjectDB.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    db.delete(subject)
    db.commit()
    return {"message": "Subject deleted"}


@app.get("/attendance/subject-wise/{student_id}")
def subject_wise_attendance(
    student_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    if user["role"] == "student":
        db_user = db.query(UserDB).filter(UserDB.username == user["username"]).first()
        if not db_user or db_user.student_id != student_id:
            raise HTTPException(status_code=403, detail="Access denied")

    records = db.query(AttendanceDB).filter(AttendanceDB.student_id == student_id).all()

    subject_map = {}
    for r in records:
        key = r.subject_id
        if key not in subject_map:
            subj = db.query(SubjectDB).filter(SubjectDB.id == key).first() if key else None
            subject_map[key] = {
                "subject_id": key,
                "subject_name": subj.name if subj else "General",
                "total": 0,
                "present": 0
            }
        subject_map[key]["total"] += 1
        if r.status == "present":
            subject_map[key]["present"] += 1

    result = []
    for s in subject_map.values():
        s["percentage"] = round((s["present"] / s["total"]) * 100, 1) if s["total"] > 0 else 0
        result.append(s)

    return {"subjects": result}


@app.get("/attendance/heatmap/{student_id}")
def get_attendance_heatmap(
    student_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    # Students can only view their own
    if user["role"] == "student":
        db_user = db.query(UserDB).filter(UserDB.username == user["username"]).first()
        if not db_user or db_user.student_id != student_id:
            raise HTTPException(status_code=403, detail="Access denied")

    # Last 90 days
    end_date = date.today()
    start_date = end_date - timedelta(days=90)

    records = db.query(AttendanceDB).filter(
        AttendanceDB.student_id == student_id,
        AttendanceDB.date >= start_date,
        AttendanceDB.date <= end_date
    ).all()

    # Build a dict: "YYYY-MM-DD" -> "present"/"absent"
    # If multiple records for same date (different subjects), prefer "present"
    heatmap = {}
    for r in records:
        key = str(r.date)
        if key not in heatmap or r.status == "present":
            heatmap[key] = r.status

    return {"heatmap": heatmap, "start_date": str(start_date), "end_date": str(end_date)}


# ----------------------------------------------------------------------------------------------------
# BULK FEES

@app.post("/fees/bulk")
def add_fees_bulk(
    data: BulkFeeCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    """Add a fee record to all students enrolled in a course."""
    # Find all students in the course (primary + additional)
    primary = db.query(StudentDB).filter(StudentDB.course == data.course_name).all()
    primary_ids = {s.id for s in primary}

    course_obj = db.query(CourseDB).filter(CourseDB.name == data.course_name).first()
    additional = []
    if course_obj:
        add_rows = db.query(StudentAdditionalCourseDB).filter(
            StudentAdditionalCourseDB.course_id == course_obj.id
        ).all()
        add_ids = [r.student_id for r in add_rows if r.student_id not in primary_ids]
        if add_ids:
            additional = db.query(StudentDB).filter(StudentDB.id.in_(add_ids)).all()

    all_students = list(primary) + additional
    if not all_students:
        raise HTTPException(status_code=404, detail=f"No students found in course '{data.course_name}'")

    count = 0
    for student in all_students:
        db.add(FeesDB(
            student_id=student.id,
            amount=data.amount,
            paid=0.0,
            description=data.description,
            due_date=data.due_date,
        ))
        count += 1

    db.commit()
    try:
        log_audit(db, user["username"], "CREATE", "BulkFee", None,
                  f"Added fee Rs.{data.amount} to {count} students in {data.course_name}")
    except Exception as e:
        print(f"Audit log error: {e}")
    return {"message": f"Fee added to {count} students in {data.course_name}", "count": count}


# ----------------------------------------------------------------------------------------------------
# FEE TEMPLATES

@app.get("/fee-templates")
def get_fee_templates(db: Session = Depends(get_db), user: dict = Depends(require_role("admin"))):
    templates = db.query(FeeTemplateDB).all()
    result = []
    for t in templates:
        course_name = None
        if t.course_id:
            c = db.query(CourseDB).filter(CourseDB.id == t.course_id).first()
            course_name = c.name if c else None
        result.append({
            "id": t.id, "name": t.name, "course_id": t.course_id,
            "course_name": course_name, "amount": t.amount, "description": t.description
        })
    return {"templates": result}


@app.post("/fee-templates")
def create_fee_template(
    data: FeeTemplateCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    t = FeeTemplateDB(name=data.name, course_id=data.course_id, amount=data.amount, description=data.description)
    db.add(t)
    db.commit()
    db.refresh(t)
    try:
        log_audit(db, user["username"], "CREATE", "FeeTemplate", t.id, f"Template: {data.name}")
    except Exception as e:
        print(f"Audit log error: {e}")
    return {"message": "Template created", "id": t.id}


@app.delete("/fee-templates/{template_id}")
def delete_fee_template(
    template_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    t = db.query(FeeTemplateDB).filter(FeeTemplateDB.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    try:
        log_audit(db, user["username"], "DELETE", "FeeTemplate", template_id, f"Template: {t.name}")
    except Exception as e:
        print(f"Audit log error: {e}")
    db.delete(t)
    db.commit()
    return {"message": "Template deleted"}


# ----------------------------------------------------------------------------------------------------
# EXAM SCHEDULE

@app.get("/exam-schedule")
def get_exam_schedule(
    course_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    q = db.query(ExamScheduleDB)
    if course_id:
        q = q.filter(ExamScheduleDB.course_id == course_id)
    exams = q.order_by(ExamScheduleDB.exam_date).all()
    result = []
    for e in exams:
        c = db.query(CourseDB).filter(CourseDB.id == e.course_id).first()
        result.append({
            "id": e.id, "course_id": e.course_id,
            "course_name": c.name if c else None,
            "title": e.title, "subject": e.subject,
            "exam_date": str(e.exam_date), "exam_time": e.exam_time,
            "duration": e.duration, "syllabus": e.syllabus,
            "total_marks": e.total_marks
        })
    return {"exams": result}


@app.post("/exam-schedule")
def create_exam(
    data: ExamScheduleCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    exam = ExamScheduleDB(
        course_id=data.course_id, title=data.title, subject=data.subject,
        exam_date=data.exam_date, exam_time=data.exam_time,
        duration=data.duration, syllabus=data.syllabus, total_marks=data.total_marks
    )
    db.add(exam)
    db.commit()
    db.refresh(exam)
    try:
        log_audit(db, user["username"], "CREATE", "ExamSchedule", exam.id,
                  f"{data.title} - {data.subject} on {data.exam_date}")
    except Exception as e:
        print(f"Audit log error: {e}")
    return {"message": "Exam scheduled", "id": exam.id}


@app.put("/exam-schedule/{exam_id}")
def update_exam(
    exam_id: int,
    data: ExamScheduleCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    exam = db.query(ExamScheduleDB).filter(ExamScheduleDB.id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    for field, value in data.dict().items():
        setattr(exam, field, value)
    db.commit()
    try:
        log_audit(db, user["username"], "UPDATE", "ExamSchedule", exam_id, f"{data.title}")
    except Exception as e:
        print(f"Audit log error: {e}")
    return {"message": "Exam updated"}


@app.delete("/exam-schedule/{exam_id}")
def delete_exam(
    exam_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    exam = db.query(ExamScheduleDB).filter(ExamScheduleDB.id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    try:
        log_audit(db, user["username"], "DELETE", "ExamSchedule", exam_id, f"{exam.title}")
    except Exception as e:
        print(f"Audit log error: {e}")
    db.delete(exam)
    db.commit()
    return {"message": "Exam deleted"}


# ----------------------------------------------------------------------------------------------------
# AUDIT LOGS

@app.get("/audit-logs")
def get_audit_logs(
    limit: int = 100,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    logs = db.query(AuditLogDB).order_by(AuditLogDB.id.desc()).limit(limit).all()
    return {"logs": [
        {
            "id": l.id, "performed_by": l.performed_by, "action": l.action,
            "entity": l.entity, "entity_id": l.entity_id,
            "details": l.details, "timestamp": l.timestamp
        }
        for l in logs
    ]}


# ----------------------------------------------------------------------------------------------------
# DATA EXPORT

@app.get("/export/students")
def export_students(db: Session = Depends(get_db), user: dict = Depends(require_role("admin"))):
    students = db.query(StudentDB).all()
    return {"students": [
        {"ID": s.id, "Student Code": s.student_code, "Name": s.name, "Father Name": s.father_name,
         "DOB": str(s.dob) if s.dob else "", "Email": s.email or "", "Phone": s.phone,
         "Parent Phone": s.parent_phone or "", "Course": s.course or "", "Fees": s.fees or 0,
         "Admission Date": str(s.admission_date) if s.admission_date else "",
         "School/College": s.school_college_name or "", "Medium": s.medium or ""}
        for s in students
    ]}


@app.get("/export/fees")
def export_fees(db: Session = Depends(get_db), user: dict = Depends(require_role("admin"))):
    fees = db.query(FeesDB).all()
    student_ids = list({f.student_id for f in fees})
    students = {s.id: s for s in db.query(StudentDB).filter(StudentDB.id.in_(student_ids)).all()}
    return {"fees": [
        {"Fee ID": f.id, "Student Name": students.get(f.student_id, StudentDB()).name or "",
         "Student Code": students.get(f.student_id, StudentDB()).student_code or "",
         "Course": students.get(f.student_id, StudentDB()).course or "",
         "Amount": f.amount, "Paid": f.paid, "Pending": f.amount - f.paid,
         "Description": f.description or "", "Due Date": str(f.due_date) if f.due_date else "",
         "Status": "Paid" if f.paid >= f.amount else "Pending"}
        for f in fees
    ]}


@app.get("/export/attendance")
def export_attendance(db: Session = Depends(get_db), user: dict = Depends(require_role("admin"))):
    records = db.query(AttendanceDB).order_by(AttendanceDB.date.desc()).limit(10000).all()
    student_ids = list({r.student_id for r in records})
    students = {s.id: s for s in db.query(StudentDB).filter(StudentDB.id.in_(student_ids)).all()}
    return {"attendance": [
        {"Date": str(r.date), "Student Name": students.get(r.student_id, StudentDB()).name or "",
         "Student Code": students.get(r.student_id, StudentDB()).student_code or "",
         "Course": students.get(r.student_id, StudentDB()).course or "",
         "Status": r.status}
        for r in records
    ]}


@app.get("/export/payments")
def export_payments(db: Session = Depends(get_db), user: dict = Depends(require_role("admin"))):
    payments = db.query(FeePaymentDB).order_by(FeePaymentDB.paid_date.desc()).all()
    fee_ids = list({p.fee_id for p in payments})
    fees = {f.id: f for f in db.query(FeesDB).filter(FeesDB.id.in_(fee_ids)).all()}
    student_ids = list({f.student_id for f in fees.values()})
    students = {s.id: s for s in db.query(StudentDB).filter(StudentDB.id.in_(student_ids)).all()}
    return {"payments": [
        {"Payment ID": p.id, "Date": str(p.paid_date),
         "Student Name": students.get(fees[p.fee_id].student_id, StudentDB()).name if p.fee_id in fees else "",
         "Amount Paid": p.amount, "Mode": p.payment_mode or p.note or "—",
         "Description": fees[p.fee_id].description if p.fee_id in fees else "",
         "Fee Total": fees[p.fee_id].amount if p.fee_id in fees else 0}
        for p in payments if p.fee_id in fees
    ]}