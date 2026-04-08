from sqlalchemy import Column, Integer, String, Date, Float, ForeignKey, UniqueConstraint
from database import Base


class StudentDB(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    age = Column(Integer, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    phone = Column(String(20), nullable=True)
    address = Column(String(255), nullable=True)
    course = Column(String(100), nullable=True)
    fees = Column(Float, nullable=True)
    parent_phone = Column(String(20), nullable=True)

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