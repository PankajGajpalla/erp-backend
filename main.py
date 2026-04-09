from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from database import SessionLocal, engine, Base
from models import StudentDB, UserDB, AttendanceDB, FeesDB, TeacherDB, NoticeDB, GradeDB, TimetableDB
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

FAST2SMS_API_KEY = os.getenv("FAST2SMS_API_KEY", "")

async def send_sms(phone: str, message: str):
    if not FAST2SMS_API_KEY:
        print("❌ SMS: API key not set!")
        return False
    if not phone:
        print("❌ SMS: No phone number provided!")
        return False
    try:
        print(f"📱 Sending SMS to {phone}...")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://www.fast2sms.com/dev/bulkV2",
                headers={"authorization": FAST2SMS_API_KEY},
                json={
                    "route": "q",
                    "message": message,
                    "language": "english",
                    "flash": 0,
                    "numbers": phone
                },
                timeout=10
            )
            data = response.json()
            print(f"📱 Fast2SMS response: {data}")
            return data.get("return", False)
    except Exception as e:
        print(f"❌ SMS error: {e}")
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
    name: str
    age: int
    email: str
    phone: Optional[str] = None
    address: Optional[str] = None
    course: Optional[str] = None
    fees: Optional[float] = None
    parent_phone: Optional[str] = None 

class StudentBulk(BaseModel):
    students: List[Student]

class AttendanceCreate(BaseModel):
    student_id: int
    date: date
    status: Literal["present", "absent"]

class FeesCreate(BaseModel):
    student_id: int
    amount: float
    description: Optional[str] = None

class FeesPayment(BaseModel):
    pay_amount: float

class TeacherCreate(BaseModel):
    name: str
    email: str
    subject: str
    phone: Optional[str] = None

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
    total_fees = sum(f.amount for f in fees)
    total_paid = sum(f.paid for f in fees)

    return {
        "total_students": total_students,
        "total_attendance": total_attendance,
        "total_fees": total_fees,
        "total_paid": total_paid,
        "total_pending": total_fees - total_paid
    }

# ----------------------------------------------------------------------------------------------------
# STUDENTS

@app.post("/add_student")
def add_student(
    student: Student,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    if db.query(StudentDB).filter(StudentDB.email == student.email).first():
        raise HTTPException(status_code=400, detail="Email already exists")

    new_student = StudentDB(
        name=student.name,
        age=student.age,
        email=student.email,
        phone=student.phone,
        address=student.address,
        course=student.course,
        fees=student.fees,
        parent_phone=student.parent_phone  # ✅ new
    )
    db.add(new_student)
    db.flush()

    if student.fees:
        db.add(FeesDB(
            student_id=new_student.id,
            amount=student.fees, paid=0.0,
            description="Initial Fees"
        ))

    db.commit()
    db.refresh(new_student)
    return {"message": "Student saved", "student": new_student}


@app.get("/students")
def get_students(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return {"students": db.query(StudentDB).all()}


@app.get("/student/{student_id}")
def get_student(student_id: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    student = db.query(StudentDB).filter(StudentDB.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return student


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

    for field in ["name", "age", "email", "phone", "address", "course", "fees"]:
        setattr(student, field, getattr(updated_data, field))
    
    #updated...
    student.parent_phone = updated_data.parent_phone 

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


@app.delete("/delete_student/{student_id}")
def delete_student(
    student_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    student = db.query(StudentDB).filter(StudentDB.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    db.query(UserDB).filter(UserDB.student_id == student_id).delete()
    db.query(FeesDB).filter(FeesDB.student_id == student_id).delete()
    db.query(AttendanceDB).filter(AttendanceDB.student_id == student_id).delete()
    db.query(GradeDB).filter(GradeDB.student_id == student_id).delete()
    db.delete(student)
    db.commit()
    return {"message": "Student and all related data deleted"}


@app.post("/import_students")
def import_students(
    data: StudentBulk,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin"))
):
    imported = skipped = 0

    for student in data.students:
        if db.query(StudentDB).filter(StudentDB.email == student.email).first():
            skipped += 1
            continue

        new_student = StudentDB(
            name=student.name,
            age=student.age,
            email=student.email,
            phone=student.phone,
            address=student.address,
            course=student.course,
            fees=student.fees,
            parent_phone=student.parent_phone  # ✅ new
        )
        db.add(new_student)
        db.flush()

        if student.fees:
            db.add(FeesDB(
                student_id=new_student.id,
                amount=student.fees, paid=0.0,
                description="Imported Fees"
            ))
        imported += 1

    db.commit()
    return {"message": "Import complete", "imported": imported, "skipped": skipped}

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


@app.post("/mark_attendance")
def mark_attendance(
    attendance: AttendanceCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles(["admin", "teacher"]))
):
    if not db.query(StudentDB).filter(StudentDB.id == attendance.student_id).first():
        raise HTTPException(status_code=404, detail="Student not found")

    existing = db.query(AttendanceDB).filter(
        AttendanceDB.student_id == attendance.student_id,
        AttendanceDB.date == attendance.date
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Attendance already marked for this date")

    new_record = AttendanceDB(
        student_id=attendance.student_id,
        date=attendance.date, status=attendance.status
    )
    db.add(new_record)
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
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    # Admin and teacher can view any student
    # Student can only view their own
    if user["role"] == "student":
        db_user = db.query(UserDB).filter(UserDB.username == user["username"]).first()
        if not db_user or db_user.student_id != student_id:
            raise HTTPException(status_code=403, detail="Access denied")

    records = db.query(AttendanceDB).filter(AttendanceDB.student_id == student_id).all()
    return {"attendance": records}


@app.post("/mark_attendance_bulk")
async def mark_attendance_bulk(
    records: List[AttendanceCreate],
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles(["admin", "teacher"]))
):
    print(f"📋 Bulk attendance called with {len(records)} records")
    marked = updated = 0
    sms_sent = 0
    sms_failed = 0

    for record in records:
        existing = db.query(AttendanceDB).filter(
            AttendanceDB.student_id == record.student_id,
            AttendanceDB.date == record.date
        ).first()

        if existing:
            existing.status = record.status
            updated += 1
        else:
            db.add(AttendanceDB(
                student_id=record.student_id,
                date=record.date,
                status=record.status
            ))
            marked += 1

        # ✅ Send SMS to parent if marked present
        if record.status == "present":
            student = db.query(StudentDB).filter(
                StudentDB.id == record.student_id
            ).first()

            if student and student.parent_phone:
                message = (
                    f"Dear Parent, your child {student.name} has been marked "
                    f"PRESENT on {record.date}. - ERP System"
                )
                sent = await send_sms(student.parent_phone, message)
                if sent:
                    sms_sent += 1
                else:
                    sms_failed += 1

    db.commit()
    return {
        "message": "Attendance saved",
        "marked": marked,
        "updated": updated,
        "sms_sent": sms_sent,
        "sms_failed": sms_failed
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
        description=fees.description
    )
    db.add(new_fee)
    db.commit()
    db.refresh(new_fee)
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
    db.commit()
    db.refresh(fee)
    return {"message": "Payment updated", "data": fee, "remaining": fee.amount - fee.paid}

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
        subject=teacher.subject, phone=teacher.phone
    )
    db.add(new_teacher)
    db.commit()
    db.refresh(new_teacher)
    return {"message": "Teacher added", "data": new_teacher}


@app.get("/teachers")
def get_teachers(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return {"teachers": db.query(TeacherDB).all()}


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


@app.get("/students/course/{course}")
def get_students_by_course(
    course: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles(["admin", "teacher"]))
):
    return {"students": db.query(StudentDB).filter(StudentDB.course == course).all()}


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
    return teacher

# ----------------------------------------------------------------------------------------------------
# GRADES

@app.post("/add_grade")
def add_grade(
    grade: GradeCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles(["admin", "teacher"]))  # ✅ teachers can add grades
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
    user: dict = Depends(require_roles(["admin", "teacher"]))
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
        day=entry.day, subject=entry.subject,
        teacher=entry.teacher, time_slot=entry.time_slot
    )
    db.add(new_entry)
    db.commit()
    db.refresh(new_entry)
    return {"message": "Timetable entry added", "data": new_entry}


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
def get_notices(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return {"notices": db.query(NoticeDB).order_by(NoticeDB.date.desc()).all()}


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