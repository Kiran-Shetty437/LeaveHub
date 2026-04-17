from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'leave_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///leaves.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Database Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(20), nullable=False) # 'admin' or 'student'

class LeaveRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    from_date = db.Column(db.String(20), nullable=False)
    to_date = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='Pending') # Pending, Approved, Rejected
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('leaves', lazy=True))

# Initialize Database
with app.app_context():
    db.create_all()
    # Create default users if not exist
    if not User.query.filter_by(username='admin').first():
        db.session.add(User(username='admin', password='admin123', role='admin'))
        db.session.add(User(username='student1', password='password123', role='student'))
        db.session.commit()

# Routes
@app.route('/')
def index():
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    user = User.query.filter_by(username=username, password=password).first()
    
    if user:
        session['user_id'] = user.id
        session['username'] = user.username
        session['role'] = user.role
        if user.role == 'admin':
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('student_dashboard'))
    return "Invalid credentials", 401

@app.route('/student')
def student_dashboard():
    if session.get('role') != 'student': return redirect(url_for('index'))
    leaves = LeaveRequest.query.filter_by(user_id=session['user_id']).order_by(LeaveRequest.timestamp.desc()).all()
    return render_template('student.html', leaves=leaves)

@app.route('/admin')
def admin_dashboard():
    if session.get('role') != 'admin': return redirect(url_for('index'))
    leaves = LeaveRequest.query.order_by(LeaveRequest.timestamp.desc()).all()
    return render_template('admin.html', leaves=leaves)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# Socket.IO Events
@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        if session['role'] == 'admin':
            join_room('admin_room')
        else:
            join_room(f"user_{session['user_id']}")
    print(f"Client connected: {request.sid}")

@socketio.on('new_leave')
def handle_new_leave(data):
    user_id = session.get('user_id')
    if not user_id: return
    
    new_request = LeaveRequest(
        user_id=user_id,
        name=data['name'],
        role=data['role'],
        reason=data['reason'],
        from_date=data['from_date'],
        to_date=data['to_date']
    )
    db.session.add(new_request)
    db.session.commit()
    
    leave_data = {
        'id': new_request.id,
        'name': new_request.name,
        'role': new_request.role,
        'reason': new_request.reason,
        'from_date': new_request.from_date,
        'to_date': new_request.to_date,
        'status': new_request.status,
        'timestamp': new_request.timestamp.strftime('%Y-%m-%d %H:%M:%S')
    }
    
    # Notify Admins instantly
    emit('receive_new_leave', leave_data, to='admin_room')

@socketio.on('update_status')
def handle_update_status(data):
    if session.get('role') != 'admin': return
    
    leave_id = data['leave_id']
    status = data['status']
    
    leave = LeaveRequest.query.get(leave_id)
    if leave:
        leave.status = status
        db.session.commit()
        
        # Notify the specific student
        emit('leave_status_updated', {
            'leave_id': leave_id,
            'status': status,
            'message': f"Your leave request has been {status}."
        }, to=f"user_{leave.user_id}")

if __name__ == '__main__':
    socketio.run(app, debug=True, port=8000)
