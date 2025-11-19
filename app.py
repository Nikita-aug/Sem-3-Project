from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_mail import Mail
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import os
import io
import secrets
import string
from datetime import datetime
import logging

app = Flask(__name__)
app.secret_key = "super_secret_key"  # change this in production!

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- Upload / Export Configuration ----------------
UPLOAD_FOLDER = 'static/uploads'
EXPORT_FOLDER = 'exports'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(EXPORT_FOLDER, exist_ok=True)

# ---------------- Database Configuration ----------------
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ---------------- Mail Configuration (unused but kept) ----------------
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'your_email@gmail.com'
app.config['MAIL_PASSWORD'] = 'your_app_password'
mail = Mail(app)

# -------------------- Models --------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50), nullable=False)

    leaves = db.relationship('Leave', backref='user', lazy=True,
                             cascade='all, delete-orphan', passive_deletes=True)
    attendance = db.relationship('Attendance', backref='student', lazy=True,
                                 cascade='all, delete-orphan', passive_deletes=True)


class Leave(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), nullable=False)
    days = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.Text, nullable=False)
    document = db.Column(db.String(150), nullable=True)
    status = db.Column(db.String(20), default='Pending')
    approved_by = db.Column(db.String(150), nullable=True)


class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    percentage = db.Column(db.Float, nullable=False, default=0.0)
    updated_by = db.Column(db.String(150), nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# -------------------- Helpers --------------------
def create_default_admin():
    admin_email = "admin@college.edu"
    admin_password = "Admin@12345"   # CHANGE IMMEDIATELY after first login
    existing = User.query.filter_by(email=admin_email).first()
    if existing:
        return False
    hashed = generate_password_hash(admin_password, method='pbkdf2:sha256')
    admin_user = User(name="Default Admin", email=admin_email, password=hashed, role="admin")
    db.session.add(admin_user)
    db.session.commit()
    logger.info(f"Default admin created: {admin_email} / {admin_password}")
    return True


# -------------------- Routes (unchanged logic) --------------------
@app.route('/')
def home():
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    flash("Public registration is disabled. Please contact the admin to create an account.", "info")
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['role'] = user.role.lower()
            session['name'] = user.name
            flash(f"Welcome, {user.name}!", "success")

            if user.role.lower() == 'student':
                return redirect(url_for('student_dashboard'))
            elif user.role.lower() == 'faculty':
                return redirect(url_for('faculty_dashboard'))
            elif user.role.lower() == 'admin':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('login'))
        else:
            flash("Invalid email or password!", "error")
            return redirect(url_for('login'))

    return render_template('login.html')


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        user = User.query.filter_by(email=email).first()
        if not user:
            flash("❌ No account found with that email.", "error")
            return redirect(url_for('forgot_password'))

        if new_password != confirm_password:
            flash("❌ Passwords do not match.", "error")
            return redirect(url_for('forgot_password'))

        user.password = generate_password_hash(new_password, method='pbkdf2:sha256')
        db.session.commit()
        flash("✅ Password reset successful! Please log in.", "success")
        return redirect(url_for('login'))

    return render_template('reset_password.html')


@app.route('/student', methods=['GET', 'POST'])
def student_dashboard():
    if 'role' not in session or session['role'].lower() != 'student':
        flash("You are not authorized to view this page.", "error")
        return redirect(url_for('login'))

    user_id = session['user_id']
    attendance_record = Attendance.query.filter_by(student_id=user_id) \
        .order_by(Attendance.updated_at.desc(), Attendance.id.desc()).first()
    attendance_percentage = attendance_record.percentage if attendance_record else 0.0

    if request.method == 'POST':
        if attendance_percentage < 80:
            flash("⚠ Attendance below 80%. You cannot apply for leave.", "error")
            return redirect(url_for('student_dashboard'))

        full_name = request.form['full_name']
        email = request.form['email']
        try:
            days = int(request.form['days'])
        except ValueError:
            flash("Invalid number of days.", "error")
            return redirect(url_for('student_dashboard'))
        reason = request.form['reason']
        file = request.files.get('document')
        filename = None

        if file and file.filename:
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        leave = Leave(
            student_id=user_id,
            name=full_name,
            email=email,
            days=days,
            reason=reason,
            document=filename
        )
        db.session.add(leave)
        db.session.commit()
        flash("✅ Leave application submitted successfully!", "success")
        return redirect(url_for('student_dashboard'))

    return render_template('student_dashboard.html', name=session['name'], attendance=round(attendance_percentage, 2))


@app.route('/leave-status')
def leave_status():
    if 'role' not in session or session['role'].lower() != 'student':
        flash("You are not authorized to view this page.", "error")
        return redirect(url_for('login'))

    user_id = session['user_id']
    leaves = Leave.query.filter_by(student_id=user_id).order_by(Leave.id.desc()).all()
    return render_template('leave_status.html', name=session['name'], leaves=leaves)


@app.route('/faculty', methods=['GET', 'POST'])
def faculty_dashboard():
    if 'role' not in session or session['role'].lower() != 'faculty':
        flash("You are not authorized to view this page.", "error")
        return redirect(url_for('login'))

    if request.method == 'POST':
        if 'leave_action' in request.form:
            leave_id = int(request.form['leave_id'])
            action = request.form['leave_action']
            leave = Leave.query.get(leave_id)

            if leave:
                if session.get('name') == leave.name:
                    flash("You cannot approve your own leave.", "error")
                    return redirect(url_for('faculty_dashboard'))

                leave.status = "Approved" if action == "approve" else "Rejected"
                leave.approved_by = session['name']
                db.session.commit()
                flash(f"Leave ID {leave.id} marked as {leave.status}.", "success")
            return redirect(url_for('faculty_dashboard'))

    leaves = Leave.query.all()
    students = User.query.filter_by(role='student').all()
    return render_template('faculty_dashboard.html', faculty_name=session['name'], leaves=leaves, students=students)


@app.route('/update_attendance', methods=['GET', 'POST'])
def update_attendance():
    if 'role' not in session or session['role'].lower() != 'faculty':
        flash("You are not authorized to perform this action.", "error")
        return redirect(url_for('login'))

    students = User.query.filter_by(role='student').order_by(User.id).all()

    if request.method == 'POST':
        logger.info("Received attendance update form: %s", dict(request.form))
        try:
            student_id = int(request.form['student_id'])
            total_days = int(request.form['total_days'])
            present_days = int(request.form['present_days'])

            if total_days <= 0:
                flash("❌ Total days must be greater than zero.", "error")
                return redirect(url_for('update_attendance'))

            if present_days < 0 or present_days > total_days:
                flash("❌ Present days must be between 0 and total days.", "error")
                return redirect(url_for('update_attendance'))

            percentage = round((present_days / total_days) * 100.0, 2)
            logger.info("Computed percentage for student %d: %s (present %d of %d)", student_id, percentage, present_days, total_days)

            record = Attendance(student_id=student_id, percentage=percentage, updated_by=session['name'], updated_at=datetime.utcnow())
            db.session.add(record)
            db.session.commit()

            logger.info("Saved Attendance id=%s for student_id=%s percentage=%s", record.id, student_id, record.percentage)
            flash(f"✅ Attendance updated for Student ID {student_id}: {percentage:.2f}%", "success")
            return redirect(url_for('update_attendance'))

        except Exception as e:
            logger.exception("Error updating attendance")
            flash(f"⚠ Error updating attendance: {str(e)}", "error")
            return redirect(url_for('update_attendance'))

    return render_template('update_attendance.html', students=students)


# ---------------- ADMIN ROUTES ----------------
@app.route('/admin')
def admin_dashboard():
    if 'role' not in session or session['role'].lower() != 'admin':
        flash("You are not authorized to access admin panel.", "error")
        return redirect(url_for('login'))

    total_students = User.query.filter_by(role='student').count()
    total_faculty = User.query.filter_by(role='faculty').count()
    total_leaves = Leave.query.count()

    return render_template('admin_dashboard.html', name=session['name'],
                           total_students=total_students, total_faculty=total_faculty, total_leaves=total_leaves)


@app.route('/admin/leaves', methods=['GET', 'POST'])
def admin_leaves():
    if 'role' not in session or session['role'].lower() != 'admin':
        flash("Unauthorized access.", "error")
        return redirect(url_for('login'))

    if request.method == 'POST':
        leave_id = int(request.form['leave_id'])
        action = request.form['action']
        leave = Leave.query.get(leave_id)
        if leave:
            leave.status = "Approved" if action == "approve" else "Rejected"
            leave.approved_by = session['name']
            db.session.commit()
            flash(f"Leave ID {leave.id} has been {leave.status}.", "success")
        return redirect(url_for('admin_leaves'))

    leaves = Leave.query.order_by(Leave.id.desc()).all()

    students = []
    for s in User.query.filter_by(role='student').order_by(User.id).all():
        rec = Attendance.query.filter_by(student_id=s.id).order_by(Attendance.updated_at.desc(), Attendance.id.desc()).first()
        perc = rec.percentage if rec else 0.0
        students.append({
            'id': s.id,
            'name': s.name,
            'email': s.email,
            'lectures_attended': '—',
            'percentage': round(perc, 2)
        })

    return render_template('admin_leaves.html', leaves=leaves, students=students)


@app.route('/admin/users', methods=['GET', 'POST'])
def admin_users():
    if 'role' not in session or session['role'].lower() != 'admin':
        flash("Unauthorized access.", "error")
        return redirect(url_for('login'))

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            name = request.form['name'].strip()
            email = request.form['email'].strip().lower()
            password = request.form['password']
            role = request.form['role'].strip().lower()
            if User.query.filter_by(email=email).first():
                flash("Email already exists!", "error")
            else:
                new_user = User(name=name, email=email,
                                password=generate_password_hash(password, method='pbkdf2:sha256'),
                                role=role)
                db.session.add(new_user)
                db.session.commit()
                flash("User added successfully.", "success")
            return redirect(url_for('admin_users'))

        elif action == 'delete':
            user_id = int(request.form.get('user_id'))
            user = User.query.get(user_id)
            if user:
                if user.id == session.get('user_id'):
                    flash("You cannot delete your own admin account.", "error")
                else:
                    try:
                        Leave.query.filter_by(student_id=user.id).delete()
                        Attendance.query.filter_by(student_id=user.id).delete()
                        db.session.delete(user)
                        db.session.commit()
                        flash("User deleted successfully.", "success")
                    except Exception as e:
                        db.session.rollback()
                        flash(f"Error deleting user: {str(e)}", "error")
            return redirect(url_for('admin_users'))

    users = User.query.order_by(User.id.desc()).all()
    total_students = User.query.filter_by(role='student').count()
    total_faculty = User.query.filter_by(role='faculty').count()
    total_leaves = Leave.query.count()
    return render_template('admin_users.html', users=users,
                           total_students=total_students, total_faculty=total_faculty, total_leaves=total_leaves)


@app.route('/admin/attendance')
def admin_attendance():
    if 'role' not in session or session['role'].lower() != 'admin':
        flash("Unauthorized access.", "error")
        return redirect(url_for('login'))

    students = []
    for s in User.query.filter_by(role='student').order_by(User.id).all():
        rec = Attendance.query.filter_by(student_id=s.id).order_by(Attendance.updated_at.desc(), Attendance.id.desc()).first()
        perc = rec.percentage if rec else 0.0
        students.append({'id': s.id, 'name': s.name, 'email': s.email, 'percentage': round(perc, 2)})
    return render_template('admin_attendance.html', students=students)


# ---------------- New export endpoints (PDF) ----------------
@app.route('/admin/download-attendance-all')
def download_attendance_all():
    # admin-only
    if 'role' not in session or session['role'].lower() != 'admin':
        flash("Unauthorized access.", "error")
        return redirect(url_for('login'))

    students = User.query.filter_by(role='student').order_by(User.id).all()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"attendance_export_{timestamp}.pdf"

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    margin = 40
    y = height - margin

    # Title
    p.setFont("Helvetica-Bold", 14)
    p.drawString(margin, y, "Student Attendance Export")
    y -= 25

    # Header row
    p.setFont("Helvetica-Bold", 10)
    p.drawString(margin, y, "ID")
    p.drawString(60, y, "Name")
    p.drawString(260, y, "Email")
    p.drawString(460, y, "Attendance %")
    y -= 18
    p.setFont("Helvetica", 9)

    for s in students:
        rec = Attendance.query.filter_by(student_id=s.id).order_by(Attendance.updated_at.desc(), Attendance.id.desc()).first()
        perc = rec.percentage if rec else 0.0

        # write row (truncate where needed)
        p.drawString(margin, y, str(s.id))
        p.drawString(60, y, (s.name[:32] + '...') if len(s.name) > 32 else s.name)
        p.drawString(260, y, (s.email[:36] + '...') if len(s.email) > 36 else s.email)
        p.drawString(460, y, f"{perc:.2f}%")
        y -= 16

        # new page if near bottom
        if y < 60:
            p.showPage()
            y = height - margin
            p.setFont("Helvetica-Bold", 10)
            p.drawString(margin, y, "ID")
            p.drawString(60, y, "Name")
            p.drawString(260, y, "Email")
            p.drawString(460, y, "Attendance %")
            y -= 18
            p.setFont("Helvetica", 9)

    p.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')


@app.route('/admin/export-users')
def export_users():
    # admin-only
    if 'role' not in session or session['role'].lower() != 'admin':
        flash("Unauthorized access.", "error")
        return redirect(url_for('login'))

    users = User.query.order_by(User.id).all()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"users_export_{timestamp}.pdf"

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    margin = 40
    y = height - margin

    # Title
    p.setFont("Helvetica-Bold", 14)
    p.drawString(margin, y, "Users Export")
    y -= 25

    # Header row (ID, Name, Email only)
    p.setFont("Helvetica-Bold", 10)
    p.drawString(margin, y, "ID")
    p.drawString(60, y, "Name")
    p.drawString(320, y, "Email")
    y -= 18
    p.setFont("Helvetica", 9)

    for u in users:
        # truncate long fields to keep layout
        pid = str(u.id)
        name = (u.name[:36] + '...') if len(u.name) > 36 else u.name
        email = (u.email[:44] + '...') if len(u.email) > 44 else u.email

        p.drawString(margin, y, pid)
        p.drawString(60, y, name)
        p.drawString(320, y, email)
        y -= 16

        if y < 60:
            p.showPage()
            y = height - margin
            p.setFont("Helvetica-Bold", 10)
            p.drawString(margin, y, "ID")
            p.drawString(60, y, "Name")
            p.drawString(320, y, "Email")
            y -= 18
            p.setFont("Helvetica", 9)

    p.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')


@app.route('/admin/download-attendance')
def download_attendance():
    # legacy PDF route kept unchanged
    if 'role' not in session or session['role'].lower() != 'admin':
        flash("Unauthorized access.", "error")
        return redirect(url_for('login'))

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    y = height - 40

    p.setFont("Helvetica-Bold", 16)
    p.drawString(160, y, "Student Attendance Report")
    y -= 30
    p.setFont("Helvetica-Bold", 11)
    p.drawString(40, y, "ID")
    p.drawString(80, y, "Name")
    p.drawString(260, y, "Email")
    p.drawString(460, y, "Attendance %")
    y -= 18
    p.setFont("Helvetica", 10)

    students = User.query.filter_by(role='student').order_by(User.id).all()
    for s in students:
        rec = Attendance.query.filter_by(student_id=s.id).order_by(Attendance.updated_at.desc(), Attendance.id.desc()).first()
        perc = rec.percentage if rec else 0.0
        p.drawString(40, y, str(s.id))
        p.drawString(80, y, (s.name[:28] + '...') if len(s.name) > 28 else s.name)
        p.drawString(260, y, (s.email[:30] + '...') if len(s.email) > 30 else s.email)
        p.drawString(460, y, f"{perc:.2f}%")
        y -= 16
        if y < 60:
            p.showPage()
            y = height - 40
            p.setFont("Helvetica", 10)

    p.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="attendance_report.pdf", mimetype='application/pdf')


@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out successfully.", "success")
    return redirect(url_for('login'))


# ----------------- DEBUG ROUTES -----------------
@app.route('/debug/attendance')
def debug_attendance():
    """
    Debug page to inspect latest attendance records.
    Use this to verify what the DB actually contains after an update.
    """
    records = Attendance.query.order_by(Attendance.updated_at.desc(), Attendance.id.desc()).limit(50).all()
    rows = []
    for r in records:
        rows.append({
            'id': r.id,
            'student_id': r.student_id,
            'student_name': r.student.name if r.student else '',
            'percentage': r.percentage,
            'updated_by': r.updated_by,
            'updated_at': r.updated_at.strftime("%Y-%m-%d %H:%M:%S") if r.updated_at else None
        })
    html = "<h2>Last Attendance Records (most recent first)</h2><table border='1' cellpadding='5'><tr><th>id</th><th>student_id</th><th>name</th><th>percentage</th><th>updated_by</th><th>updated_at</th></tr>"
    for r in rows:
        html += f"<tr><td>{r['id']}</td><td>{r['student_id']}</td><td>{r['student_name']}</td><td>{r['percentage']}</td><td>{r['updated_by']}</td><td>{r['updated_at']}</td></tr>"
    html += "</table>"
    return html


# -------- Run App --------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        created = create_default_admin()
        if created:
            logger.info("Default admin created (admin@college.edu). Change the password immediately.")
    app.run(debug=True)
