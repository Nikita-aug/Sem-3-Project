from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_mail import Mail, Message
import os

app = Flask(__name__)
app.secret_key = "super_secret_key"

# ---------------- Upload Configuration ----------------
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ---------------- Database Configuration ----------------
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ---------------- Mail Configuration ----------------
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'your_email@gmail.com'       # Replace with your Gmail
app.config['MAIL_PASSWORD'] = 'your_app_password'          # Replace with your Gmail App Password
mail = Mail(app)

# -------------------- Models --------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50), nullable=False)
    leaves = db.relationship('Leave', backref='user', lazy=True)

class Leave(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), nullable=False)
    days = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.Text, nullable=False)
    document = db.Column(db.String(150), nullable=True)
    status = db.Column(db.String(20), default='Pending')  # Pending, Approved, Rejected

# -------------------- Routes --------------------
@app.route('/')
def home():
    return redirect(url_for('login'))

# -------- Registration --------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = generate_password_hash(request.form['password'], method='pbkdf2:sha256')
        role = request.form['role']

        if User.query.filter_by(email=email).first():
            flash("Email already exists!")
            return redirect(url_for('register'))

        new_user = User(name=name, email=email, password=password, role=role)
        db.session.add(new_user)
        db.session.commit()
        flash("Registration successful! Please login.")
        return redirect(url_for('login'))
    return render_template('register.html')

# -------- Login --------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        role = request.form['role']

        user = User.query.filter_by(email=email, role=role).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['role'] = user.role
            session['name'] = user.name

            if user.role.lower() == 'student':
                return redirect(url_for('student_dashboard'))
            elif user.role.lower() == 'faculty':
                return redirect(url_for('faculty_dashboard'))
        else:
            flash("Invalid email, password, or role!")
            return redirect(url_for('login'))
    return render_template('login.html')

# -------- Forgot Password --------
@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        user = User.query.filter_by(email=email).first()
        if not user:
            flash("Email not found!")
            return redirect(url_for('forgot_password'))

        if new_password != confirm_password:
            flash("Passwords do not match!")
            return redirect(url_for('forgot_password'))

        user.password = generate_password_hash(new_password, method='pbkdf2:sha256')
        db.session.commit()
        flash("Password reset successful! Please login.")
        return redirect(url_for('login'))

    return render_template('forgot_password.html')

# -------- Student Dashboard --------
@app.route('/student', methods=['GET', 'POST'])
def student_dashboard():
    if 'role' not in session or session['role'].lower() != 'student':
        flash("You are not authorized to view this page.")
        return redirect(url_for('login'))

    if request.method == 'POST':
        name = session['name']
        user_email = User.query.get(session['user_id']).email

        try:
            days = int(request.form['days'])
        except (ValueError, TypeError):
            flash("Please enter a valid number for days.")
            return redirect(url_for('student_dashboard'))

        reason = request.form['reason']
        file = request.files.get('document')
        filename = None
        if file and file.filename:
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        leave = Leave(student_id=session['user_id'], name=name, email=user_email,
                      days=days, reason=reason, document=filename)
        db.session.add(leave)
        db.session.commit()
        flash("Leave application submitted!")
        return redirect(url_for('student_dashboard'))

    return render_template('student_dashboard.html', name=session['name'])

# -------- My Leave Status Page --------
@app.route('/my-leaves')
def my_leaves():
    if 'role' not in session or session['role'].lower() != 'student':
        flash("You are not authorized to view this page.")
        return redirect(url_for('login'))

    leaves = Leave.query.filter_by(student_id=session['user_id']).all()
    return render_template('my_leaves.html', name=session['name'], leaves=leaves)

# -------- Faculty Dashboard --------
@app.route('/faculty')
def faculty_dashboard():
    if 'role' not in session or session['role'].lower() != 'faculty':
        flash("You are not authorized to view this page.")
        return redirect(url_for('login'))

    leaves = Leave.query.all()
    return render_template('faculty_dashboard.html', name=session['name'], leaves=leaves)

# -------- Approve/Reject Leave with Email Handling --------
def send_email(subject, recipient, body):
    try:
        msg = Message(subject=subject, sender=app.config['MAIL_USERNAME'], recipients=[recipient], body=body)
        mail.send(msg)
        return True
    except Exception as e:
        print("Email sending failed:", e)
        return False

@app.route('/approve/<int:leave_id>', methods=['POST'])
def approve_leave(leave_id):
    if 'role' not in session or session['role'].lower() != 'faculty':
        flash("You are not authorized to perform this action.")
        return redirect(url_for('login'))

    leave = Leave.query.get(leave_id)
    if leave:
        leave.status = 'Approved'
        db.session.commit()
        flash("Leave approved successfully.")
        body = f"Hello {leave.name},\n\nYour leave request for {leave.days} day(s) has been APPROVED.\n\nReason: {leave.reason}\n\nThank you."
        if not send_email("Leave Approved", leave.email, body):
            flash("Failed to send approval email. Check mail configuration.")
    return redirect(url_for('faculty_dashboard'))

@app.route('/reject/<int:leave_id>', methods=['POST'])
def reject_leave(leave_id):
    if 'role' not in session or session['role'].lower() != 'faculty':
        flash("You are not authorized to perform this action.")
        return redirect(url_for('login'))

    leave = Leave.query.get(leave_id)
    if leave:
        leave.status = 'Rejected'
        db.session.commit()
        flash("Leave rejected.")
        body = f"Hello {leave.name},\n\nYour leave request for {leave.days} day(s) has been REJECTED.\n\nReason: {leave.reason}\n\nPlease contact faculty for more details."
        if not send_email("Leave Rejected", leave.email, body):
            flash("Failed to send rejection email. Check mail configuration.")
    return redirect(url_for('faculty_dashboard'))

# -------- Logout --------
@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out successfully.")
    return redirect(url_for('login'))

# -------------------- Main --------------------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host="0.0.0.0", port=5000)
