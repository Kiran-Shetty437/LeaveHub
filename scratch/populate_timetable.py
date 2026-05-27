import os
import json
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///c:/D/Code_Predators/instance/database_v2.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    department = db.Column(db.String(100))

class TeacherTimetable(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    day = db.Column(db.String(20), nullable=False)
    period = db.Column(db.Integer, nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    class_name = db.Column(db.String(50))

with app.app_context():
    with open('c:/D/Code_Predators/class_subjects.json', 'r') as f:
        curriculum = json.load(f)
    
    teachers = User.query.filter_by(role='Teacher').all()
    # Group teachers by department
    dept_teachers = {}
    for t in teachers:
        if t.department not in dept_teachers: dept_teachers[t.department] = []
        dept_teachers[t.department].append(t)
    
    # Rule Tracking
    # 1. teacher_subject_map: {(teacher_id, class_name): subject}
    #    Ensures a teacher takes only ONE specific subject in a specific class.
    teacher_subject_map = {}
    
    # 2. time_occupancy: {(teacher_id, day, period): True}
    #    Ensures a teacher is not in two places at once.
    time_occupancy = {}

    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
    periods = [1, 2, 3, 4, 6, 7]
    
    TeacherTimetable.query.delete()
    
    for class_name, subjects in curriculum.items():
        # Cycle through subjects for each slot
        for day in days:
            for period in periods:
                # Pick a subject for this slot (simple cycling)
                slot_idx = (days.index(day) * len(periods) + periods.index(period))
                subject = subjects[slot_idx % len(subjects)]
                
                # Determine department for this subject
                is_lang = any(x in subject.lower() for x in ['english', 'kannada', 'hindi', 'communication', 'language'])
                if is_lang: target_dept = 'Language'
                elif 'BCA' in class_name: target_dept = 'Computer Science'
                elif 'BCOM' in class_name: target_dept = 'Commerce'
                else: target_dept = 'Business Administration'
                
                pool = dept_teachers.get(target_dept, [])
                if not pool: pool = teachers # Total fallback
                
                assigned_teacher = None
                
                # Try to find a teacher who already takes this subject in this class
                for t in pool:
                    # Condition A: Not occupied at this time
                    if (t.id, day, period) in time_occupancy: continue
                    
                    # Condition B: If they already take a subject in this class, it MUST be THIS subject
                    existing_sub = teacher_subject_map.get((t.id, class_name))
                    if existing_sub and existing_sub != subject: continue
                    
                    # If we reach here, this teacher is a candidate
                    assigned_teacher = t
                    break
                
                # If no existing teacher found, pick any available who hasn't been assigned a different subject in this class
                if not assigned_teacher:
                    for t in pool:
                        if (t.id, day, period) in time_occupancy: continue
                        if (t.id, class_name) in teacher_subject_map: continue # Already taking a different subject here
                        
                        assigned_teacher = t
                        break
                
                if assigned_teacher:
                    new_entry = TeacherTimetable(
                        teacher_id=assigned_teacher.id,
                        day=day,
                        period=period,
                        subject=subject,
                        class_name=class_name
                    )
                    db.session.add(new_entry)
                    time_occupancy[(assigned_teacher.id, day, period)] = True
                    teacher_subject_map[(assigned_teacher.id, class_name)] = subject
                
    db.session.commit()
    print("Timetable repopulated: Teachers take multiple classes but dedicated subjects per class.")
