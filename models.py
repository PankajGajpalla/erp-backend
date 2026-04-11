from sqlalchemy import Column, Integer, String, Date, Float, ForeignKey, UniqueConstraint, Text
from database import Base


class StudentDB(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    father_name = Column(String(100), nullable=True)
    dob = Column(Date, nullable=True)
    email = Column(String(255), unique=True, nullable=False)
    phone = Column(String(20), nullable=True)           # student mobile
    parent_phone = Column(String(20), nullable=True)
    permanent_address = Column(String(500), nullable=True)
    local_address = Column(String(500), nullable=True)
    course = Column(String(100), nullable=True)
    fees = Column(Float, nullable=True)
    school_college_name = Column(String(200), nullable=True)
    medium = Column(String(20), nullable=True)          # hindi / english
    admission_date = Column(Date, nullable=True)
    photo = Column(Text, nullable=True)                 # base64 image
    # kept for backward compatibility
    age = Column(Integer, nullable=True)
    address = Column(String(255), nullable=True)

class CourseDB(Base):
    __tablename__ = "courses"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), unique=True, nullable=False)
    description = Column(String(500), nullable=True)
    duration = Column(String(100), nullable=True)   # e.g. "1 Year", "6 Months"
    fees = Column(Float, nullable=True)             # default fees for this course


class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    role = Column(String(50), default="student", nullable=False)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)


class AttendanceDB(Base):
    __tablename__ = "attendance"

    # ✅ Prevent duplicate attendance at DB level
    __table_args__ = (
        UniqueConstraint('student_id', 'date', name='unique_student_date'),
    )

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    date = Column(Date, nullable=False)
    status = Column(String(20), nullable=False)    # present / absent


class FeesDB(Base):
    __tablename__ = "fees"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    amount = Column(Float, nullable=False)        # total fee amount
    paid = Column(Float, default=0.0)             # how much has been paid
    description = Column(String(200), nullable=True)
    due_date = Column(Date, nullable=True)        # payment due date


class FeePaymentDB(Base):
    __tablename__ = "fee_payments"

    id = Column(Integer, primary_key=True, index=True)
    fee_id = Column(Integer, ForeignKey("fees.id"), nullable=False)
    amount = Column(Float, nullable=False)        # amount paid in this transaction
    paid_date = Column(Date, nullable=False)      # date of this payment
    note = Column(String(200), nullable=True)     # e.g. "Cash", "Online", receipt no.


class TeacherDB(Base):
    __tablename__ = "teachers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    subject = Column(String(100), nullable=False)
    phone = Column(String(20), nullable=True)


class GradeDB(Base):
    __tablename__ = "grades"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    subject = Column(String(100), nullable=False)
    marks = Column(Float, nullable=False)
    total_marks = Column(Float, nullable=False)
    grade = Column(String(5), nullable=True)


class TimetableDB(Base):
    __tablename__ = "timetable"

    id = Column(Integer, primary_key=True, index=True)
    day = Column(String(20), nullable=False)
    subject = Column(String(100), nullable=False)
    teacher = Column(String(100), nullable=False)
    time_slot = Column(String(50), nullable=False)


class NoticeDB(Base):
    __tablename__ = "notices"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    content = Column(String(5000), nullable=False)  # ✅ increased limit
    date = Column(Date, nullable=False)