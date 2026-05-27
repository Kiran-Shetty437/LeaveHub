# LeaveHub (College Leave Management System) - Project Documentation

## 1. Project Overview
**LeaveHub** is an automated leave management and attendance tracking system tailored for educational institutions. It streamlines the workflow for students and faculty to apply for leaves, while providing administrators and mentors with real-time tools for monitoring and approval.

---

## 2. Technology Stack
- **Core**: Flask (Python)
- **Database**: SQLite with SQLAlchemy ORM
- **Real-time Communication**: Flask-SocketIO (WebSocket)
- **Email Notifications**: Flask-Mail (SMTP)
- **Frontend**: HTML5, JavaScript, and Vanilla CSS (Modern Glassmorphism Aesthetic)
- **Data Storage**: JSON-based auxiliary data for mentors, subjects, and user synchronization.

---

## 3. Software Requirements Specification (SRS)

### 3.1 Role-Based Functional Requirements
#### **Admin Role**
- **Dashboard**: High-level overview of faculty/student counts and pending actions.
- **User Management**: Add, update, or remove teachers and students.
- **Approval Workflow**: Final authority for teacher leaves and forwarded student leaves.
- **Attendance Insights**: View daily absentees and generate leave reports.
- **System Control**: Toggle the Academic Portal (Start/End dates) to restrict applications.
- **Watchlist**: Automatically identify students with low attendance (< 70%).

#### **Teacher Role**
- **Personal Dashboard**: Track personal leave status and balances.
- **Mentorship**: Act as a mentor for specific classes; review and approve/forward student leaves.
- **Attendance Management**: Mark subject-wise attendance and view class analytics.
- **Absence Alerts**: Receive real-time Socket.IO alerts when a student in their lecture is absent.
- **Timetable**: View and manage assigned lecture schedules.

#### **Student Role**
- **Dashboard**: Monitor active leave requests and current status.
- **Leave Application**: Detailed application form with dynamic rules (e.g., medical certificates for long leaves).
- **Attendance Monitoring**: View individual attendance records and receive "below threshold" warnings.
- **Timetable Access**: view the dynamic class schedule.

### 3.2 Business Rules & Constraints
- **Student Same-Day Leave**: Must be submitted before 8:30 AM on the day of leave.
- **Teacher Same-Day Leave**: Must be submitted at least 1 hour before the leave start time.
- **Advance Limit**: Leaves can only be applied up to 10 days in advance.
- **Attendance Threshold**: System flags students below 75% for warnings and below 70% for the Admin Watchlist.

---

## 4. Database Models (Schema)

### **User**
Tracks identity, role (Admin/Teacher/Student), and department/class association.

### **LeaveRequest**
Core record for every application. Tracks dates, reasons, status (Pending/Forwarded/Approved/Rejected), and optional documents.

### **LeaveBalance**
Manages specific leave quotas (Casual, Medical, etc.) for faculty members. Deductions occur only upon final approval.

### **Attendance**
Logs student attendance per subject and month. Used for generating dynamic metrics and warnings.

### **TeacherTimetable**
Maps teachers to specific periods, subjects, and classes for schedule-aware notifications.

### **Settings**
Global key-value pairs for system behavior, such as `academic_start` and `academic_end` dates.

---

## 5. System Design & Architecture
- **Event-Driven UI**: Socket.IO is used to push toast notifications to Admins and Mentors as soon as a request is submitted.
- **Proactive Alerts**: Background logic calculates if a student absent on leave impacts a specific teacher's lecture and alerts them during their session.
- **Role-Based Middlewares**: Ensures secure access to templates and API endpoints based on the session role.
- **Schema Resilience**: `app.py` includes automatic schema migrators to ensure missing table columns (like `remark` or `start_time`) are added without manual DB intervention.

---

## 6. Installation & Deployment
1. **Prepare Environment**: Install dependencies (`flask`, `flask-sqlalchemy`, `flask-socketio`, `flask-mail`).
2. **Setup Environment**: Configure `.env` with secure keys and SMTP credentials.
3. **Run**: Execute `python app.py`. The system will automatically initialize the database and sync users from `users_data.json`.
