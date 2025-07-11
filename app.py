import os
import csv
import hashlib
import io
from datetime import datetime, date

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, send_file
)
import qrcode

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your_secret_key_here")

USERS_FILE = 'users.csv'
CHILDREN_FILE = 'children.csv'
ATTENDANCE_FILE = 'attendance.csv'

# Admin secret key (set in env variable for security)
ADMIN_KEY = os.getenv('ADMIN_KEY', 'fallback_admin_key')


def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def initialize_files():
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'w', newline='') as f:
            csv.writer(f).writerow(['ID', 'Email', 'PasswordHash', 'Gender', 'Role'])
    if not os.path.exists(CHILDREN_FILE):
        with open(CHILDREN_FILE, 'w', newline='') as f:
            csv.writer(f).writerow(['ParentID', 'ChildName', 'Gender'])
    if not os.path.exists(ATTENDANCE_FILE):
        with open(ATTENDANCE_FILE, 'w', newline='') as f:
            csv.writer(f).writerow(['ID', 'Timestamp', 'ChildName'])


def validate_login(uid, pw):
    if not os.path.exists(USERS_FILE):
        return False
    h = hash_password(pw)
    with open(USERS_FILE) as f:
        for r in csv.DictReader(f):
            if r['ID'] == uid and r['PasswordHash'] == h:
                return True
    return False


def get_user_role(uid):
    with open(USERS_FILE) as f:
        for r in csv.DictReader(f):
            if r['ID'] == uid:
                return r['Role']
    return None


def has_parent_marked_today(uid):
    today = date.today().isoformat()
    with open(ATTENDANCE_FILE) as f:
        for r in csv.DictReader(f):
            if r['ID'] == uid and r['ChildName'] == '' and r['Timestamp'].startswith(today):
                return True
    return False


@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('attendance'))
    return render_template('home.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        uid = request.form['user_id'].strip()
        email = request.form['email'].strip()
        pw = request.form['password']
        gender = request.form['gender']
        role = 'user'  # default role on registration

        # Check if user already exists
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE) as f:
                for r in csv.DictReader(f):
                    if r['ID'] == uid:
                        flash('User ID already exists.', 'danger')
                        return redirect(url_for('register'))

        # Save user data
        with open(USERS_FILE, 'a', newline='') as f:
            csv.writer(f).writerow([uid, email, hash_password(pw), gender, role])

        flash(f"Registered successfully! Your ID: {uid}", "success")
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        uid = request.form['user_id'].strip()
        pw = request.form['password']
        input_admin_key = request.form.get('admin_key', '').strip()

        if validate_login(uid, pw):
            role = get_user_role(uid)

            # If admin key matches, elevate to admin role for this session
            if input_admin_key and input_admin_key == ADMIN_KEY:
                role = 'admin'

            session['user_id'] = uid
            session['role'] = role
            flash(f"Welcome back, {uid}!", "success")
            return redirect(url_for('attendance'))

        flash("Invalid credentials", "danger")

    return render_template('login.html', show_admin_key=False)


@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out", "info")
    return redirect(url_for('home'))


@app.route('/attendance', methods=['GET', 'POST'])
def attendance():
    if 'user_id' not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for('login'))

    uid = session['user_id']

    # Load saved children for this user
    children_saved = []
    if os.path.exists(CHILDREN_FILE):
        with open(CHILDREN_FILE) as f:
            for r in csv.DictReader(f):
                if r['ParentID'] == uid:
                    children_saved.append(r)

    if request.method == 'POST':
        step = request.form.get('step', 'mark_parent')

        if step == 'mark_parent':
            if has_parent_marked_today(uid):
                flash("You have already marked attendance today.", "info")
                return redirect(url_for('attendance'))
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(ATTENDANCE_FILE, 'a', newline='') as f:
                csv.writer(f).writerow([uid, ts, ''])
            return render_template('attendance.html', step='ask_children', children=children_saved)

        elif step == 'children_response':
            return render_template('attendance.html', step='enter_children', children=children_saved)

        elif step == 'submit_children':
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            names = request.form.getlist('child_name')
            genders = request.form.getlist('child_gender')
            existing_names = {c['ChildName'] for c in children_saved}

            with open(CHILDREN_FILE, 'a', newline='') as cf, open(ATTENDANCE_FILE, 'a', newline='') as af:
                cw = csv.writer(cf)
                aw = csv.writer(af)
                for n, g in zip(names, genders):
                    n = n.strip()
                    if n and n not in existing_names:
                        cw.writerow([uid, n, g])
                    if n:
                        aw.writerow([uid, ts, n])

            return render_template('attendance.html', step='thank_you')

    # Default GET view
    return render_template('attendance.html', step='mark_parent', children=children_saved)


@app.route('/admin')
def admin():
    if session.get('role') != 'admin':
        flash("Admin access only", "danger")
        return redirect(url_for('login'))

    records = []
    if os.path.exists(ATTENDANCE_FILE):
        with open(ATTENDANCE_FILE) as f:
            records = list(csv.DictReader(f))

    return render_template('admin.html', records=records)


@app.route('/admin/assign', methods=['GET', 'POST'])
def admin_assign():
    if session.get('role') != 'admin':
        flash("Admin access only", "danger")
        return redirect(url_for('login'))

    if request.method == 'POST':
        user_id = request.form.get('user_id')
        new_role = request.form.get('role')
        if user_id and new_role in ['user', 'admin']:
            # Update user role
            users = []
            with open(USERS_FILE, newline='') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row['ID'] == user_id:
                        row['Role'] = new_role
                    users.append(row)
            with open(USERS_FILE, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['ID', 'Email', 'PasswordHash', 'Gender', 'Role'])
                writer.writeheader()
                writer.writerows(users)

            flash(f"Updated role of user {user_id} to {new_role}", "success")
        else:
            flash("Invalid input", "danger")

        return redirect(url_for('admin_assign'))

    users = []
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, newline='') as f:
            users = list(csv.DictReader(f))

    return render_template('admin_assign.html', users=users)


@app.route('/qr')
def qr_code():
    img = qrcode.make(url_for('attendance', _external=True))
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype='image/png')


if __name__ == '__main__':
    initialize_files()
    app.run(debug=True, host='0.0.0.0')
