import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from datetime import datetime
from werkzeug.utils import secure_filename
import threading
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'default_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URI', 'sqlite:///database_v2.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
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

def is_absent_today(date_str):
    """
    Checks if today's date falls within the provided date_str.
    Expects formats like 'DD-MM-YYYY' or 'DD-MM-YYYY to DD-MM-YYYY'
    """
    from datetime import date
    today = date.today()
    
    try:
        if 'to' in date_str.lower():
            start_str, end_str = date_str.lower().split('to')
            start_date = datetime.strptime(start_str.strip(), '%d-%m-%Y').date()
            end_date = datetime.strptime(end_str.strip(), '%d-%m-%Y').date()
            return start_date <= today <= end_date
        else:
            single_date = datetime.strptime(date_str.strip(), '%d-%m-%Y').date()
            return single_date == today
    except:
        # Fallback: simple string inclusion check if format is non-standard
        today_str = today.strftime('%d-%m-%Y')
        return today_str in date_str

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

class LeaveRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    dates = db.Column(db.String(100), nullable=False)
    document_path = db.Column(db.String(200)) # Path to uploaded file
    status = db.Column(db.String(20), default='Pending') # Pending, Approved, Rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationship to user
    user = db.relationship('User', backref=db.backref('leaves', lazy=True))

class TeacherTimetable(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    day = db.Column(db.String(20), nullable=False) # Monday, Tuesday, etc.
    period = db.Column(db.Integer, nullable=False) # 1, 2, 3, etc.
    subject = db.Column(db.String(100), nullable=False)
    
    teacher = db.relationship('User', backref=db.backref('timetable', lazy=True))

# Create Database and Admin
with app.app_context():
    db.create_all()
    # Check if admin exists
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(name='Administrator', role='Admin', username='admin', password='admin123')
        db.session.add(admin)
        db.session.commit()
    
    # Always sync users from JSON on startup to keep data fresh
    import json
    json_path = os.path.join(app.root_path, 'users_data.json')
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
            
            # Clear existing non-admin users to remove "fake" or old data
            # This ensures the database EXACTLY matches the JSON file
            User.query.filter(User.role != 'Admin').delete()
            
            for user_data in data:
                new_user = User(
                    name=user_data['name'],
                    role=user_data['role'],
                    username=user_data['username'],
                    password=user_data['password'],
                    department=user_data['department'],
                    email=user_data.get('email'),
                    phone=user_data.get('phone'),
                    dob=user_data.get('dob'),
                    roll_no=user_data.get('roll_no')
                )
                db.session.add(new_user)
            
            db.session.commit()
            print("Successfully synchronized users from JSON (Clean Sync).")
        except Exception as e:
            print(f"Error syncing JSON: {e}")
            db.session.rollback()

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
    
    if role == 'Admin':
        teacher_count = User.query.filter_by(role='Teacher').count()
        student_count = User.query.filter_by(role='Student').count()
        pending_leaves = LeaveRequest.query.filter_by(status='Pending', role='Teacher').count()
        return render_template('admin/dashboard.html', teacher_count=teacher_count, student_count=student_count, pending_leaves=pending_leaves)
    
    elif role == 'Teacher':
        pending_student_leaves = LeaveRequest.query.filter_by(status='Pending', role='Student').count()
        my_leaves = LeaveRequest.query.filter_by(user_id=session['user_id']).all()
        return render_template('teacher/dashboard.html', pending_student_leaves=pending_student_leaves, my_leaves=my_leaves)
    
    elif role == 'Student':
        my_leaves = LeaveRequest.query.filter_by(user_id=session['user_id']).all()
        return render_template('student/dashboard.html', my_leaves=my_leaves)
        
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# Admin Routes
@app.route('/admin/teachers')
def manage_teachers():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    teachers = User.query.filter_by(role='Teacher').all()
    return render_template('admin/teachers.html', teachers=teachers)

@app.route('/admin/students')
def manage_students():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    students = User.query.filter_by(role='Student').all()
    return render_template('admin/students.html', students=students)

@app.route('/admin/leaves')
def view_all_leaves():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    # Admin views: 1. All Teacher leaves, 2. Student leaves forwarded to Admin
    leaves = LeaveRequest.query.filter(
        (LeaveRequest.role == 'Teacher') | 
        (LeaveRequest.status == 'Forwarded to Admin')
    ).all()
    return render_template('admin/leaves.html', leaves=leaves)

@app.route('/admin/absentees')
def view_absentees():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    # Get all approved leaves
    approved_leaves = LeaveRequest.query.filter_by(status='Approved').all()
    
    absent_teachers = {}
    absent_students = {}
    
    for leave in approved_leaves:
        if is_absent_today(leave.dates):
            if leave.role == 'Teacher':
                dept = leave.user.department
                if dept not in absent_teachers: absent_teachers[dept] = []
                absent_teachers[dept].append(leave.user)
            else:
                cls = leave.user.department # department field stores Class for students
                if cls not in absent_students: absent_students[cls] = []
                absent_students[cls].append(leave.user)
                
    return render_template('admin/absentees.html', 
                            absent_teachers=absent_teachers, 
                            absent_students=absent_students)

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

    # Filter leaves: Student requests where student department is in mentored_classes
    leaves = LeaveRequest.query.join(User, LeaveRequest.user_id == User.id)\
                               .filter(User.role == 'Student')\
                               .filter(User.department.in_(mentored_classes)).all()
                               
    return render_template('teacher/student_leaves.html', leaves=leaves, mentored_classes=mentored_classes)

# General Routes
@app.route('/apply_leave', methods=['GET', 'POST'])
def apply_leave():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        reason = request.form.get('reason')
        dates = request.form.get('dates')
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
            if ' to ' in dates.lower():
                start_str = dates.lower().split('to')[0].strip()
            else:
                start_str = dates.strip()
            
            # Use %d-%m-%Y for parsing
            requested_start = datetime.strptime(start_str, '%d-%m-%Y').date()
            if requested_start < today:
                flash(f'Cannot apply for past dates! Today is {today.strftime("%d-%m-%Y")}.', 'warning')
                return redirect(request.referrer)
            
            # 9 AM Cutoff Logic for Today's Leave (Students only)
            if session.get('role') == 'Student' and requested_start == today:
                now_time = datetime.now().time()
                cutoff_time = datetime.strptime("09:00:00", "%H:%M:%S").time()
                if now_time >= cutoff_time:
                    flash('Same-day leave must be applied before 9:00 AM!', 'danger')
                    return redirect(request.referrer)
        except Exception as e:
            flash(f'Error parsing dates: {e}', 'warning')
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
            document_path=filename
        )
        db.session.add(new_leave)
        db.session.commit()
        flash('Leave request submitted!', 'success')
        return redirect(url_for('dashboard'))
    
    if session['role'] == 'Teacher':
        return render_template('teacher/apply_leave.html')
    else:
        return render_template('student/apply_leave.html')

@app.route('/update_leave/<int:leave_id>/<string:status>')
def update_leave(leave_id, status):
    if 'user_id' not in session: return redirect(url_for('login'))
    leave = LeaveRequest.query.get(leave_id)
    if not leave: return redirect(url_for('dashboard'))
    
    current_role = session.get('role')
    
    # Admin can approve/reject Teacher leaves OR Student leaves forwarded to them
    if current_role == 'Admin':
        if leave.role == 'Teacher' or leave.status == 'Forwarded to Admin':
            leave.status = status
        else:
            flash('Unauthorized for this request', 'danger')
            return redirect(url_for('dashboard'))
            
    # Teacher (Mentor) can approve/reject/forward Student leaves
    elif current_role == 'Teacher' and leave.role == 'Student':
        leave.status = status
    else:
        flash('Unauthorized action', 'danger')
        return redirect(url_for('dashboard'))
        
    db.session.commit()
    
    # Send Email Notification if Approved
    if status == 'Approved' and leave.user.email:
        send_approval_email(leave.user.email, leave.user.name, leave.role, leave.dates)
        
    flash(f'Leave updated to {status} successfully!', 'info')
    return redirect(request.referrer)

# Timetable Management Routes
def get_class_subject_mapping():
    json_path = os.path.join(app.root_path, 'class_subjects.json')
    if os.path.exists(json_path):
        with open(json_path, 'r') as f:
            return json.load(f)
    return {}

def get_class_from_subject(subject_name):
    mapping = get_class_subject_mapping()
    for class_name, subjects in mapping.items():
        if subject_name in subjects:
            return class_name
    return None

@app.route('/teacher/timetable')
def teacher_timetable():
    if session.get('role') != 'Teacher': return redirect(url_for('login'))
    
    teacher_id = session.get('user_id')
    timetable_records = TeacherTimetable.query.filter_by(teacher_id=teacher_id).all()
    
    # Organize into a dict for easy access: {day: {period: subject}}
    timetable_data = {}
    for record in timetable_records:
        if record.day not in timetable_data: timetable_data[record.day] = {}
        timetable_data[record.day][record.period] = record.subject
        
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
    periods = range(1, 8) # 7 periods
    
    mapping = get_class_subject_mapping()
    all_subjects = []
    for subjects in mapping.values():
        all_subjects.extend(subjects)
    all_subjects = sorted(list(set(all_subjects)))
    
    return render_template('teacher/timetable.html', 
                           timetable_data=timetable_data, 
                           days=days, 
                           periods=periods,
                           all_subjects=all_subjects,
                           class_mapping=mapping)

@app.route('/api/save_timetable', methods=['POST'])
def save_timetable():
    if session.get('role') != 'Teacher': return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    data = request.json
    teacher_id = session.get('user_id')
    day = data.get('day')
    period = data.get('period')
    subject = data.get('subject')
    
    if not day or not period:
        return jsonify({'success': False, 'message': 'Missing data'}), 400

    # Determine class for the subject
    class_name = get_class_from_subject(subject) if subject else None
    
    # VALIDATION 1: Check if class already has a subject at this time (from another teacher)
    if subject:
        if not class_name:
            return jsonify({'success': False, 'message': f'Subject "{subject}" not mapped to any class.'}), 400
            
        # Find all subjects mapped to this class
        mapping = get_class_subject_mapping()
        class_subjects = mapping.get(class_name, [])
        
        existing_class_record = TeacherTimetable.query.filter(
            TeacherTimetable.day == day,
            TeacherTimetable.period == period,
            TeacherTimetable.subject.in_(class_subjects),
            TeacherTimetable.teacher_id != teacher_id
        ).first()
        
        if existing_class_record:
            return jsonify({'success': False, 'message': f'Class {class_name} already has {existing_class_record.subject} at this time (Teacher: {existing_class_record.teacher.name})'}), 400

    # VALIDATION 2: Prevent teacher having multiple classes at same time 
    # (Implicitly handled if we only allow one subject per teacher per slot, but let's be explicit)
    # Actually, in the teacher's own grid, they are just updating their own slot.
    # But if they try to assign a subject that belongs to a class which is ALREADY busy with another teacher, we caught it above.
    
    # Find or create record
    record = TeacherTimetable.query.filter_by(teacher_id=teacher_id, day=day, period=period).first()
    
    if subject:
        if record:
            record.subject = subject
        else:
            new_record = TeacherTimetable(teacher_id=teacher_id, day=day, period=period, subject=subject)
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
    
    # Get subjects for this class
    mapping = get_class_subject_mapping()
    class_subjects = mapping.get(student_class, [])
    
    # Fetch all teacher timetable records that involve these subjects
    timetable_records = TeacherTimetable.query.filter(TeacherTimetable.subject.in_(class_subjects)).all()
    
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

if __name__ == '__main__':
    app.run(debug=True)
