import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime, timedelta
import requests
import json
from werkzeug.utils import secure_filename
import threading
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'default_secret_key')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URI', 'sqlite:///database_v2.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB limit

# Email Configuration
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')

mail = Mail(app)

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def send_approval_email(target_email, name, role, dates):
    try:
        msg = Message("Leave Request Approved",
                      recipients=[target_email])
        msg.body = f"Hello {name},\n\nYour leave request for {dates} as a {role} has been APPROVED.\n\nBest regards,\nCollege Leave Management System"
        
        # Send in a background thread to avoid slowing down the UI
        thread = threading.Thread(target=lambda: mail.send(msg))
        thread.start()
        print(f"Approval email sent to {target_email}")
    except Exception as e:
        print(f"Error sending email: {e}")

def is_absent_today(date_str, target_date=None):
    """
    Checks if target_date falls within the provided date_str.
    Expects formats like 'DD-MM-YYYY' or 'DD-MM-YYYY to DD-MM-YYYY'
    """
    from datetime import date
    if target_date is None:
        target_date = date.today()
    
    try:
        if 'to' in date_str.lower():
            start_str, end_str = date_str.lower().split('to')
            start_date = datetime.strptime(start_str.strip(), '%d-%m-%Y').date()
            end_date = datetime.strptime(end_str.strip(), '%d-%m-%Y').date()
            return start_date <= target_date <= end_date
        else:
            single_date = datetime.strptime(date_str.strip(), '%d-%m-%Y').date()
            return single_date == target_date
    except:
        # Fallback: simple string inclusion check if format is non-standard
        today_str = target_date.strftime('%d-%m-%Y')
        return today_str in date_str

def get_effective_status(leave):
    """
    Returns 'Not Approved' if the leave is still Pending or Forwarded but the date has passed.
    """
    if leave.status not in ['Pending', 'Forwarded to Admin']:
        return leave.status
        
    from datetime import date
    today = date.today()
    try:
        dates_str = leave.dates.lower()
        if ' to ' in dates_str:
            end_str = dates_str.split('to')[1].strip()
        else:
            end_str = dates_str.strip()
        
        # Consistent parsing with DD-MM-YYYY
        end_date = datetime.strptime(end_str, '%d-%m-%Y').date()
        if today > end_date:
            return "Not Approved (Expired)"
    except Exception as e:
        print(f"Error checking leave expiration for ID {leave.id}: {e}")
        
    return leave.status

def add_notice(name, message):
    notices_path = os.path.join(app.root_path, 'notices.json')
    try:
        if os.path.exists(notices_path):
            with open(notices_path, 'r') as f:
                notices = json.load(f)
        else:
            notices = []
    except:
        notices = []
    notices.append({'name': name, 'message': message})
    try:
        with open(notices_path, 'w') as f:
            json.dump(notices, f)
    except: pass

def pop_notices(name):
    notices_path = os.path.join(app.root_path, 'notices.json')
    user_notices = []
    try:
        if os.path.exists(notices_path):
            with open(notices_path, 'r') as f:
                notices = json.load(f)
            remaining = []
            for n in notices:
                if n.get('name') == name:
                    user_notices.append(n.get('message'))
                else:
                    remaining.append(n)
            with open(notices_path, 'w') as f:
                json.dump(remaining, f)
    except: pass
    return user_notices

db = SQLAlchemy(app)

# Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False) # Admin, Teacher, Student
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    department = db.Column(db.String(100)) # Dept for Teacher, Class for Student
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    dob = db.Column(db.String(20))
    roll_no = db.Column(db.String(50))
    is_hod = db.Column(db.Boolean, default=False)

class Settings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(200))

class LeaveBalance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    leave_type = db.Column(db.String(50), nullable=False)
    balance = db.Column(db.Integer, default=0)
    
    user = db.relationship('User', backref=db.backref('balances', lazy=True))

class LeaveRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    dates = db.Column(db.String(100), nullable=False)
    leave_type = db.Column(db.String(50))
    start_time = db.Column(db.String(20))
    document_path = db.Column(db.String(200))
    status = db.Column(db.String(20), default='Pending')
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('leaves', lazy=True))

class TeacherTimetable(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    day = db.Column(db.String(20), nullable=False) # Monday, Tuesday, etc.
    period = db.Column(db.Integer, nullable=False) # 1, 2, 3, etc.
    subject = db.Column(db.String(100), nullable=False)
    class_name = db.Column(db.String(50)) # The class this period belongs to
    
    teacher = db.relationship('User', backref=db.backref('timetable', lazy=True))

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    total_classes = db.Column(db.Integer, default=0)
    attended_classes = db.Column(db.Integer, default=0)
    month = db.Column(db.String(20), nullable=False) # e.g., "April 2026"
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)
    
    student = db.relationship('User', backref=db.backref('attendance_records', lazy=True))

class TeacherSubject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    
    teacher = db.relationship('User', backref=db.backref('assigned_subjects', lazy=True))


# Create Database and Admin
with app.app_context():
    db.create_all()

    # DB Schema Update: Run BEFORE user sync or any queries so new columns exist
    try:
        from sqlalchemy import text
        with db.engine.connect() as conn:
            # Check and add 'is_hod' to user table
            try:
                conn.execute(text("ALTER TABLE user ADD COLUMN is_hod BOOLEAN DEFAULT 0"))
                conn.commit()
                print("Added 'is_hod' column to user table.")
            except: pass

            # Check and add 'remark'
            try:
                conn.execute(text("ALTER TABLE leave_request ADD COLUMN remark TEXT"))
                conn.commit()
                print("Added 'remark' column to leave_request table.")
            except: pass
            
            # Check and add 'start_time'
            try:
                conn.execute(text("ALTER TABLE leave_request ADD COLUMN start_time TEXT"))
                conn.commit()
                print("Added 'start_time' column to leave_request table.")
            except: pass

            # 1. LeaveRequest Updates
            for col in ['remark', 'start_time', 'leave_type']:
                try:
                    conn.execute(text(f"ALTER TABLE leave_request ADD COLUMN {col} TEXT"))
                    conn.commit()
                    print(f"Added '{col}' to leave_request.")
                except: pass
            
            # 2. TeacherTimetable Updates
            try:
                conn.execute(text("ALTER TABLE teacher_timetable ADD COLUMN class_name TEXT"))
                conn.commit()
                print("Added 'class_name' to teacher_timetable.")
            except: pass

            # 3. Attendance Table
            try:
                conn.execute(text("CREATE TABLE IF NOT EXISTS attendance (id INTEGER PRIMARY KEY, student_id INTEGER, subject TEXT, total_classes INTEGER, attended_classes INTEGER, month TEXT, last_updated DATETIME)"))
                conn.commit()
                try: # Ensure month column exists
                    conn.execute(text("ALTER TABLE attendance ADD COLUMN month TEXT"))
                    conn.commit()
                except: pass
            except: pass

            # 4. New Management Tables
            try:
                conn.execute(text("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, key TEXT UNIQUE, value TEXT)"))
                conn.execute(text("CREATE TABLE IF NOT EXISTS leave_balance (id INTEGER PRIMARY KEY, user_id INTEGER, leave_type TEXT, balance INTEGER, FOREIGN KEY(user_id) REFERENCES user(id))"))
                conn.commit()
                print("Management tables ensured.")
            except: pass

    except Exception as e:
        print(f"Schema update error: {e}")
    
    # Sync users from JSON (AFTER schema migration, BEFORE queries)
    json_path = os.path.join(app.root_path, 'users_data.json')
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
            
            # safer sync: upsert instead of delete all (to preserve IDs and relations)
            existing_usernames = {u.username for u in User.query.all()}
            
            for user_data in data:
                if user_data['username'] not in existing_usernames:
                    new_user = User(
                        name=user_data['name'],
                        role=user_data['role'],
                        username=user_data['username'],
                        password=user_data['password'],
                        department=user_data['department'],
                        email=user_data.get('email'),
                        phone=user_data.get('phone'),
                        dob=user_data.get('dob'),
                        roll_no=user_data.get('roll_no'),
                        is_hod=user_data.get('is_hod', False)
                    )
                    db.session.add(new_user)
                else:
                    u = User.query.filter_by(username=user_data['username']).first()
                    if u:
                        u.is_hod = user_data.get('is_hod', False)
            
            db.session.commit()
            print("Successfully synchronized new users from JSON.")
        except Exception as e:
            print(f"Error syncing JSON: {e}")
            db.session.rollback()

    # Check if admin exists
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(name='Administrator', role='Admin', username='admin', password='admin123')
        db.session.add(admin)
        db.session.commit()

    # Initialize default leave balances for all teachers
    teachers = User.query.filter_by(role='Teacher').all()
    default_counts = {
        'Casual Leave': 15,
        'Medical Leave': 10,
        'Earned Leave': 5,
        'Special Leave': 5
    }
    for t in teachers:
        existing_bals = LeaveBalance.query.filter_by(user_id=t.id).all()
        if not existing_bals:
            # No records at all — create defaults
            for lt, count in default_counts.items():
                new_bal = LeaveBalance(user_id=t.id, leave_type=lt, balance=count)
                db.session.add(new_bal)
        else:
            # If ALL balances are 0, reset to defaults
            if all(b.balance == 0 for b in existing_bals):
                for lt, count in default_counts.items():
                    bal = LeaveBalance.query.filter_by(user_id=t.id, leave_type=lt).first()
                    if bal:
                        bal.balance = count
                    else:
                        db.session.add(LeaveBalance(user_id=t.id, leave_type=lt, balance=count))
            # Also ensure all 4 leave types exist
            existing_types = {b.leave_type for b in existing_bals}
            for lt, count in default_counts.items():
                if lt not in existing_types:
                    db.session.add(LeaveBalance(user_id=t.id, leave_type=lt, balance=count))
    db.session.commit()
    
    def inject_helpers():
        return dict(get_effective_status=get_effective_status)
    app.context_processor(inject_helpers)

# Routes
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.password == password:
            session['user_id'] = user.id
            session['role'] = user.role
            session['name'] = user.name
            session['department'] = user.department
            session['is_hod'] = user.is_hod or False
            session['roll_no'] = user.roll_no
            
            # Determine if this teacher is a mentor
            session['is_mentor'] = False
            if user.role == 'Teacher':
                try:
                    mentors_path = os.path.join(app.root_path, 'mentors_data.json')
                    if os.path.exists(mentors_path):
                        with open(mentors_path, 'r') as f:
                            mentors_data = json.load(f)
                        for item in mentors_data:
                            if item['mentor1'] == user.name or item['mentor2'] == user.name:
                                session['is_mentor'] = True
                                break
                except Exception as e:
                    print(f"Error checking mentor status at login: {e}")
                    
            for msg in pop_notices(user.name):
                flash(msg, 'info')
                
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials!', 'danger')
            
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    role = session['role']
    user = User.query.get(session['user_id'])
    
    # Refresh session data from DB to ensure it's not None
    if user:
        session['department'] = user.department
        session['name'] = user.name
        session['is_hod'] = user.is_hod or False
        session['roll_no'] = user.roll_no
        
        # Refresh is_mentor status
        if user.role == 'Teacher':
            session['is_mentor'] = False
            try:
                mentors_path = os.path.join(app.root_path, 'mentors_data.json')
                if os.path.exists(mentors_path):
                    with open(mentors_path, 'r') as f:
                        mentors_data = json.load(f)
                    for item in mentors_data:
                        if item['mentor1'] == user.name or item['mentor2'] == user.name:
                            session['is_mentor'] = True
                            break
            except Exception as e:
                print(f"Error refreshing mentor status: {e}")
    
    if role == 'Admin':
        teacher_count = User.query.filter_by(role='Teacher').count()
        student_count = User.query.filter_by(role='Student').count()
        all_pending = LeaveRequest.query.filter(
            ((LeaveRequest.role == 'Teacher') & (LeaveRequest.status == 'Pending')) |
            (LeaveRequest.status == 'Forwarded to Admin')
        ).all()
        
        pending_leaves = 0
        yesterday = (datetime.now() - timedelta(days=1)).date()
        end_date = (datetime.now() + timedelta(days=10)).date()
        for leave in all_pending:
            try:
                if 'to' in leave.dates.lower():
                    start_str = leave.dates.lower().split('to')[0].strip()
                else:
                    start_str = leave.dates.strip()
                leave_start = datetime.strptime(start_str, '%d-%m-%Y').date()
                if yesterday <= leave_start <= end_date:
                    pending_leaves += 1
            except:
                pending_leaves += 1
                
        return render_template('admin/dashboard.html', 
                             teacher_count=teacher_count, 
                             student_count=student_count, 
                             pending_leaves=pending_leaves)
    
    elif role == 'Teacher':
        # Find mentored classes for this teacher
        current_teacher_name = session.get('name')
        mentored_classes = []
        try:
            mentors_path = os.path.join(app.root_path, 'mentors_data.json')
            if os.path.exists(mentors_path):
                with open(mentors_path, 'r') as f:
                    mentors_data = json.load(f)
                for item in mentors_data:
                    if item['mentor1'] == current_teacher_name or item['mentor2'] == current_teacher_name:
                        mentored_classes.append(item['class_name'])
                        
            if session.get('is_hod') and mentored_classes:
                dept_teachers = User.query.filter_by(role='Teacher', department=session.get('department')).all()
                all_mentored_teachers = set()
                for item in mentors_data:
                    if item.get('mentor1'): all_mentored_teachers.add(item['mentor1'])
                    if item.get('mentor2'): all_mentored_teachers.add(item['mentor2'])
                
                all_others_mentors = True
                for t in dept_teachers:
                    if t.name != current_teacher_name and not t.is_hod:
                        if t.name not in all_mentored_teachers:
                            all_others_mentors = False
                            break
                if not all_others_mentors:
                    flash("Notice: Change your mentorship. As an HOD, you should not be a mentor unless all other teachers in your department are assigned.", "warning")

        except Exception as e:
            print(f"Error loading mentors in dashboard: {e}")

        # Filter count for mentored classes only (with date filter)
        pending_student_leaves = 0
        if mentored_classes:
            raw_pending_leaves = LeaveRequest.query.join(User, LeaveRequest.user_id == User.id)\
                                    .filter(LeaveRequest.status == 'Pending', LeaveRequest.role == 'Student')\
                                    .filter(User.department.in_(mentored_classes)).all()
            
            yesterday = (datetime.now() - timedelta(days=1)).date()
            end_date = (datetime.now() + timedelta(days=10)).date()
            
            for leave in raw_pending_leaves:
                try:
                    if 'to' in leave.dates.lower():
                        start_str = leave.dates.lower().split('to')[0].strip()
                    else:
                        start_str = leave.dates.strip()
                    leave_start = datetime.strptime(start_str, '%d-%m-%Y').date()
                    if yesterday <= leave_start <= end_date:
                        pending_student_leaves += 1
                except:
                    pending_student_leaves += 1
                                
        # 9 AM Shift Rule: If it's before 9 AM, use yesterday's date.
        now_time_for_shift = datetime.now()
        if now_time_for_shift.time() < datetime.strptime("09:00:00", "%H:%M:%S").time():
            effective_date = (now_time_for_shift - timedelta(days=1)).date()
        else:
            effective_date = now_time_for_shift.date()

        # Today's Student Absences (Mentored Classes, effective date)
        active_student_absences = []
        if mentored_classes:
            all_approved = LeaveRequest.query.join(User, LeaveRequest.user_id == User.id)\
                            .filter(LeaveRequest.status == 'Approved', LeaveRequest.role == 'Student')\
                            .filter(User.department.in_(mentored_classes)).all()
            
            for leave in all_approved:
                if is_absent_today(leave.dates, effective_date):
                    active_student_absences.append(leave)

        # Today's Absentee List for Teacher's Classes (effective date)
        today_day = effective_date.strftime('%A')
        today_classes_objs = TeacherTimetable.query.filter_by(teacher_id=session['user_id'], day=today_day).all()
        today_classes = list(set([tc.class_name for tc in today_classes_objs if tc.class_name]))
        
        today_class_absentees = {}
        if today_classes:
            all_approved_today = LeaveRequest.query.join(User, LeaveRequest.user_id == User.id)\
                .filter(LeaveRequest.status == 'Approved', LeaveRequest.role == 'Student')\
                .filter(User.department.in_(today_classes)).all()
            
            for leave in all_approved_today:
                if is_absent_today(leave.dates, effective_date):
                    cls = leave.user.department
                    if cls not in today_class_absentees:
                        today_class_absentees[cls] = []
                    today_class_absentees[cls].append(leave)
                    
        now_time = datetime.now().time()
        is_after_9am = now_time >= datetime.strptime("09:00:00", "%H:%M:%S").time()

        all_my_leaves = LeaveRequest.query.filter_by(user_id=session['user_id']).order_by(LeaveRequest.created_at.desc()).all()
        my_leaves = []
        now_date_for_leaves = datetime.now().date()
        for leave in all_my_leaves:
            try:
                if 'to' in leave.dates.lower():
                    end_str = leave.dates.lower().split('to')[1].strip()
                else:
                    end_str = leave.dates.strip()
                end_dt = datetime.strptime(end_str, '%d-%m-%Y').date()
                if now_date_for_leaves <= end_dt + timedelta(days=1):
                    my_leaves.append(leave)
            except:
                my_leaves.append(leave)
        return render_template('teacher/dashboard.html', 
                               pending_student_leaves=pending_student_leaves, 
                               my_leaves=my_leaves,
                               active_student_absences=active_student_absences,
                               today_classes=today_classes,
                               today_class_absentees=today_class_absentees,
                               is_after_9am=is_after_9am)
    
    elif role == 'Student':
        student_class = session.get('department')
        
        # 1. Fetch Class Mentor
        mentors = []
        try:
            mentors_path = os.path.join(app.root_path, 'mentors_data.json')
            if os.path.exists(mentors_path):
                with open(mentors_path, 'r') as f:
                    for item in json.load(f):
                        if item.get('class_name') == student_class:
                            if item.get('mentor1'): mentors.append(item['mentor1'])
                            if item.get('mentor2'): mentors.append(item['mentor2'])
                            break
        except: pass
        mentor_name = " / ".join(mentors) if mentors else "Not Assigned"

        # 2. Fetch HOD Name
        hod_name = "Not Assigned"
        all_hods = User.query.filter_by(role='Teacher', is_hod=True).all()
        for hod in all_hods:
            d_classes, _ = get_hod_allowed_subjects(hod.department)
            if student_class in d_classes:
                hod_name = hod.name
                break

        # 3. Fetch Subject Teachers
        subject_teachers_map = {}
        tt_records = TeacherTimetable.query.filter_by(class_name=student_class).all()
        for rec in tt_records:
            if rec.subject and rec.teacher:
                if rec.subject not in subject_teachers_map:
                    subject_teachers_map[rec.subject] = set()
                subject_teachers_map[rec.subject].add(rec.teacher.name)
        
        subject_teachers = {sub: " / ".join(list(teachers)) for sub, teachers in subject_teachers_map.items()}

        all_my_leaves = LeaveRequest.query.filter_by(user_id=session['user_id']).order_by(LeaveRequest.created_at.desc()).all()
        my_leaves = []
        now_date_for_leaves = datetime.now().date()
        for leave in all_my_leaves:
            try:
                if 'to' in leave.dates.lower():
                    end_str = leave.dates.lower().split('to')[1].strip()
                else:
                    end_str = leave.dates.strip()
                end_dt = datetime.strptime(end_str, '%d-%m-%Y').date()
                if now_date_for_leaves <= end_dt + timedelta(days=1):
                    my_leaves.append(leave)
            except:
                my_leaves.append(leave)
        now = datetime.now()
        for leave in my_leaves:
            leave.contact_teacher = False
            if leave.status == 'Pending':
                try:
                    # Parse the start date of the leave
                    if ' to ' in leave.dates.lower():
                        start_str = leave.dates.lower().split('to')[0].strip()
                    else:
                        start_str = leave.dates.strip()
                    
                    leave_start_dt = datetime.strptime(start_str, '%d-%m-%Y')
                    cutoff_time = datetime.strptime("09:00:00", "%H:%M:%S").time()
                    
                    # Rule: If today is >= leave date AND current time >= 9:00 AM
                    if now.date() > leave_start_dt.date():
                        leave.contact_teacher = True
                    elif now.date() == leave_start_dt.date() and now.time() >= cutoff_time:
                        leave.contact_teacher = True
                except:
                    pass
                    
        return render_template('student/dashboard.html', 
                               my_leaves=my_leaves, 
                               now=now,
                               mentor_name=mentor_name,
                               hod_name=hod_name,
                               subject_teachers=subject_teachers)
        
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# Socket.IO Connection and Rooms
@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        if session['role'] == 'Admin':
            join_room('admin_room')
        elif session['role'] == 'Teacher':
            # Join room for individual notifications
            join_room(f"user_{session['user_id']}")
            # Join rooms for mentored classes
            current_teacher_name = session.get('name')
            try:
                mentors_path = os.path.join(app.root_path, 'mentors_data.json')
                if os.path.exists(mentors_path):
                    with open(mentors_path, 'r') as f:
                        mentors_data = json.load(f)
                    for item in mentors_data:
                        if item['mentor1'] == current_teacher_name or item['mentor2'] == current_teacher_name:
                            join_room(f"mentor_{item['class_name']}")
                            print(f"Teacher {current_teacher_name} joined room: mentor_{item['class_name']}")
            except Exception as e:
                print(f"Error joining mentor rooms: {e}")
        else:
            join_room(f"user_{session['user_id']}")
    print(f"Client connected: {request.sid}")

# Admin Routes
def sync_users_json():
    """Sync all users from the database back to users_data.json"""
    try:
        users = User.query.all()
        users_list = []
        for u in users:
            users_list.append({
                'name': u.name,
                'role': u.role,
                'username': u.username,
                'password': u.password,
                'department': u.department,
                'email': u.email,
                'phone': u.phone,
                'dob': u.dob,
                'roll_no': u.roll_no,
                'is_hod': u.is_hod if u.is_hod else False
            })
        json_path = os.path.join(app.root_path, 'users_data.json')
        with open(json_path, 'w') as f:
            json.dump(users_list, f, indent=4)
    except Exception as e:
        print(f'Error syncing users_data.json: {e}')

@app.route('/admin/teachers')
def manage_teachers():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    teachers = User.query.filter_by(role='Teacher').all()
    
    mentor_names = set()
    mentors_path = os.path.join(app.root_path, 'mentors_data.json')
    if os.path.exists(mentors_path):
        try:
            with open(mentors_path, 'r') as f:
                for item in json.load(f):
                    mentor_names.add(item.get('mentor1'))
                    mentor_names.add(item.get('mentor2'))
        except: pass
            
    for t in teachers:
        t.is_mentor = t.name in mentor_names
        
    return render_template('admin/teachers.html', teachers=teachers)

@app.route('/admin/set_hod/<int:teacher_id>', methods=['POST'])
def set_hod(teacher_id):
    if session.get('role') != 'Admin': return jsonify({'success': False}), 403
    
    teacher = User.query.get(teacher_id)
    if not teacher or teacher.role != 'Teacher':
        return jsonify({'success': False, 'message': 'Teacher not found'}), 404
        
    # Remove HOD status from any other teacher in this department
    other_hods = User.query.filter_by(role='Teacher', department=teacher.department, is_hod=True).all()
    for t in other_hods:
        t.is_hod = False
        add_notice(t.name, f'Notice: You have been removed from the HOD position for {teacher.department}.')
        
    teacher.is_hod = True
    add_notice(teacher.name, f'Congratulations! You are now the HOD of {teacher.department}.')
    db.session.commit()
    sync_users_json()
    
    return jsonify({'success': True, 'message': f'{teacher.name} is now the HOD of {teacher.department}.'})

@app.route('/admin/students')
def manage_students():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    students = User.query.filter_by(role='Student').all()
    return render_template('admin/students.html', students=students)

@app.route('/admin/add_teacher', methods=['POST'])
def add_teacher():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    name = request.form.get('name')
    username = request.form.get('username')
    password = request.form.get('password')
    department = request.form.get('department')
    email = request.form.get('email')
    phone = request.form.get('phone')
    
    if User.query.filter_by(username=username).first():
        flash('Username already exists!', 'danger')
        return redirect(url_for('manage_teachers'))
        
    new_user = User(name=name, role='Teacher', username=username, password=password, department=department, email=email, phone=phone)
    db.session.add(new_user)
    db.session.commit()
    
    # Initialize default balances
    default_counts = {'Casual Leave': 15, 'Medical Leave': 10, 'Earned Leave': 5, 'Special Leave': 5}
    for lt, count in default_counts.items():
        db.session.add(LeaveBalance(user_id=new_user.id, leave_type=lt, balance=count))
    db.session.commit()
    sync_users_json()
    
    flash('Teacher added successfully!', 'success')
    return redirect(url_for('manage_teachers'))

@app.route('/admin/add_student', methods=['POST'])
def add_student():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    name = request.form.get('name')
    username = request.form.get('username')
    password = request.form.get('password')
    department = request.form.get('department')
    roll_no = request.form.get('roll_no')
    email = request.form.get('email')
    phone = request.form.get('phone')
    
    if User.query.filter_by(username=username).first():
        flash('Username already exists!', 'danger')
        return redirect(url_for('manage_students'))
        
    new_user = User(name=name, role='Student', username=username, password=password, department=department, roll_no=roll_no, email=email, phone=phone)
    db.session.add(new_user)
    db.session.commit()
    sync_users_json()
    
    flash('Student added successfully!', 'success')
    return redirect(url_for('manage_students'))

@app.route('/admin/upload_students_json', methods=['POST'])
def upload_students_json():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    file = request.files.get('json_file')
    if not file or not file.filename.endswith('.json'):
        flash('Please upload a valid JSON file', 'danger')
        return redirect(url_for('manage_students'))
        
    try:
        data = json.load(file)
        added_count = 0
        for user_data in data:
            if user_data.get('role') == 'Student' and not User.query.filter_by(username=user_data.get('username')).first():
                new_user = User(
                    name=user_data.get('name'),
                    role='Student',
                    username=user_data.get('username'),
                    password=user_data.get('password'),
                    department=user_data.get('department'),
                    email=user_data.get('email'),
                    phone=user_data.get('phone'),
                    dob=user_data.get('dob'),
                    roll_no=user_data.get('roll_no')
                )
                db.session.add(new_user)
                added_count += 1
        db.session.commit()
        sync_users_json()
        flash(f'Successfully imported {added_count} students.', 'success')
    except json.JSONDecodeError:
        flash('Invalid JSON file format. Please ensure your file is a valid JSON and formatted correctly.', 'danger')
    except Exception as e:
        flash(f'Error processing JSON: {e}', 'danger')
        
    return redirect(url_for('manage_students'))

@app.route('/admin/watchlist')
def admin_watchlist():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    current_month = datetime.now().strftime('%B %Y')
    filter_month = request.args.get('month', current_month)
    
    # Get available months
    available_months_query = db.session.query(Attendance.month).distinct().all()
    months = sorted(list(set([m[0] for m in available_months_query if m[0] is not None])))
    if current_month not in months:
        months.append(current_month)
        
    low_attendance_students = []
    students = User.query.filter_by(role='Student').all()
    
    mentors_map = {}
    try:
        mentors_path = os.path.join(app.root_path, 'mentors_data.json')
        if os.path.exists(mentors_path):
            with open(mentors_path, 'r') as f:
                mentors_data = json.load(f)
                for item in mentors_data:
                    mentors_map[item['class_name']] = f"{item['mentor1']} / {item['mentor2']}"
    except: pass

    for student in students:
        records = Attendance.query.filter_by(student_id=student.id, month=filter_month).all()
        total_held = sum(r.total_classes for r in records)
        total_att = sum(r.attended_classes for r in records)
        
        if total_held > 0:
            pct = (total_att / total_held) * 100
            if pct < 70:
                low_attendance_students.append({
                    'name': student.name,
                    'class': student.department,
                    'mentor': mentors_map.get(student.department, 'Not Assigned'),
                    'percentage': round(pct, 1)
                })
                
    # Check for missing attendance by teachers for the filter_month
    missing_attendance = []
    teachers = User.query.filter_by(role='Teacher').all()
    
    for teacher in teachers:
        timetables = TeacherTimetable.query.filter_by(teacher_id=teacher.id).all()
        assigned = set()
        for t in timetables:
            if t.class_name and t.subject:
                assigned.add((t.class_name, t.subject))
                
        missed = []
        for cls_name, subj in assigned:
            student_ids = [s.id for s in User.query.filter_by(role='Student', department=cls_name).all()]
            if not student_ids:
                continue
                
            record_exists = Attendance.query.filter(
                Attendance.student_id.in_(student_ids),
                Attendance.subject == subj,
                Attendance.month == filter_month,
                Attendance.total_classes > 0
            ).first()
            
            if not record_exists:
                missed.append({'class': cls_name, 'subject': subj})
                
        if missed:
            missing_attendance.append({
                'teacher_name': teacher.name,
                'email': teacher.email or 'N/A',
                'phone': teacher.phone or 'N/A',
                'missed': missed
            })
                
    return render_template('admin/watchlist.html', 
                         low_attendance=low_attendance_students,
                         missing_attendance=missing_attendance,
                         months=months,
                         filter_month=filter_month)

@app.route('/admin/leaves')
def view_all_leaves():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    # Admin views: All Teacher leaves and Forwarded/Timeout Student leaves
    all_leaves = LeaveRequest.query.filter(
        (LeaveRequest.role == 'Teacher') | 
        (LeaveRequest.status == 'Forwarded to Admin') |
        (LeaveRequest.status == 'Timeout')
    ).order_by(LeaveRequest.created_at.desc()).all()
    
    filtered_leaves = []
    now = datetime.now()
    today = now.date()
    cutoff_time = datetime.strptime("09:00:00", "%H:%M:%S").time()
    timeout_committed = False
    
    for leave in all_leaves:
        try:
            dates_lower = leave.dates.lower()
            if 'to' in dates_lower:
                start_str = dates_lower.split('to')[0].strip()
                end_str = dates_lower.split('to')[1].strip()
            else:
                start_str = dates_lower.strip()
                end_str = start_str
            
            leave_start = datetime.strptime(start_str, '%d-%m-%Y').date()
            leave_end = datetime.strptime(end_str, '%d-%m-%Y').date()
            
            # Auto-Timeout: If still Pending/Forwarded and it's past 9 AM on the leave start date
            if leave.status in ['Pending', 'Forwarded to Admin'] and \
               (today > leave_start or (today == leave_start and now.time() >= cutoff_time)):
                leave.status = 'Timeout'
                leave.remark = (leave.remark or '') + ' [Auto-Timeout: No action taken before 9 AM on leave date]'
                timeout_committed = True
            
            # Visibility Rule: Show if the leave end date + 1 day hasn't fully passed
            if leave_end + timedelta(days=1) >= today:
                filtered_leaves.append(leave)
            elif leave.status == 'Timeout' and leave_start == today:
                filtered_leaves.append(leave)
        except:
            # If dates can't be parsed, include to be safe
            filtered_leaves.append(leave)
    
    if timeout_committed:
        db.session.commit()
            
    info_message = f"Showing active leave requests as of {today.strftime('%d-%m-%Y')}. Leaves are automatically timed out after 9 AM on the leave date."
    
    return render_template('admin/leaves.html', leaves=filtered_leaves, info_message=info_message)

@app.route('/admin/absentees')
def view_absentees():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    # Get all approved leaves
    approved_leaves = LeaveRequest.query.filter_by(status='Approved').all()
    
    absent_teachers = {}
    absent_students = {}
    
    for leave in approved_leaves:
        if leave.role == 'Teacher':
            dept = leave.user.department
            if dept not in absent_teachers: absent_teachers[dept] = []
            absent_teachers[dept].append(leave)
        else:
            cls = leave.user.department # department field stores Class for students
            if cls not in absent_students: absent_students[cls] = []
            absent_students[cls].append(leave)
                
    return render_template('admin/absentees.html', 
                            absent_teachers=absent_teachers, 
                            absent_students=absent_students)

@app.route('/admin/reports')
def leave_reports():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    filter_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    filter_status = request.args.get('status', '')
    
    all_leaves = LeaveRequest.query.order_by(LeaveRequest.created_at.desc()).all()
    
    report_teachers = {}
    report_students = {}
    
    for leave in all_leaves:
            
        # Date Filter (Leaves occurring on that date)
        if filter_date:
            try:
                target_dt = datetime.strptime(filter_date, '%Y-%m-%d').date()
                dates_str = leave.dates.lower()
                is_match = False
                if 'to' in dates_str:
                    parts = dates_str.split('to')
                    start_dt = datetime.strptime(parts[0].strip(), '%d-%m-%Y').date()
                    end_dt = datetime.strptime(parts[1].strip(), '%d-%m-%Y').date()
                    if start_dt <= target_dt <= end_dt:
                        is_match = True
                else:
                    single_dt = datetime.strptime(dates_str.strip(), '%d-%m-%Y').date()
                    if single_dt == target_dt:
                        is_match = True
                        
                if not is_match:
                    continue
            except Exception as e:
                target_str = datetime.strptime(filter_date, '%Y-%m-%d').date().strftime('%d-%m-%Y')
                if target_str not in leave.dates:
                    continue
                    
        # Note: Status filter is handled by frontend JavaScript now.
                
        if leave.role == 'Teacher':
            dept = leave.user.department or 'Unknown'
            if dept not in report_teachers: report_teachers[dept] = []
            report_teachers[dept].append(leave)
        else:
            cls = leave.user.department or 'Unknown'
            if cls not in report_students: report_students[cls] = []
            report_students[cls].append(leave)
            
    return render_template('admin/reports.html', 
                            report_teachers=report_teachers, 
                            report_students=report_students,
                            filter_date=filter_date,
                            filter_status=filter_status)

@app.route('/admin/delete_user/<int:user_id>')
def delete_user(user_id):
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    user = User.query.get(user_id)
    if user and user.role != 'Admin':
        db.session.delete(user)
        db.session.commit()
        flash('User deleted successfully!', 'success')
    return redirect(request.referrer)

# Teacher Routes
@app.route('/teacher/student_leaves')
def teacher_student_leaves():
    if session.get('role') != 'Teacher': return redirect(url_for('login'))
    
    # Only mentors can access the approvals page
    if not session.get('is_mentor'):
        flash('Approvals are only available for mentors.', 'warning')
        return redirect(url_for('dashboard'))
    
    current_teacher_name = session.get('name')
    
    # Load mentors data to find which class this teacher mentors
    import json
    mentors_path = os.path.join(app.root_path, 'mentors_data.json')
    mentored_classes = []
    
    if os.path.exists(mentors_path):
        try:
            with open(mentors_path, 'r') as f:
                mentors_data = json.load(f)
            for item in mentors_data:
                if item['mentor1'] == current_teacher_name or item['mentor2'] == current_teacher_name:
                    mentored_classes.append(item['class_name'])
        except Exception as e:
            print(f"Error reading mentors: {e}")

    # If the teacher is not a mentor for any class, they see no student leaves
    if not mentored_classes:
        return render_template('teacher/student_leaves.html', leaves=[], mentored_classes=[])

    # Fetch ALL student leaves for mentored classes (any actionable status)
    all_student_leaves = LeaveRequest.query.join(User, LeaveRequest.user_id == User.id)\
                               .filter(User.role == 'Student')\
                               .filter(LeaveRequest.status.in_(['Pending', 'Approved', 'Rejected', 'Forwarded to Admin', 'Timeout']))\
                               .filter(User.department.in_(mentored_classes))\
                               .order_by(LeaveRequest.created_at.desc()).all()
                               
    leaves = []
    now = datetime.now()
    today = now.date()
    cutoff_time = datetime.strptime("09:00:00", "%H:%M:%S").time()
    timeout_committed = False
    
    for leave in all_student_leaves:
        try:
            dates_lower = leave.dates.lower()
            if 'to' in dates_lower:
                start_str = dates_lower.split('to')[0].strip()
                end_str = dates_lower.split('to')[1].strip()
            else:
                start_str = dates_lower.strip()
                end_str = start_str
            
            leave_start = datetime.strptime(start_str, '%d-%m-%Y').date()
            leave_end = datetime.strptime(end_str, '%d-%m-%Y').date()
            
            # Auto-Timeout: If still Pending and it's past 9 AM on the leave start date
            if leave.status == 'Pending' and (today > leave_start or (today == leave_start and now.time() >= cutoff_time)):
                leave.status = 'Timeout'
                leave.remark = (leave.remark or '') + ' [Auto-Timeout: No action taken before 9 AM on leave date]'
                timeout_committed = True
            
            # Visibility Rule: Show if the leave end date + 1 day hasn't fully passed
            # i.e., show on the leave end date itself and the day after
            if leave_end + timedelta(days=1) >= today:
                leaves.append(leave)
            # Also show Timeout leaves on the day they timed out (leave_start == today)
            elif leave.status == 'Timeout' and leave_start == today:
                leaves.append(leave)
        except:
            # If dates can't be parsed, include it to be safe so it doesn't get lost
            leaves.append(leave)
    
    if timeout_committed:
        db.session.commit()
                               
    return render_template('teacher/student_leaves.html', leaves=leaves, mentored_classes=mentored_classes)

@app.route('/teacher/monthly_reports')
def monthly_reports():
    if session.get('role') != 'Teacher': return redirect(url_for('login'))
    
    # Only mentors can access class analytics
    if not session.get('is_mentor'):
        flash('Class Analytics is only available for mentors.', 'warning')
        return redirect(url_for('dashboard'))
    
    current_teacher_name = session.get('name')
    mentors_path = os.path.join(app.root_path, 'mentors_data.json')
    mentored_classes = []
    
    if os.path.exists(mentors_path):
        try:
            with open(mentors_path, 'r') as f:
                mentors_data = json.load(f)
            for item in mentors_data:
                if item['mentor1'] == current_teacher_name or item['mentor2'] == current_teacher_name:
                    mentored_classes.append(item['class_name'])
        except Exception as e: print(f"Error: {e}")

    if not mentored_classes:
        flash("You are not assigned as a mentor for any class.", "warning")
        return redirect(url_for('dashboard'))

    # Analytics Logic
    from sqlalchemy import extract
    now = datetime.now()
    current_month_leaves = LeaveRequest.query.join(User).filter(
        User.department.in_(mentored_classes),
        extract('month', LeaveRequest.created_at) == now.month,
        extract('year', LeaveRequest.created_at) == now.year,
        LeaveRequest.status == 'Approved'
    ).all()

    # Aggregate Data for Charts
    stats = {} # {StudentName: Count}
    reasons = {} # {ReasonWord: Count}
    
    for leave in current_month_leaves:
        stats[leave.user.name] = stats.get(leave.user.name, 0) + 1
        # Simple reason extraction
        r = leave.reason.split()[0][:10] if leave.reason else "Other"
        reasons[r] = reasons.get(r, 0) + 1

    chart_data = {
        'labels': list(stats.keys()),
        'counts': list(stats.values()),
        'reason_labels': list(reasons.keys()),
        'reason_counts': list(reasons.values())
    }

    return render_template('teacher/monthly_reports.html', 
                           mentored_classes=mentored_classes, 
                           chart_data=chart_data,
                           total_month_leaves=len(current_month_leaves),
                           current_month_leaves=current_month_leaves,
                           now=now)

# General Routes
@app.route('/apply_leave', methods=['GET', 'POST'])
def apply_leave():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if request.method == 'POST':
        reason = request.form.get('reason')
        dates = request.form.get('dates')
        start_time_val = request.form.get('start_time') # Optional time field
        file = request.files.get('document')
        
        # Date Validation: Pattern and Past Dates
        from datetime import date
        today = date.today()
        import re
        # Regex for DD-MM-YYYY or DD-MM-YYYY to DD-MM-YYYY
        pattern = r'^\d{2}-\d{2}-\d{4}( to \d{2}-\d{2}-\d{4})?$'
        
        if not re.match(pattern, dates.strip()):
            flash('Invalid date pattern! Please use DD-MM-YYYY or DD-MM-YYYY to DD-MM-YYYY', 'warning')
            return redirect(request.referrer)

        try:
            # 0. Academic Window Check
            from datetime import date
            today = date.today()
            
            s_obj = Settings.query.filter_by(key='academic_start').first()
            e_obj = Settings.query.filter_by(key='academic_end').first()
            
            if s_obj and e_obj:
                acad_start = datetime.strptime(s_obj.value, '%Y-%m-%d').date()
                acad_end = datetime.strptime(e_obj.value, '%Y-%m-%d').date()
                
                if today < acad_start or today > acad_end:
                    flash(f'Academic Portal Closed. Leaves can only be applied between {acad_start.strftime("%d-%m-%Y")} and {acad_end.strftime("%d-%m-%Y")}.', 'danger')
                    return redirect(request.referrer)

            if ' to ' in dates.lower():
                start_str = dates.lower().split('to')[0].strip()
            else:
                start_str = dates.strip()
            
            # Use %d-%m-%Y for parsing
            requested_start = datetime.strptime(start_str, '%d-%m-%Y').date()
            if requested_start < today:
                flash(f'Cannot apply for past dates! Today is {today.strftime("%d-%m-%Y")}.', 'warning')
                return redirect(request.referrer)
            
            # 1. Same-Day Leave Rules
            if requested_start == today:
                now_dt = datetime.now()
                now_time = now_dt.time()
                
                # Student Rule: Before 8:30 AM
                if session.get('role') == 'Student':
                    cutoff_time = datetime.strptime("08:30:00", "%H:%M:%S").time()
                    if now_time >= cutoff_time:
                        flash('Same-day student leave must be applied before 8:30 AM!', 'danger')
                        return redirect(request.referrer)
                
                # Teacher Rules for Same-Day
                elif session.get('role') == 'Teacher':
                    if start_time_val:
                        try:
                            leave_dt = datetime.strptime(f"{today.strftime('%d-%m-%Y')} {start_time_val}", "%d-%m-%Y %H:%M")
                            time_diff = leave_dt - now_dt
                            diff_minutes = time_diff.total_seconds() / 60
                            
                            if diff_minutes < 60:
                                flash('Same-day teacher leave must be applied at least 1 hour before the leave starts!', 'danger')
                                return redirect(request.referrer)
                        except ValueError:
                            flash('Invalid time format! Please use HH:MM', 'warning')
                            return redirect(request.referrer)
                    else:
                        cutoff_time = datetime.strptime("08:00:00", "%H:%M:%S").time()
                        if now_time >= cutoff_time:
                            flash('Same-day full day teacher leave must be applied before 8:00 AM!', 'danger')
                            return redirect(request.referrer)
            
            # 2. Advance Limit Check (10 days)
            days_diff = (requested_start - today).days
            if days_diff > 10:
                flash('Leave can only be applied up to 10 days in advance!', 'danger')
                return redirect(request.referrer)
                
            # Time constraint for Teacher Partial Leaves (9:30 AM to 4:50 PM)
            if session.get('role') == 'Teacher' and start_time_val:
                try:
                    st_time = datetime.strptime(start_time_val, "%H:%M").time()
                    min_time = datetime.strptime("09:30:00", "%H:%M:%S").time()
                    max_time = datetime.strptime("16:50:00", "%H:%M:%S").time()
                    if st_time < min_time or st_time > max_time:
                        flash('Partial day leaves can only be taken between 09:30 AM and 04:50 PM. Action Denied.', 'danger')
                        return redirect(request.referrer)
                except Exception as e:
                    pass

            # 3. Leave Balance Check for Teachers
            if session.get('role') == 'Teacher':
                l_type = request.form.get('leave_type')
                if not l_type:
                    flash('Please select a leave type.', 'warning')
                    return redirect(request.referrer)
                
                bal = LeaveBalance.query.filter_by(user_id=session['user_id'], leave_type=l_type).first()
                if not bal or bal.balance <= 0:
                    flash(f'No {l_type} available. Your current balance is 0.', 'danger')
                    return redirect(request.referrer)
                
                # Calculate duration
                duration = 1
                if ' to ' in dates.lower():
                    try:
                        pts = dates.lower().split(' to ')
                        d1 = datetime.strptime(pts[0].strip(), '%d-%m-%Y')
                        d2 = datetime.strptime(pts[1].strip(), '%d-%m-%Y')
                        duration = (d2 - d1).days + 1
                    except: duration = 1
                
                if bal.balance < duration:
                    flash(f'Insufficient balance. Required: {duration}, Available: {bal.balance}', 'danger')
                    return redirect(request.referrer)
                
                # Deduction will happen ONLY upon Admin approval now.
                # db.session.commit() removed here to prevent immediate deduction.

        except Exception as e:
            flash(f'Error processing request: {e}', 'warning')
            return redirect(request.referrer)
        
        filename = None
        if file and allowed_file(file.filename):
            filename = secure_filename(f"{session['user_id']}_{datetime.now().timestamp()}_{file.filename}")
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        
        new_leave = LeaveRequest(
            user_id=session['user_id'], 
            role=session['role'], 
            reason=reason, 
            dates=dates, 
            leave_type=request.form.get('leave_type'),
            start_time=start_time_val if start_time_val else "Full Day",
            document_path=filename
        )
        db.session.add(new_leave)
        db.session.commit()
        
        # Real-time notification for Admin
        socketio.emit('new_leave_submitted', {
            'id': new_leave.id,
            'name': session['name'],
            'role': session['role'],
            'dates': dates,
            'reason': reason,
            'status': new_leave.status
        }, to='admin_room')
        
        # Real-time notification for Mentors
        if session['role'] == 'Student':
            student_class = user.department # 'department' field stores the class (e.g., IIBCA)
            socketio.emit('new_student_leave', {
                'id': new_leave.id,
                'student_name': session['name'],
                'student_class': student_class,
                'dates': dates
            }, to=f"mentor_{student_class}")
        
        flash('Leave request submitted!', 'success')
        return redirect(url_for('dashboard'))
    
    if session['role'] == 'Teacher':
        return render_template('teacher/apply_leave.html', user=user)
    else:
        # Calculate aggregate attendance for warning
        attendance_records = Attendance.query.filter_by(student_id=session['user_id']).all()
        total_held = sum(r.total_classes for r in attendance_records)
        total_attended = sum(r.attended_classes for r in attendance_records)
        
        avg_pct = (total_attended / total_held * 100) if total_held > 0 else 100
        attendance_warning = None
        if avg_pct < 75:
            attendance_warning = f"Alert: Your current aggregate attendance is {round(avg_pct, 1)}%, which is below the required 75%. Further leaves may impact your eligibility."
            
        return render_template('student/apply_leave.html', attendance_warning=attendance_warning)

@app.route('/api/student_attendance/<int:student_id>')
def get_student_attendance(student_id):
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    
    # Get all attendance records for this student
    records = Attendance.query.filter_by(student_id=student_id).all()
    
    # Organize by subject (show latest record per subject or average)
    summary = {}
    for r in records:
        if r.subject not in summary:
            summary[r.subject] = {'total': 0, 'attended': 0}
        summary[r.subject]['total'] += r.total_classes
        summary[r.subject]['attended'] += r.attended_classes
        
    data = []
    for sub, vals in summary.items():
        pct = (vals['attended'] / vals['total'] * 100) if vals['total'] > 0 else 0
        data.append({
            'subject': sub,
            'total': vals['total'],
            'attended': vals['attended'],
            'percentage': round(pct, 1)
        })
        
    return jsonify(data)

@app.route('/api/teacher/absentees')
def api_teacher_absentees():
    if session.get('role') != 'Teacher':
        return jsonify({'error': 'Unauthorized', 'success': False}), 401
        
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'error': 'Date is required', 'success': False}), 400
        
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD', 'success': False}), 400
        
    teacher_id = session['user_id']
    
    # 1. Find the day name for the selected date
    day_name = target_date.strftime('%A')
    
    # 2. Get teacher's classes and subjects on this day
    timetable_entries = TeacherTimetable.query.filter_by(teacher_id=teacher_id, day=day_name).all()
    
    class_subject_map = {} # {class_name: set(subjects)}
    for entry in timetable_entries:
        if entry.class_name and entry.subject:
            if entry.class_name not in class_subject_map:
                class_subject_map[entry.class_name] = set()
            class_subject_map[entry.class_name].add(entry.subject)
            
    if not class_subject_map:
        return jsonify({
            'success': True,
            'date': date_str,
            'absentees': [],
            'classes': []
        })
        
    # 3. Get all students enrolled in these classes
    students = User.query.filter(User.role == 'Student', User.department.in_(class_subject_map.keys())).all()
    student_ids = [s.id for s in students]
    
    if not student_ids:
        return jsonify({
            'success': True,
            'date': date_str,
            'absentees': [],
            'classes': list(class_subject_map.keys())
        })
        
    # 4. Get all approved student leave requests
    approved_leaves = LeaveRequest.query.filter(
        LeaveRequest.role == 'Student',
        LeaveRequest.status == 'Approved',
        LeaveRequest.user_id.in_(student_ids)
    ).all()
    
    absentees_list = []
    for leave in approved_leaves:
        if is_absent_today(leave.dates, target_date):
            student_class = leave.user.department
            # Subjects this teacher teaches to this class on this day
            missed_subjects = list(class_subject_map.get(student_class, []))
            for sub in missed_subjects:
                absentees_list.append({
                    'student_name': leave.user.name,
                    'roll_no': leave.user.roll_no or 'N/A',
                    'class_name': student_class,
                    'subject_missed': sub,
                    'reason': leave.reason
                })
                
    return jsonify({
        'success': True,
        'date': date_str,
        'absentees': absentees_list,
        'classes': sorted(list(class_subject_map.keys()))
    })

@app.route('/teacher/search_absentees')
def teacher_search_absentees():
    if session.get('role') != 'Teacher': return redirect(url_for('login'))
    return render_template('teacher/search_absentees.html')

@app.route('/teacher/attendance')
def teacher_attendance_list():
    if session.get('role') != 'Teacher': return redirect(url_for('login'))
    
    teacher_id = session.get('user_id')
    # Get all unique class+subject combinations this teacher takes
    classes = db.session.query(TeacherTimetable.class_name, TeacherTimetable.subject).filter_by(teacher_id=teacher_id).distinct().all()
    
    return render_template('teacher/attendance_list.html', classes=classes)

@app.route('/teacher/mark_attendance/<string:class_name>/<string:subject>')
def mark_attendance_form(class_name, subject):
    if session.get('role') != 'Teacher': return redirect(url_for('login'))
    
    current_month = request.args.get('month', datetime.now().strftime('%B %Y'))
    
    # Get all students in this class
    students = User.query.filter_by(role='Student', department=class_name).all()
    
    # Get existing attendance records for the selected month
    existing = {a.student_id: a for a in Attendance.query.filter_by(subject=subject, month=current_month).all()}
    
    # Generate list of last 6 months for selection
    months_list = []
    for i in range(6):
        m = (datetime.now() - timedelta(days=i*30)).strftime('%B %Y')
        if m not in months_list: months_list.append(m)
        
    return render_template('teacher/mark_attendance.html', 
                           students=students, 
                           class_name=class_name, 
                           subject=subject, 
                           existing=existing, 
                           current_month=current_month,
                           months_list=months_list)

@app.route('/teacher/save_attendance', methods=['POST'])
def save_attendance():
    if session.get('role') != 'Teacher': return redirect(url_for('login'))
    
    class_name = request.form.get('class_name')
    subject = request.form.get('subject')
    month = request.form.get('month')
    total_classes = int(request.form.get('total_classes', 0))
    
    # Get student list
    students = User.query.filter_by(role='Student', department=class_name).all()
    
    for student in students:
        attended_val = request.form.get(f'attended_{student.id}', 0)
        attended = int(attended_val) if attended_val else 0
        
        # Upsert logic based on student, subject, AND month
        record = Attendance.query.filter_by(student_id=student.id, subject=subject, month=month).first()
        if record:
            record.total_classes = total_classes
            record.attended_classes = attended
            record.last_updated = datetime.utcnow()
        else:
            new_record = Attendance(
                student_id=student.id,
                subject=subject,
                month=month,
                total_classes=total_classes,
                attended_classes=attended
            )
            db.session.add(new_record)
            
    db.session.commit()
    flash(f'Attendance for {month} saved successfully!', 'success')
    return redirect(url_for('teacher_attendance_list'))

@app.route('/update_leave/<int:leave_id>/<string:status>')
def update_leave(leave_id, status):
    if 'user_id' not in session: return redirect(url_for('login'))
    leave = LeaveRequest.query.get(leave_id)
    if not leave: return redirect(url_for('dashboard'))
    
    now = datetime.now()
    today = now.date()
    try:
        if 'to' in leave.dates.lower():
            start_str = leave.dates.lower().split('to')[0].strip()
        else:
            start_str = leave.dates.strip()
        leave_start_dt = datetime.strptime(start_str, '%d-%m-%Y').date()
    except:
        leave_start_dt = today
        
    # Student rule: Not modifiable after 9 AM on leave start date
    if leave.role == 'Student':
        if today > leave_start_dt or (today == leave_start_dt and now.time() >= datetime.strptime("09:00:00", "%H:%M:%S").time()):
            flash('Action Denied: Student leaves cannot be modified after 9 AM on the leave start date.', 'danger')
            return redirect(request.referrer)
            
    # Teacher rule
    elif leave.role == 'Teacher':
        if today > leave_start_dt:
            flash('Action Denied: Past leaves cannot be modified.', 'danger')
            return redirect(request.referrer)
        if today == leave_start_dt:
            if leave.start_time and leave.start_time != "Full Day":
                # Partial day: modifiable until 30 mins before start_time
                try:
                    start_t = datetime.strptime(leave.start_time, '%H:%M').time()
                    leave_dt = datetime.combine(today, start_t)
                    if now >= leave_dt - timedelta(minutes=30):
                        flash('Action Denied: Partial day leaves cannot be modified within 30 minutes of start time.', 'danger')
                        return redirect(request.referrer)
                except:
                    pass
            else:
                # Full day: modifiable until 9 AM
                if now.time() >= datetime.strptime("09:00:00", "%H:%M:%S").time():
                    flash('Action Denied: Full day teacher leaves cannot be modified after 9 AM on the leave date.', 'danger')
                    return redirect(request.referrer)

    current_role = session.get('role')
    
    remark = request.args.get('remark')
    old_status = leave.status
    
    # Admin can approve/reject Teacher leaves OR Student leaves forwarded to them
    if current_role == 'Admin':
        if leave.role == 'Teacher' or leave.status == 'Forwarded to Admin':
            leave.status = status
            if remark: leave.remark = remark
        else:
            flash('Unauthorized for this request', 'danger')
            return redirect(url_for('dashboard'))
            
    # Teacher (Mentor) can approve/reject/forward Student leaves
    elif current_role == 'Teacher' and leave.role == 'Student':
        leave.status = status
        if remark: leave.remark = remark
        
    # 3. Deduction Logic for Teachers only upon Approval
    if leave.role == 'Teacher':
        bal = LeaveBalance.query.filter_by(user_id=leave.user_id, leave_type=leave.leave_type).first()
        if bal:
            # Calculate duration
            duration = 1
            try:
                if ' to ' in leave.dates.lower():
                    pts = leave.dates.lower().split(' to ')
                    d1 = datetime.strptime(pts[0].strip(), '%d-%m-%Y')
                    d2 = datetime.strptime(pts[1].strip(), '%d-%m-%Y')
                    duration = (d2 - d1).days + 1
            except: duration = 1
            
            if status == 'Approved' and old_status != 'Approved':
                if bal.balance >= duration:
                    bal.balance -= duration
                    print(f"Deducted {duration} from {leave.user.name}'s {leave.leave_type} balance.")
                else:
                    flash(f"Warning: Teacher {leave.user.name} has insufficient balance ({bal.balance}) for this {duration}-day request.", "warning")
            elif status in ['Rejected', 'Pending'] and old_status == 'Approved':
                bal.balance += duration
                print(f"Refunded {duration} to {leave.user.name}'s {leave.leave_type} balance.")

    db.session.commit()
    
    # Notify User and Subject Teachers if leave is Approved
    if status == 'Approved':
        if leave.user.email:
            send_approval_email(leave.user.email, leave.user.name, leave.role, leave.dates)
            
        if leave.role == 'Student':
            try:
                student_class = leave.user.department
                # Parse dates to find days of week
                date_strs = []
                try:
                    if ' to ' in leave.dates:
                        start_str, end_str = leave.dates.split(' to ')
                        start_dt = datetime.strptime(start_str.strip(), '%d-%m-%Y')
                        end_dt = datetime.strptime(end_str.strip(), '%d-%m-%Y')
                        curr = start_dt
                        while curr <= end_dt:
                            date_strs.append(curr)
                            curr += timedelta(days=1)
                    else:
                        date_strs.append(datetime.strptime(leave.dates.strip(), '%d-%m-%Y'))
                except Exception as e:
                    print(f"Date parsing error: {e}")
                    date_strs = [datetime.now()]
                
                days_of_week = set(d.strftime('%A') for d in date_strs)
                
                # Cross-reference timetable for affected subject teachers
                subject_teachers = TeacherTimetable.query.filter(
                    TeacherTimetable.class_name == student_class,
                    TeacherTimetable.day.in_(list(days_of_week))
                ).all()
                
                teacher_map = {} # {teacher_id: [impacts]}
                for entry in subject_teachers:
                    if entry.teacher_id not in teacher_map: teacher_map[entry.teacher_id] = []
                    impact = f"{entry.subject} on {entry.day}"
                    if impact not in teacher_map[entry.teacher_id]:
                        teacher_map[entry.teacher_id].append(impact)
                
                # Notify each affected subject teacher in real-time
                for t_id, impacts in teacher_map.items():
                    socketio.emit('subject_teacher_absence_alert', {
                        'student_name': leave.user.name,
                        'class_name': student_class,
                        'dates': leave.dates,
                        'impacts': impacts
                    }, to=f"user_{t_id}")
                    
            except Exception as e:
                print(f"Subject teacher notification error: {e}")

    flash(f'Leave updated to {status} successfully!', 'info')
    
    # Standard real-time status update for Student and Admin
    update_data = {
        'id': leave_id,
        'status': status,
        'message': f"Leave request for {leave.user.name} has been {status}."
    }
    socketio.emit('leave_status_changed', update_data, to=f"user_{leave.user_id}")
    socketio.emit('leave_status_changed', update_data, to='admin_room')
    
    return redirect(request.referrer)

# Timetable Management Routes
def get_class_subject_mapping():
    json_path = os.path.join(app.root_path, 'class_subjects.json')
    if os.path.exists(json_path):
        with open(json_path, 'r') as f:
            return json.load(f)
    return {}

def get_class_from_subject(subject_name):
    if not subject_name: return None
    mapping = get_class_subject_mapping()
    search_sub = subject_name.strip().lower()
    for class_name, subjects in mapping.items():
        # Check case-insensitively
        if any(search_sub == s.strip().lower() for s in subjects):
            return class_name
    return None

@app.route('/teacher/timetable')
def teacher_timetable():
    if session.get('role') != 'Teacher': return redirect(url_for('login'))
    
    teacher_id = session.get('user_id')
    user = User.query.get(teacher_id)
    dept = user.department
    timetable_records = TeacherTimetable.query.filter_by(teacher_id=teacher_id).all()
    
    # Organize into a dict for easy access: {day: {period: subject}}
    timetable_data = {}
    for record in timetable_records:
        if record.day not in timetable_data: timetable_data[record.day] = {}
        timetable_data[record.day][record.period] = {
            'subject': record.subject,
            'class_name': record.class_name
        }
        
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
    periods = range(1, 8) # 7 periods
    
    mapping = get_class_subject_mapping()
    all_subjects = []
    for subjects in mapping.values():
        all_subjects.extend(subjects)
    all_subjects = sorted(list(set(all_subjects)))

    # Identify the teacher's mentored class
    mentor_class = None
    mentors_path = os.path.join(app.root_path, 'mentors_data.json')
    if os.path.exists(mentors_path):
        with open(mentors_path, 'r') as f:
            mentors_data = json.load(f)
        teacher_name = (session.get('name') or "").strip().lower()
        for mentor_info in mentors_data:
            if (mentor_info.get('mentor1') or "").strip().lower() == teacher_name or \
               (mentor_info.get('mentor2') or "").strip().lower() == teacher_name:
                mentor_class = mentor_info['class_name']
                break
    
    # Get teacher's HOD-assigned subjects for smart input
    assigned_subs = TeacherSubject.query.filter_by(teacher_id=teacher_id).all()
    assigned_subjects_list = [s.subject for s in assigned_subs]
    
    # Build a lookup: {subject_lower: class_name} and {class_name: subject} for assigned subjects only
    assigned_subject_class_map = {}  # subject -> class
    assigned_class_subject_map = {}  # class -> subject
    for sub_name in assigned_subjects_list:
        for c_name, subs in mapping.items():
            if any(sub_name.strip().lower() == s.strip().lower() for s in subs):
                assigned_subject_class_map[sub_name] = c_name
                assigned_class_subject_map[c_name] = sub_name
                break
    
    # 1. Get Department Classes & Timetables
    dept_classes, _ = get_hod_allowed_subjects(dept)
    classes_timetable = {}
    for c_name in dept_classes:
        records = TeacherTimetable.query.filter_by(class_name=c_name).all()
        tt = {}
        for rec in records:
            if rec.day not in tt: tt[rec.day] = {}
            tt[rec.day][rec.period] = {
                'subject': rec.subject,
                'teacher': rec.teacher.name if rec.teacher else 'Unknown'
            }
        classes_timetable[c_name] = tt

    # 2. Get Department Teachers & Timetables
    dept_teachers_objs = User.query.filter_by(role='Teacher', department=dept).all()
    dept_teachers = []
    for t in dept_teachers_objs:
        records = TeacherTimetable.query.filter_by(teacher_id=t.id).all()
        tt = {}
        for rec in records:
            if rec.day not in tt: tt[rec.day] = {}
            tt[rec.day][rec.period] = {
                'subject': rec.subject,
                'class_name': rec.class_name
            }
        dept_teachers.append({
            'id': t.id,
            'name': t.name,
            'is_hod': t.is_hod,
            'timetable': tt
        })

    return render_template('teacher/timetable.html', 
                           timetable_data=timetable_data, 
                           days=days, 
                           periods=periods,
                           all_subjects=all_subjects,
                           class_mapping=mapping,
                           mentor_class=mentor_class,
                           assigned_subjects=assigned_subjects_list,
                           assigned_subject_class_map=assigned_subject_class_map,
                           assigned_class_subject_map=assigned_class_subject_map,
                           dept_classes=dept_classes,
                           classes_timetable=classes_timetable,
                           dept_teachers=dept_teachers,
                           department=dept)

@app.route('/admin/subjects', methods=['GET', 'POST'])
def manage_subjects():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    mapping = get_class_subject_mapping()
    
    if request.method == 'POST':
        class_name = request.form.get('class_name')
        new_class_name = request.form.get('new_class_name', '').strip()
        new_subject = request.form.get('subject').strip()
        
        # Use existing class if selected, otherwise use new class name
        final_class = class_name if class_name else new_class_name
        
        if final_class and new_subject:
            if final_class not in mapping:
                mapping[final_class] = []
            
            if new_subject not in mapping[final_class]:
                mapping[final_class].append(new_subject)
                
                # Save back to JSON
                json_path = os.path.join(app.root_path, 'class_subjects.json')
                with open(json_path, 'w') as f:
                    json.dump(mapping, f, indent=2)
                
                flash(f'Subject "{new_subject}" added to {final_class}!', 'success')
            else:
                flash('Subject already exists for this class.', 'warning')
        else:
            flash('Please provide both Class Name and Subject.', 'danger')
        return redirect(url_for('manage_subjects'))
        
    return render_template('admin/subjects.html', mapping=mapping)

@app.route('/admin/settings', methods=['GET', 'POST'])
def manage_settings():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    if request.method == 'POST':
        st = request.form.get('academic_start')
        en = request.form.get('academic_end')
        
        s_obj = Settings.query.filter_by(key='academic_start').first()
        e_obj = Settings.query.filter_by(key='academic_end').first()
        
        if s_obj: s_obj.value = st
        if e_obj: e_obj.value = en
        db.session.commit()
        flash('Academic session settings updated!', 'success')
        return redirect(url_for('manage_settings'))
        
    st_obj = Settings.query.filter_by(key='academic_start').first()
    en_obj = Settings.query.filter_by(key='academic_end').first()
    st = st_obj.value if st_obj else '2026-06-01'
    en = en_obj.value if en_obj else '2027-05-31'
    return render_template('admin/settings.html', st=st, en=en)

@app.route('/admin/faculty_leaves', methods=['GET', 'POST'])
def manage_faculty_leaves():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    leave_types = ['Casual Leave', 'Medical Leave', 'Earned Leave', 'Special Leave']
    
    if request.method == 'POST':
        l_type = request.form.get('leave_type')
        count = int(request.form.get('count', 0))
        
        # Apply this count to ALL teachers
        teachers = User.query.filter_by(role='Teacher').all()
        for t in teachers:
            balance_obj = LeaveBalance.query.filter_by(user_id=t.id, leave_type=l_type).first()
            if balance_obj:
                balance_obj.balance = count
            else:
                new_bal = LeaveBalance(user_id=t.id, leave_type=l_type, balance=count)
                db.session.add(new_bal)
        
        db.session.commit()
        flash(f'All faculty members now have {count} units of {l_type}!', 'success')
        return redirect(url_for('manage_faculty_leaves'))
        
    teachers = User.query.filter_by(role='Teacher').all()
    return render_template('admin/faculty_leaves.html', teachers=teachers, leave_types=leave_types)

@app.route('/api/delete_subject', methods=['POST'])
def delete_subject():
    if session.get('role') != 'Admin': return jsonify({'success': False}), 403
    data = request.json
    class_name = data.get('class_name')
    subject = data.get('subject')
    
    mapping = get_class_subject_mapping()
    if class_name in mapping and subject in mapping[class_name]:
        mapping[class_name].remove(subject)
        json_path = os.path.join(app.root_path, 'class_subjects.json')
        with open(json_path, 'w') as f:
            json.dump(mapping, f, indent=2)
        return jsonify({'success': True})
    return jsonify({'success': False}), 400

@app.route('/api/save_timetable', methods=['POST'])
def save_timetable():
    if session.get('role') != 'Teacher': return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    data = request.json
    print(f"DEBUG: Received save_timetable request: {data}")
    teacher_id = session.get('user_id')
    day = data.get('day')
    class_name = None
    try:
        period = int(data.get('period'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'Invalid period data'}), 400
    subject = data.get('subject')
    
    if not day or not period:
        return jsonify({'success': False, 'message': 'Missing data'}), 400

    # 1. Determine Target Class
    mapping = get_class_subject_mapping()
    target_class = data.get('class_name') # Explicit selection from user
    official_subject = subject
    
    if subject:
        search_sub = subject.strip().lower()
        
        # 2. Identify the teacher's mentored class
        user_mentored_class = None
        mentors_path = os.path.join(app.root_path, 'mentors_data.json')
        if os.path.exists(mentors_path):
            with open(mentors_path, 'r') as f:
                mentors_data = json.load(f)
            teacher_name = (session.get('name') or "").strip().lower()
            for mentor_info in mentors_data:
                m1 = (mentor_info.get('mentor1') or "").strip().lower()
                m2 = (mentor_info.get('mentor2') or "").strip().lower()
                if m1 == teacher_name or m2 == teacher_name:
                    user_mentored_class = mentor_info['class_name']
                    break

        # 3. Determine Target Class
        if not target_class:
            # Check mentored class FIRST to prevent ambiguity
            if user_mentored_class and user_mentored_class in mapping:
                for s in mapping[user_mentored_class]:
                    if search_sub == s.strip().lower():
                        target_class = user_mentored_class
                        official_subject = s
                        break
            
            # If not in mentored class, search ALL classes
            if not target_class:
                for c_name, subjects in mapping.items():
                    for s in subjects:
                        if search_sub == s.strip().lower():
                            target_class = c_name
                            official_subject = s
                            break
                    if target_class: break
        
        # 4. Final Fallback: First class in their department, or first class in list, or "General"
        if not target_class:
            user_dept = session.get('department')
            if user_dept:
                for c_name in mapping.keys():
                    if user_dept.lower() in c_name.lower():
                        target_class = c_name
                        break
        
        if not target_class:
            target_class = list(mapping.keys())[0] if mapping.keys() else "General"
            
        print(f"Final determined class for {subject}: {target_class}")
        
        # 5. Auto-add to curriculum if it's missing from target class
        if target_class not in mapping: mapping[target_class] = []
        if not any(s.strip().lower() == search_sub for s in mapping[target_class]):
            mapping[target_class].append(subject)
            json_path = os.path.join(app.root_path, 'class_subjects.json')
            with open(json_path, 'w') as f:
                json.dump(mapping, f, indent=2)
            official_subject = subject
            print(f"Auto-added {subject} to class {target_class}")
        else:
            # If it IS there, find the official casing
            for s in mapping[target_class]:
                if s.strip().lower() == search_sub:
                    official_subject = s
                    break

        # Now we have final class_name and official_subject
        class_name = target_class

        # Verify that the subject is assigned to this teacher by the HOD
        assigned_subjects = TeacherSubject.query.filter_by(teacher_id=teacher_id).all()
        assigned_subject_names = [sub.subject.strip().lower() for sub in assigned_subjects]
        
        if official_subject.strip().lower() not in assigned_subject_names:
            return jsonify({
                'success': False, 
                'message': f'Warning: You can only assign subjects that the HOD has assigned to you. Assigned: {", ".join([sub.subject for sub in assigned_subjects]) or "None"}'
            }), 400

        # Refined Clash Detection: Check if another teacher is already taking THIS class at THIS time
        existing_class_record = TeacherTimetable.query.filter(
            TeacherTimetable.day == day,
            TeacherTimetable.period == period,
            TeacherTimetable.class_name == class_name,
            TeacherTimetable.teacher_id != teacher_id
        ).first()
        
        if existing_class_record:
            return jsonify({'success': False, 'message': f'Class {class_name} is busy with {existing_class_record.subject} (Teacher: {existing_class_record.teacher.name})'}), 400

    # Find or create record
    record = TeacherTimetable.query.filter_by(teacher_id=teacher_id, day=day, period=period).first()
    
    if subject:
        if record:
            record.subject = official_subject
            record.class_name = class_name
        else:
            new_record = TeacherTimetable(teacher_id=teacher_id, day=day, period=period, subject=official_subject, class_name=class_name)
            db.session.add(new_record)
    else:
        # If subject is empty/None, remove the record
        if record:
            db.session.delete(record)
            
    db.session.commit()
    return jsonify({'success': True, 'class_name': class_name})

@app.route('/student/timetable')
def student_timetable():
    if session.get('role') != 'Student': return redirect(url_for('login'))
    
    student = User.query.get(session.get('user_id'))
    student_class = student.department # department field stores Class for students
    
    # Fetch all timetable records for this student's class
    timetable_records = TeacherTimetable.query.filter_by(class_name=student_class).all()
    
    # Organize: {day: {period: {subject: subject, teacher: teacher_name}}}
    timetable_data = {}
    for record in timetable_records:
        if record.day not in timetable_data: timetable_data[record.day] = {}
        timetable_data[record.day][record.period] = {
            'subject': record.subject,
            'teacher': record.teacher.name
        }
        
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
    periods = range(1, 8)
    
    return render_template('student/timetable.html', 
                           timetable_data=timetable_data, 
                           days=days, 
                           periods=periods,
                           student_class=student_class)

# Helper for HOD allowed subjects
def get_hod_allowed_subjects(dept):
    mapping = get_class_subject_mapping()
    dept_classes = []
    
    if 'computer' in dept.lower():
        dept_classes = ['IBCA', 'IIBCA', 'IIIBCA', 'BSC']
    elif 'commerce' in dept.lower():
        dept_classes = ['IBCOM', 'IIBCOM', 'IIIBCOM']
    elif 'business' in dept.lower():
        dept_classes = ['IBBA', 'IIBBA', 'IIIBBA']
    elif 'language' in dept.lower():
        language_subjects = ['english', 'kannada', 'kannada/hindi', 'hindi']
        for c_name, subs in mapping.items():
            if any(s.lower() in language_subjects for s in subs):
                dept_classes.append(c_name)
    else:
        dept_classes = list(mapping.keys())
        
    all_subjects = []
    language_subjects_lower = ['english', 'kannada', 'kannada/hindi', 'hindi']
    
    for c_name, subjects in mapping.items():
        if c_name in dept_classes:
            for s in subjects:
                is_lang = s.lower() in language_subjects_lower
                if 'language' in dept.lower():
                    if is_lang:
                        all_subjects.append(s)
                else:
                    if not is_lang:
                        all_subjects.append(s)
                        
    return dept_classes, sorted(list(set(all_subjects)))

# HOD Routes
@app.route('/hod/assign_subjects')
def hod_assign_subjects():
    if session.get('role') != 'Teacher': return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user or not user.is_hod: 
        flash('Access denied. HOD only.', 'danger')
        return redirect(url_for('dashboard'))
    
    dept = user.department
    dept_classes, all_subjects = get_hod_allowed_subjects(dept)
    mapping = get_class_subject_mapping()
    
    # Get all teachers in this department
    dept_teachers = User.query.filter_by(role='Teacher', department=dept).all()
    
    # Build timetable and subject data for each teacher
    teachers_data = []
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
    periods = range(1, 8)
    
    for teacher in dept_teachers:
        # Load Timetable
        records = TeacherTimetable.query.filter_by(teacher_id=teacher.id).all()
        tt = {}
        for rec in records:
            if rec.day not in tt: tt[rec.day] = {}
            tt[rec.day][rec.period] = {
                'subject': rec.subject,
                'class_name': rec.class_name
            }
            
        # Load Assigned Subjects
        assigned_subs = TeacherSubject.query.filter_by(teacher_id=teacher.id).all()
        assigned_subs_list = [s.subject for s in assigned_subs]
            
        teachers_data.append({
            'id': teacher.id,
            'name': teacher.name,
            'is_hod': teacher.is_hod,
            'timetable': tt,
            'assigned_subjects': assigned_subs_list
        })
    
    # Build class timetables
    classes_timetable = {}
    for c_name in dept_classes:
        records = TeacherTimetable.query.filter_by(class_name=c_name).all()
        tt = {}
        for rec in records:
            if rec.day not in tt: tt[rec.day] = {}
            tt[rec.day][rec.period] = {
                'subject': rec.subject,
                'teacher': rec.teacher.name if rec.teacher else 'Unknown'
            }
        classes_timetable[c_name] = tt

    # Load mentor data for department classes
    mentors_path = os.path.join(app.root_path, 'mentors_data.json')
    mentors_data = []
    if os.path.exists(mentors_path):
        try:
            with open(mentors_path, 'r') as f:
                mentors_data = json.load(f)
        except: pass
    
    # Filter to only dept classes
    dept_mentors = {}
    for item in mentors_data:
        if item.get('class_name') in dept_classes:
            dept_mentors[item['class_name']] = {
                'mentor1': item.get('mentor1', ''),
                'mentor2': item.get('mentor2', '')
            }
    # Ensure all dept classes have an entry
    for c in dept_classes:
        if c not in dept_mentors:
            dept_mentors[c] = {'mentor1': '', 'mentor2': ''}
    
    # Build teacher name list for dropdown
    dept_teacher_names = [t.name for t in dept_teachers]

    return render_template('teacher/hod_assign.html',
                           teachers_data=teachers_data,
                           all_subjects=all_subjects,
                           class_mapping=mapping,
                           days=days,
                           periods=periods,
                           department=dept,
                           dept_classes=dept_classes,
                           classes_timetable=classes_timetable,
                           dept_mentors=dept_mentors,
                           dept_teacher_names=dept_teacher_names)

@app.route('/api/hod_update_mentor', methods=['POST'])
def hod_update_mentor():
    if session.get('role') != 'Teacher': return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    user = User.query.get(session['user_id'])
    if not user or not user.is_hod: return jsonify({'success': False, 'message': 'HOD access required'}), 403
    
    data = request.json
    class_name = data.get('class_name')
    mentor1 = data.get('mentor1', '').strip()
    mentor2 = data.get('mentor2', '').strip()
    
    if not class_name:
        return jsonify({'success': False, 'message': 'Class name is required'}), 400
    
    # Verify this class belongs to HOD's department
    dept = user.department
    dept_classes, _ = get_hod_allowed_subjects(dept)
    if class_name not in dept_classes:
        return jsonify({'success': False, 'message': 'This class is not in your department'}), 403
    
    # Validate that both mentors are teachers in this department
    dept_teachers = User.query.filter_by(role='Teacher', department=dept).all()
    dept_teacher_names = [t.name for t in dept_teachers]
    
    if mentor1 and mentor1 not in dept_teacher_names:
        return jsonify({'success': False, 'message': f'{mentor1} is not a teacher in your department'}), 400
    if mentor2 and mentor2 not in dept_teacher_names:
        return jsonify({'success': False, 'message': f'{mentor2} is not a teacher in your department'}), 400
    
    if mentor1 and mentor2 and mentor1 == mentor2:
        return jsonify({'success': False, 'message': 'Mentor 1 and Mentor 2 cannot be the same person'}), 400
    
    # Load current mentors data
    mentors_path = os.path.join(app.root_path, 'mentors_data.json')
    mentors_data = []
    if os.path.exists(mentors_path):
        try:
            with open(mentors_path, 'r') as f:
                mentors_data = json.load(f)
        except: pass
    
    # Rule: One teacher can only mentor ONE class
    # Build a map of teacher -> class they currently mentor (excluding the current class being edited)
    teacher_to_class = {}
    for item in mentors_data:
        if item.get('class_name') != class_name:
            if item.get('mentor1'):
                teacher_to_class[item['mentor1']] = item['class_name']
            if item.get('mentor2'):
                teacher_to_class[item['mentor2']] = item['class_name']
    
    if mentor1 and mentor1 in teacher_to_class:
        return jsonify({'success': False, 'message': f'{mentor1} is already a mentor for {teacher_to_class[mentor1]}. A teacher can only mentor one class.'}), 400
    if mentor2 and mentor2 in teacher_to_class:
        return jsonify({'success': False, 'message': f'{mentor2} is already a mentor for {teacher_to_class[mentor2]}. A teacher can only mentor one class.'}), 400
    
    # Rule: HOD should NOT get mentorship unless all other non-HOD teachers already have a mentorship
    hod_teacher = None
    non_hod_teachers = []
    for t in dept_teachers:
        if t.is_hod:
            hod_teacher = t
        else:
            non_hod_teachers.append(t)
    
    # Check if mentor1 or mentor2 is the HOD
    for mentor_name in [mentor1, mentor2]:
        if mentor_name and hod_teacher and mentor_name == hod_teacher.name:
            # Check if all non-HOD teachers already have mentorship
            # Include existing assignments + the OTHER mentor being set in this request
            all_mentored = set(teacher_to_class.keys())
            # Also count the other mentor in this same request
            other_mentor = mentor2 if mentor_name == mentor1 else mentor1
            if other_mentor:
                all_mentored.add(other_mentor)
            
            unassigned = [t.name for t in non_hod_teachers if t.name not in all_mentored]
            if unassigned:
                return jsonify({'success': False, 'message': f'HOD can only be assigned as mentor when all other teachers have mentorship. Unassigned teachers: {", ".join(unassigned)}'}), 400
    
    # Find and update or create the entry
    found = False
    old_mentor1 = None
    old_mentor2 = None
    for item in mentors_data:
        if item.get('class_name') == class_name:
            old_mentor1 = item.get('mentor1')
            old_mentor2 = item.get('mentor2')
            item['mentor1'] = mentor1
            item['mentor2'] = mentor2
            found = True
            break
            
    if old_mentor1 and old_mentor1 != mentor1 and old_mentor1 != mentor2:
        add_notice(old_mentor1, f'Notice: You have been removed as mentor for class {class_name}.')
    if old_mentor2 and old_mentor2 != mentor1 and old_mentor2 != mentor2:
        add_notice(old_mentor2, f'Notice: You have been removed as mentor for class {class_name}.')
        
    if mentor1 and mentor1 != old_mentor1 and mentor1 != old_mentor2:
        if mentor1 not in teacher_to_class:
            add_notice(mentor1, f'Congratulations! You have been assigned as a mentor for class {class_name} for the first time.')
        else:
            add_notice(mentor1, f'Notice: You have been assigned as a mentor for class {class_name}.')
            
    if mentor2 and mentor2 != old_mentor1 and mentor2 != old_mentor2:
        if mentor2 not in teacher_to_class:
            add_notice(mentor2, f'Congratulations! You have been assigned as a mentor for class {class_name} for the first time.')
        else:
            add_notice(mentor2, f'Notice: You have been assigned as a mentor for class {class_name}.')
    
    if not found:
        mentors_data.append({
            'class_name': class_name,
            'mentor1': mentor1,
            'mentor2': mentor2
        })
    
    with open(mentors_path, 'w') as f:
        json.dump(mentors_data, f, indent=4)
    
    return jsonify({'success': True, 'message': f'Mentors updated for {class_name}'})

@app.route('/api/hod_assign_teacher_subject', methods=['POST'])
def hod_assign_teacher_subject():
    if session.get('role') != 'Teacher': return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    user = User.query.get(session['user_id'])
    if not user or not user.is_hod: return jsonify({'success': False, 'message': 'HOD access required'}), 403
    
    data = request.json
    teacher_id = data.get('teacher_id')
    subject = data.get('subject')
    action = data.get('action') # 'add' or 'remove'
    
    if not teacher_id or not subject or not action:
        return jsonify({'success': False, 'message': 'Missing data'}), 400
        
    target_teacher = User.query.get(teacher_id)
    if not target_teacher or target_teacher.department != user.department:
        return jsonify({'success': False, 'message': 'Invalid teacher'}), 400
        
    existing = TeacherSubject.query.filter_by(teacher_id=teacher_id, subject=subject).first()
    
    if action == 'add':
        dept_classes, allowed_subjects = get_hod_allowed_subjects(user.department)
        allowed_subjects_lower = [s.strip().lower() for s in allowed_subjects]
        
        if subject.strip().lower() not in allowed_subjects_lower:
            return jsonify({'success': False, 'message': f"You can only assign {user.department} subjects."}), 400
            
        existing_global = TeacherSubject.query.filter(db.func.lower(TeacherSubject.subject) == subject.strip().lower()).first()
        if existing_global and existing_global.teacher_id != teacher_id:
            return jsonify({'success': False, 'message': f"'{subject}' is already assigned to {existing_global.teacher.name}. One subject per teacher."}), 400
        
        # One-subject-per-class restriction: find which class this subject belongs to
        mapping = get_class_subject_mapping()
        subject_class = None
        for c_name, subs in mapping.items():
            if any(subject.strip().lower() == s.strip().lower() for s in subs):
                subject_class = c_name
                break
        
        if subject_class:
            # Check if this teacher already has another subject assigned from the same class
            existing_teacher_subs = TeacherSubject.query.filter_by(teacher_id=teacher_id).all()
            for ts in existing_teacher_subs:
                for c_name, subs in mapping.items():
                    if c_name == subject_class and any(ts.subject.strip().lower() == s.strip().lower() for s in subs):
                        if ts.subject.strip().lower() != subject.strip().lower():
                            return jsonify({'success': False, 'message': f"This teacher already has '{ts.subject}' assigned from class {subject_class}. Only one subject per class is allowed."}), 400
            
        if not existing:
            new_sub = TeacherSubject(teacher_id=teacher_id, subject=subject)
            db.session.add(new_sub)
    elif action == 'remove':
        if existing:
            db.session.delete(existing)
            # Cascade: also remove all timetable entries for this teacher+subject
            timetable_entries = TeacherTimetable.query.filter_by(teacher_id=teacher_id, subject=subject).all()
            for entry in timetable_entries:
                db.session.delete(entry)
            
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/hod_save_timetable', methods=['POST'])
def hod_save_timetable():
    if session.get('role') != 'Teacher': return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    user = User.query.get(session['user_id'])
    if not user or not user.is_hod:
        return jsonify({'success': False, 'message': 'HOD access required'}), 403
    
    data = request.json
    target_teacher_id = data.get('teacher_id')
    day = data.get('day')
    subject = data.get('subject')
    
    try:
        period = int(data.get('period'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'Invalid period'}), 400
    
    if not day or not period or not target_teacher_id:
        return jsonify({'success': False, 'message': 'Missing data'}), 400
    
    # Verify the target teacher is in the same department
    target_teacher = User.query.get(target_teacher_id)
    if not target_teacher or target_teacher.department != user.department:
        return jsonify({'success': False, 'message': 'Teacher not in your department'}), 403
    
    class_name = None
    official_subject = subject
    
    if subject:
        mapping = get_class_subject_mapping()
        search_sub = subject.strip().lower()
        
        # Find which class this subject belongs to
        for c_name, subjects_list in mapping.items():
            for s in subjects_list:
                if search_sub == s.strip().lower():
                    class_name = c_name
                    official_subject = s
                    break
            if class_name: break
            
        # HOD Authorization check for this subject
        dept_classes, allowed_subjects = get_hod_allowed_subjects(user.department)
        allowed_subjects_lower = [s.strip().lower() for s in allowed_subjects]
        if search_sub not in allowed_subjects_lower:
            return jsonify({'success': False, 'message': f"You can only assign {user.department} subjects."}), 400
        
        if not class_name:
            class_name = 'General'
        
        # Check clash: another teacher already taking this class at this time
        existing = TeacherTimetable.query.filter(
            TeacherTimetable.day == day,
            TeacherTimetable.period == period,
            TeacherTimetable.class_name == class_name,
            TeacherTimetable.teacher_id != target_teacher_id
        ).first()
        
        if existing:
            return jsonify({'success': False, 'message': f'Class {class_name} is busy with {existing.subject} (Teacher: {existing.teacher.name})'}), 400
            
        # Ensure subject is exclusively assigned to this teacher
        existing_sub_assignment = TeacherSubject.query.filter(db.func.lower(TeacherSubject.subject) == official_subject.strip().lower()).first()
        if existing_sub_assignment and existing_sub_assignment.teacher_id != target_teacher_id:
            return jsonify({'success': False, 'message': f"'{official_subject}' is already exclusively assigned to {existing_sub_assignment.teacher.name}."}), 400
            
        # Check if this subject for this class is already assigned to ANOTHER teacher in timetable
        existing_subject_teacher = TeacherTimetable.query.filter(
            TeacherTimetable.class_name == class_name,
            TeacherTimetable.subject == official_subject,
            TeacherTimetable.teacher_id != target_teacher_id
        ).first()
        
        if existing_subject_teacher:
            return jsonify({'success': False, 'message': f'{official_subject} for {class_name} is already assigned to {existing_subject_teacher.teacher.name}'}), 400
    
    # Find or create record
    record = TeacherTimetable.query.filter_by(teacher_id=target_teacher_id, day=day, period=period).first()
    
    if subject:
        if record:
            record.subject = official_subject
            record.class_name = class_name
        else:
            new_record = TeacherTimetable(teacher_id=target_teacher_id, day=day, period=period, subject=official_subject, class_name=class_name)
            db.session.add(new_record)
            
        # Automatically add to Assigned Subjects if not present
        existing_sub = TeacherSubject.query.filter_by(teacher_id=target_teacher_id, subject=official_subject).first()
        if not existing_sub:
            db.session.add(TeacherSubject(teacher_id=target_teacher_id, subject=official_subject))
    else:
        if record:
            db.session.delete(record)
    
    db.session.commit()
    return jsonify({'success': True, 'class_name': class_name})

# HOD: Teacher Leaves for Today & Tomorrow
@app.route('/hod/teacher_leaves')
def hod_teacher_leaves():
    if session.get('role') != 'Teacher': return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user or not user.is_hod:
        flash('Access denied. HOD only.', 'danger')
        return redirect(url_for('dashboard'))

    from datetime import date
    today = date.today()
    tomorrow = today + timedelta(days=1)

    # Fetch all approved teacher leave requests
    all_teacher_leaves = LeaveRequest.query.filter(
        LeaveRequest.role == 'Teacher',
        LeaveRequest.status == 'Approved'
    ).order_by(LeaveRequest.dates).all()

    def overlaps_window(dates_str, start_window, end_window):
        """Return True if the leave date range overlaps [start_window, end_window]."""
        try:
            if ' to ' in dates_str.lower():
                parts = dates_str.lower().split('to')
                leave_start = datetime.strptime(parts[0].strip(), '%d-%m-%Y').date()
                leave_end   = datetime.strptime(parts[1].strip(), '%d-%m-%Y').date()
            else:
                leave_start = leave_end = datetime.strptime(dates_str.strip(), '%d-%m-%Y').date()
            return leave_start <= end_window and leave_end >= start_window
        except Exception:
            return False

    tomorrow_leaves = []
    today_leaves     = []

    for leave in all_teacher_leaves:
        if overlaps_window(leave.dates, tomorrow, tomorrow):
            tomorrow_leaves.append(leave)
        if overlaps_window(leave.dates, today, today):
            today_leaves.append(leave)

    # Attach timetable details for each day
    today_weekday = today.strftime('%A')
    tomorrow_weekday = tomorrow.strftime('%A')

    for leave in today_leaves:
        leave.scheduled_classes = TeacherTimetable.query.filter_by(
            teacher_id=leave.user_id,
            day=today_weekday
        ).order_by(TeacherTimetable.period).all()

    for leave in tomorrow_leaves:
        leave.scheduled_classes = TeacherTimetable.query.filter_by(
            teacher_id=leave.user_id,
            day=tomorrow_weekday
        ).order_by(TeacherTimetable.period).all()

    return render_template('teacher/hod_teacher_leaves.html',
                           tomorrow_leaves=tomorrow_leaves,
                           today_leaves=today_leaves,
                           today=today,
                           tomorrow=tomorrow)

# AI Integration with Ollama
@app.route('/ai/ask', methods=['POST'])
def ai_ask():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_query = request.json.get('query')
    if not user_query:
        return jsonify({'error': 'No query provided'}), 400

    role = session.get('role')
    user_name = session.get('name')
    
    # System prompt to give context
    system_prompt = f"You are an AI assistant for a College Leave Management System. The current user is {user_name} with the role of {role}. Answer concisely."
    
    try:
        # Calling local Ollama API (Assumes llama3 is installed, fallback to llama2)
        response = requests.post('http://localhost:11434/api/generate', 
            json={
                'model': 'llama3', 
                'prompt': f"{system_prompt}\nUser: {user_query}",
                'stream': False
            }, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            return jsonify({'response': result['response']})
        else:
            return jsonify({'error': 'Ollama server error'}), 500
    except requests.exceptions.ConnectionError:
        return jsonify({'error': 'Ollama not running. Please start Ollama on your machine.'}), 503
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    db.session.commit()
    return redirect(request.referrer)

# Background Task for Morning Notifications
import time
def notification_scheduler():
    while True:
        with app.app_context():
            try:
                today_str = datetime.now().strftime('%d-%m-%Y')
                today_name = datetime.now().strftime('%A')
                
                # Find all approved student leaves that involve today
                # This is a bit complex due to string storage, but we check if today_str is in active dates
                active_leaves = LeaveRequest.query.filter_by(status='Approved', role='Student').all()
                
                for leave in active_leaves:
                    # Check if today is one of the leave dates
                    is_today = False
                    if ' to ' in leave.dates:
                        start_str, end_str = leave.dates.split(' to ')
                        start_dt = datetime.strptime(start_str.strip(), '%d-%m-%Y').date()
                        end_dt = datetime.strptime(end_str.strip(), '%d-%m-%Y').date()
                        if start_dt <= datetime.now().date() <= end_dt:
                            is_today = True
                    elif leave.dates.strip() == today_str:
                        is_today = True
                    
                    if is_today:
                        # Check "3-day only" rule: only notify if today is within the first 3 days of the leave
                        if ' to ' in leave.dates:
                            start_str = leave.dates.split(' to ')[0].strip()
                            start_dt = datetime.strptime(start_str, '%d-%m-%Y').date()
                            day_index = (datetime.now().date() - start_dt).days
                            if day_index >= 3: # Day 0, 1, 2 are notified. Day 3+ (4th day onwards) is NOT.
                                is_today = False
                    
                    if is_today:
                        student_class = leave.user.department
                        # Find teachers for today
                        subject_teachers = TeacherTimetable.query.filter_by(class_name=student_class, day=today_name).all()
                        
                        teacher_map = {}
                        for entry in subject_teachers:
                            if entry.teacher_id not in teacher_map: teacher_map[entry.teacher_id] = []
                            teacher_map[entry.teacher_id].append(entry.subject)
                            
                        for t_id, subjects in teacher_map.items():
                            socketio.emit('subject_teacher_absence_alert', {
                                'student_name': leave.user.name,
                                'class_name': student_class,
                                'dates': "Today",
                                'impacts': [f"{s} (Today)" for s in subjects]
                            }, to=f"user_{t_id}")
                            
            except Exception as e:
                print(f"Scheduler error: {e}")
        
        # Run every 6 hours (adjust as needed)
        time.sleep(21600)

# Start scheduler in a background thread
scheduler_thread = threading.Thread(target=notification_scheduler, daemon=True)
scheduler_thread.start()

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)
