from sqlalchemy import Column, Integer, String, Date, Float, ForeignKey, UniqueConstraint, Text, Boolean
from database import Base


class StudentDB(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    student_code = Column(String(20), unique=True, nullable=True)  # e.g. STU0001
    name = Column(String(100), nullable=False)
    father_name = Column(String(100), nullable=True)
    dob = Column(Date, nullable=True)
    email = Column(String(255), unique=True, nullable=True)
    phone = Column(String(50), nullable=False)           # student mobile — required
    parent_phone = Column(String(100), nullable=True)   # may contain multiple numbers
    permanent_address = Column(String(500), nullable=True)
    local_address = Column(String(500), nullable=True)
    course = Column(String(100), nullable=True)
    fees = Column(Float, nullable=True)
    school_college_name = Column(String(200), nullable=True)
    medium = Column(String(20), nullable=True)          # hindi / english
    admission_date = Column(Date, nullable=True)
    photo = Column(Text, nullable=True)                 # base64 image

class CourseDB(Base):
    __tablename__ = "courses"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), unique=True, nullable=False)
    description = Column(String(500), nullable=True)
    duration = Column(String(100), nullable=True)   # e.g. "1 Year", "6 Months"
    fees = Column(Float, nullable=True)             # default fees for this course


class SubjectDB(Base):
    __tablename__ = "subjects"

    id = Column(Integer, primary_key=True, index=True)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=False)
    name = Column(String(200), nullable=False)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)


class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    role = Column(String(50), default="student", nullable=False)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)
    is_hidden = Column(Boolean, default=False)  # hidden superadmin — never shown in UI


class AttendanceDB(Base):
    __tablename__ = "attendance"

    # ✅ Prevent duplicate attendance at DB level
    __table_args__ = (
        UniqueConstraint('student_id', 'date', 'subject_id', name='unique_student_date_subject'),
    )

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    date = Column(Date, nullable=False)
    status = Column(String(20), nullable=False)    # present / absent
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=True)


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
    payment_mode = Column(String(50), nullable=True)


class TeacherDB(Base):
    __tablename__ = "teachers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    subject = Column(String(100), nullable=False)
    phone = Column(String(50), nullable=True)


class GradeDB(Base):
    __tablename__ = "grades"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    subject = Column(String(100), nullable=False)
    marks = Column(Float, nullable=False)
    total_marks = Column(Float, nullable=False)
    grade = Column(String(5), nullable=True)
    test_title = Column(String(200), nullable=True)  # e.g. "Unit Test 1", "Mid Term"


class TimetableDB(Base):
    __tablename__ = "timetable"

    id = Column(Integer, primary_key=True, index=True)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=True)
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
    course = Column(String(100), nullable=True)   # null = visible to all


class StudentAdditionalCourseDB(Base):
    """Many-to-many: a student can opt into multiple additional courses."""
    __tablename__ = "student_additional_courses"

    __table_args__ = (
        UniqueConstraint('student_id', 'course_id', name='uq_student_additional_course'),
    )

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    course_id  = Column(Integer, ForeignKey("courses.id",  ondelete="CASCADE"), nullable=False)


class FeeTemplateDB(Base):
    __tablename__ = "fee_templates"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)          # e.g. "Monthly Fee - BCA"
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=True)  # null = all courses
    amount = Column(Float, nullable=False)
    description = Column(String(200), nullable=True)

class ExamScheduleDB(Base):
    __tablename__ = "exam_schedule"
    id = Column(Integer, primary_key=True, index=True)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=False)
    title = Column(String(200), nullable=False)          # e.g. "Unit Test 1"
    subject = Column(String(200), nullable=False)
    exam_date = Column(Date, nullable=False)
    exam_time = Column(String(50), nullable=True)        # e.g. "10:00 AM"
    duration = Column(String(50), nullable=True)         # e.g. "2 Hours"
    syllabus = Column(Text, nullable=True)
    total_marks = Column(Float, nullable=True)

class AuditLogDB(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    performed_by = Column(String(100), nullable=False)   # username
    action = Column(String(50), nullable=False)          # CREATE / UPDATE / DELETE
    entity = Column(String(100), nullable=False)         # e.g. "Student", "Fee"
    entity_id = Column(Integer, nullable=True)
    details = Column(Text, nullable=True)                # JSON or short description
    timestamp = Column(String(50), nullable=False)       # ISO string


class NoticeReadDB(Base):
    __tablename__ = "notice_reads"
    __table_args__ = (
        UniqueConstraint('notice_id', 'user_id', name='uq_notice_read'),
    )
    id = Column(Integer, primary_key=True, index=True)
    notice_id = Column(Integer, ForeignKey("notices.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    read_at = Column(String(50), nullable=False)