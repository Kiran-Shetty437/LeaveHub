import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import app
with app.app.app_context():
    user = app.User.query.filter_by(name='Priya Kapoor').first()
    if user:
        print(f"User: {user.name}, Role: {user.role}, Dept: {user.department}, HOD: {user.is_hod}, ID: {user.id}")
        # Timetable
        tt = app.TeacherTimetable.query.filter_by(teacher_id=user.id).all()
        print(f"Timetable entries: {len(tt)}")
        for entry in tt:
            print(f"  Day: {entry.day}, Period: {entry.period}, Class: {entry.class_name}, Subject: {entry.subject}")
    else:
        print("User Priya Kapoor not found")
