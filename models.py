from sqlalchemy import Column, Integer, String, Date, Float, ForeignKey
from database import Base


class StudentDB(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    age = Column(Integer, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    phone = Column(String(20), nullable=True)
    address = Column(String(255), nullable=True)


class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    role = Column(String(50), default="student", nullable=False)

    # ✅ FIX 3: Link User to Student
    student_id = Column(Integer, ForeignKey("students.id"), nullable=True)


class AttendanceDB(Base):
    __tablename__ = "attendance"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    date = Column(Date, nullable=False)
    status = Column(String(20), nullable=False)    # present / absent


class FeesDB(Base):
    __tablename__ = "fees"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    amount = Column(Float, nullable=False)       # total fee amount
    paid = Column(Float, default=0.0)            # how much has been paid
    description = Column(String(200), nullable=True)  # e.g. "Term 1 Fees"  status = Column(String(20), nullable=False)    # paid / unpaid


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
    day = Column(String(20), nullable=False)       # Monday, Tuesday etc.
    subject = Column(String(100), nullable=False)
    teacher = Column(String(100), nullable=False)
    time_slot = Column(String(50), nullable=False)  # e.g. 9:00 - 10:00


class NoticeDB(Base):
    __tablename__ = "notices"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    content = Column(String(1000), nullable=False)
    date = Column(Date, nullable=False)