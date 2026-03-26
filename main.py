from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session

from database import SessionLocal, engine, Base
from models import StudentDB, UserDB, AttendanceDB, FeesDB, TeacherDB, NoticeDB, GradeDB, TimetableDB
from pydantic import BaseModel
from fastapi import HTTPException
from passlib.context import CryptContext
import os
from datetime import date, datetime, timedelta, timezone   # ✅ FIX 4: added timezone
from jose import JWTError, jwt

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from typing import Literal

# ----------------------------------------------------------------------------------------------------
from fastapi.middleware.cors import CORSMiddleware

# App Creation
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create tables
Base.metadata.create_all(bind=engine)


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


# JWT CONFIG
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


# Create Token function
def create_access_token(data: dict):
    to_encode = data.copy()
    # ✅ FIX 4: use timezone-aware datetime instead of deprecated utcnow()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# ----------------------------------------------------------------------------------------------------

# SECURITY (AUTH)
security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return {
            "username": payload.get("sub"),
            "role": payload.get("role")
        }
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


# require_role
def require_role(required_role: str):
    def role_checker(user: dict = Depends(get_current_user)):
        if user["role"] != required_role:
            raise HTTPException(status_code=403, detail="Access denied")
        return user
    return role_checker


# ----------------------------------------------------------------------------------------------------

# Pydantic models

class UserCreate(BaseModel):
    username: str
    password: str
    student_id: int   # student must provide their ID given by admin


class UserLogin(BaseModel):
    username: str
    password: str


class Student(BaseModel):
    name: str
    age: int
    email: str


class AttendanceCreate(BaseModel):
    student_id: int
    date: date
    status: Literal["present", "absent"]


class FeesCreate(BaseModel):
    student_id: int
    amount: float
    description: str = None

class FeesPayment(BaseModel):
    pay_amount: float

class TeacherCreate(BaseModel):
    name: str
    email: str
    subject: str
    phone: str = None

class GradeCreate(BaseModel):
    student_id: int
    subject: str
    marks: float
    total_marks: float

class TimetableCreate(BaseModel):
    day: str
    subject: str
    teacher: str
    time_slot: str

class NoticeCreate(BaseModel):
    title: str
    content: str
    date: date

# ----------------------------------------------------------------------------------------------------

@app.get("/")
def home():
    return {"message": "ERP with DB running 🚀"}


# ----------------------------------------------------------------------------------------------------

# Authentication APIs

# Register API
@app.post("/register")
def register(user: UserCreate, db: Session = Depends(get_db)):

    # Check if username already exists
    existing_user = db.query(UserDB).filter(UserDB.username == user.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username already exists")

    # Check if student_id exists in students table
    student = db.query(StudentDB).filter(StudentDB.id == user.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student ID not found. Contact admin.")

    # Check if student_id is already linked to another account
    already_linked = db.query(UserDB).filter(UserDB.student_id == user.student_id).first()
    if already_linked:
        raise HTTPException(status_code=400, detail="This Student ID already has an account")

    # Hash password
    hashed_pwd = hash_password(user.password)

    new_user = UserDB(
        username=user.username,
        password=hashed_pwd,
        role="student",
        student_id=user.student_id   # link user to student record
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {"message": f"Account created! Welcome {student.name}"}


# Admin Create schema (no student_id needed)
class AdminCreate(BaseModel):
    username: str
    password: str

# Create Admin API (only existing admin can do this)
@app.post("/create_admin")
def create_admin(
    user: AdminCreate,
    db: Session = Depends(get_db),
    current: dict = Depends(require_role("admin"))   # 🔐 only admin can create admin
):
    existing = db.query(UserDB).filter(UserDB.username == user.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    hashed_pwd = hash_password(user.password)

    new_admin = UserDB(
        username=user.username,
        password=hashed_pwd,
        role="admin",
        student_id=None
    )

    db.add(new_admin)
    db.commit()
    db.refresh(new_admin)

    return {"message": f"Admin '{user.username}' created successfully"}


# Login API
@app.post("/login")
def login(user: UserLogin, db: Session = Depends(get_db)):
    db_user = db.query(UserDB).filter(UserDB.username == user.username).first()

    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    if not verify_password(user.password, db_user.password):
        raise HTTPException(status_code=401, detail="Incorrect password")

    token = create_access_token({
        "sub": db_user.username,
        "role": db_user.role,
        "student_id": db_user.student_id   # included so frontend knows which student this is
    })

    return {
        "access_token": token,
        "token_type": "bearer"
    }


# ----------------------------------------------------------------------------------------------------

# Student APIs

# Add student to DB
@app.post("/add_student")
def add_student(
    student: Student,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    existing = db.query(StudentDB).filter(StudentDB.email == student.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already exists")

    new_student = StudentDB(
        name=student.name,
        age=student.age,
        email=student.email
    )
    db.add(new_student)
    db.commit()
    db.refresh(new_student)
    return {"message": "Student saved in DB", "student": new_student}


# Get all students from DB
@app.get("/students")
def get_students(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    students = db.query(StudentDB).all()
    return {"students": students}


# Get student by id
@app.get("/student/{student_id}")
def get_student(
    student_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    student = db.query(StudentDB).filter(StudentDB.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return student


# Update student
@app.put("/update_student/{student_id}")
def update_student(
    student_id: int,
    updated_data: Student,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    student = db.query(StudentDB).filter(StudentDB.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    student.name = updated_data.name
    student.age = updated_data.age
    student.email = updated_data.email

    db.commit()
    db.refresh(student)
    return {"message": "Student updated", "student": student}


# Delete Student
@app.delete("/delete_student/{student_id}")
def delete_student(
    student_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    student = db.query(StudentDB).filter(StudentDB.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    db.delete(student)
    db.commit()
    return {"message": "Student deleted"}


# ----------------------------------------------------------------------------------------------------

# Attendance APIs

# ✅ FIX 1: /summary/ route placed BEFORE /{student_id} to avoid route conflict
# Attendance percentage summary
@app.get("/attendance/summary/{student_id}")
def attendance_summary(
    student_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    records = db.query(AttendanceDB).filter(AttendanceDB.student_id == student_id).all()

    total = len(records)
    present = sum(1 for r in records if r.status == "present")
    percentage = (present / total * 100) if total > 0 else 0

    return {
        "student_id": student_id,
        "total_classes": total,
        "present": present,
        "attendance_percentage": round(percentage, 2)
    }


# Mark attendance (Admin only)
@app.post("/mark_attendance")
def mark_attendance(
    attendance: AttendanceCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    student = db.query(StudentDB).filter(StudentDB.id == attendance.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    existing = db.query(AttendanceDB).filter(
        AttendanceDB.student_id == attendance.student_id,
        AttendanceDB.date == attendance.date
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Attendance already marked for this date")

    new_record = AttendanceDB(
        student_id=attendance.student_id,
        date=attendance.date,
        status=attendance.status
    )
    db.add(new_record)
    db.commit()
    db.refresh(new_record)
    return {"message": "Attendance marked", "data": new_record}


# View all attendance
@app.get("/attendance")
def get_attendance(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    records = db.query(AttendanceDB).all()
    return {"attendance": records}


# View attendance by student
@app.get("/attendance/{student_id}")
def get_student_attendance(
    student_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    # ✅ FIX 3: student_id now exists on UserDB so this check works correctly
    if user["role"] != "admin":
        db_user = db.query(UserDB).filter(UserDB.username == user["username"]).first()
        if not db_user or db_user.student_id != student_id:
            raise HTTPException(status_code=403, detail="Access denied")

    records = db.query(AttendanceDB).filter(AttendanceDB.student_id == student_id).all()
    return {"attendance": records}


# ----------------------------------------------------------------------------------------------------

# Fees APIs

# ✅ FIX 1: /summary/ route placed BEFORE /{student_id} to avoid route conflict
# Total dues summary
# ----------------------------------------------------------------------------------------------------
# FEES

@app.post("/add_fees")
def add_fees(
    fees: FeesCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    student = db.query(StudentDB).filter(StudentDB.id == fees.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    new_fee = FeesDB(
        student_id=fees.student_id,
        amount=fees.amount,
        paid=0.0,
        description=fees.description
    )
    db.add(new_fee)
    db.commit()
    db.refresh(new_fee)
    return {"message": "Fees added", "data": new_fee}


@app.get("/fees/{student_id}")
def get_student_fees(
    student_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    if user["role"] != "admin":
        db_user = db.query(UserDB).filter(UserDB.username == user["username"]).first()
        if not db_user or db_user.student_id != student_id:
            raise HTTPException(status_code=403, detail="Access denied")

    fees = db.query(FeesDB).filter(FeesDB.student_id == student_id).all()
    return {"fees": fees}


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
    db.commit()
    db.refresh(fee)

    return {
        "message": "Payment updated",
        "data": fee,
        "remaining": fee.amount - fee.paid
    }


@app.get("/fees/summary/{student_id}")
def fee_summary(
    student_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    fees = db.query(FeesDB).filter(FeesDB.student_id == student_id).all()

    total = sum(f.amount for f in fees)
    paid = sum(f.paid for f in fees)
    pending = total - paid

    return {
        "student_id": student_id,
        "total_fees": total,
        "paid": paid,
        "pending": pending
    }

# ----------------------------------------------------------------------------------------------------
# TEACHERS

@app.post("/add_teacher")
def add_teacher(
    teacher: TeacherCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    existing = db.query(TeacherDB).filter(TeacherDB.email == teacher.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already exists")

    new_teacher = TeacherDB(
        name=teacher.name,
        email=teacher.email,
        subject=teacher.subject,
        phone=teacher.phone
    )
    db.add(new_teacher)
    db.commit()
    db.refresh(new_teacher)
    return {"message": "Teacher added", "data": new_teacher}


@app.get("/teachers")
def get_teachers(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    teachers = db.query(TeacherDB).all()
    return {"teachers": teachers}


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
    teacher.subject = updated.subject
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

    db.delete(teacher)
    db.commit()
    return {"message": "Teacher deleted"}


# ----------------------------------------------------------------------------------------------------
# GRADES

@app.post("/add_grade")
def add_grade(
    grade: GradeCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    student = db.query(StudentDB).filter(StudentDB.id == grade.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    # Auto calculate grade
    percentage = (grade.marks / grade.total_marks) * 100
    if percentage >= 90:
        g = "A+"
    elif percentage >= 80:
        g = "A"
    elif percentage >= 70:
        g = "B"
    elif percentage >= 60:
        g = "C"
    elif percentage >= 50:
        g = "D"
    else:
        g = "F"

    new_grade = GradeDB(
        student_id=grade.student_id,
        subject=grade.subject,
        marks=grade.marks,
        total_marks=grade.total_marks,
        grade=g
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
    if user["role"] != "admin":
        db_user = db.query(UserDB).filter(UserDB.username == user["username"]).first()
        if not db_user or db_user.student_id != student_id:
            raise HTTPException(status_code=403, detail="Access denied")

    grades = db.query(GradeDB).filter(GradeDB.student_id == student_id).all()
    return {"grades": grades}


@app.delete("/delete_grade/{grade_id}")
def delete_grade(
    grade_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
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
        day=entry.day,
        subject=entry.subject,
        teacher=entry.teacher,
        time_slot=entry.time_slot
    )
    db.add(new_entry)
    db.commit()
    db.refresh(new_entry)
    return {"message": "Timetable entry added", "data": new_entry}


@app.get("/timetable")
def get_timetable(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    entries = db.query(TimetableDB).all()
    return {"timetable": entries}


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
    notice: NoticeCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    new_notice = NoticeDB(
        title=notice.title,
        content=notice.content,
        date=notice.date
    )
    db.add(new_notice)
    db.commit()
    db.refresh(new_notice)
    return {"message": "Notice added", "data": new_notice}


@app.get("/notices")
def get_notices(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    notices = db.query(NoticeDB).order_by(NoticeDB.date.desc()).all()
    return {"notices": notices}


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