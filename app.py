from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_mysqldb import MySQL
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from functools import wraps
import os, re

from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')
app.config['MYSQL_HOST'] = os.environ.get('MYSQL_HOST', 'localhost')
app.config['MYSQL_USER'] = os.environ.get('MYSQL_USER', 'root')
app.config['MYSQL_PASSWORD'] = os.environ.get('MYSQL_PASSWORD', '')
app.config['MYSQL_DB'] = os.environ.get('MYSQL_DB', 'blog_db')

UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

mysql = MySQL(app)
CATEGORIES = ['Travel', 'Food', 'Technology', 'Education', 'Lifestyle']
TRUSTED_THRESHOLD = 20   # blogs needed to earn trusted badge

REPORT_REASONS = [
    'Inappropriate content',
    'Copied / plagiarised content',
    'Spam',
    'Wrong or misleading image',
    'False information',
    'Hate speech',
]

# ── helpers ──────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_file(file, subfolder=''):
    if file and file.filename and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filename = f"{datetime.now().timestamp()}_{filename}"
        dest = os.path.join(app.config['UPLOAD_FOLDER'], subfolder)
        os.makedirs(dest, exist_ok=True)
        file.save(os.path.join(dest, filename))
        return f"uploads/{subfolder}/{filename}" if subfolder else f"uploads/{filename}"
    return None

def get_db():
    return mysql.connection.cursor()

def get_author_profile(user_id):
    cur = get_db()
    cur.execute("SELECT * FROM author_profiles WHERE user_id=%s", (user_id,))
    profile = cur.fetchone()
    cur.close()
    return profile

def push_notification(ntype, message, link=None):
    """Insert a row into admin_notifications."""
    cur = get_db()
    cur.execute(
        "INSERT INTO admin_notifications (type, message, link, is_read, created_at) "
        "VALUES (%s,%s,%s,0,%s)",
        (ntype, message, link, datetime.now())
    )
    mysql.connection.commit()
    cur.close()

def check_and_grant_trust(author_id):
    """Grant trusted badge if author has >= TRUSTED_THRESHOLD approved blogs."""
    cur = get_db()
    cur.execute(
        "SELECT COUNT(*) FROM blogs WHERE author_id=%s AND status='approved'",
        (author_id,)
    )
    count = cur.fetchone()[0]
    if count >= TRUSTED_THRESHOLD:
        cur.execute("UPDATE users SET is_trusted=1 WHERE id=%s", (author_id,))
        mysql.connection.commit()
    cur.close()
    return count

# ── decorators ───────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login to continue.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def author_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login to continue.', 'error')
            return redirect(url_for('login'))
        if session.get('role') not in ('author', 'admin'):
            flash('You need an Author account to access this.', 'error')
            return redirect(url_for('index'))
        cur = get_db()
        cur.execute("SELECT account_status FROM users WHERE id=%s", (session['user_id'],))
        row = cur.fetchone()
        cur.close()
        if row and row[0] == 'suspended':
            flash('Your account is suspended. You cannot publish blogs.', 'error')
            return redirect(url_for('index'))
        if row and row[0] == 'banned':
            session.clear()
            flash('Your account has been banned.', 'error')
            return redirect(url_for('landing'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'admin':
            flash('Admin access required.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

# ── context processor ────────────────────────────────────────

@app.context_processor
def inject_globals():
    has_profile = False
    unread_notifications = 0
    if 'user_id' in session:
        has_profile = get_author_profile(session['user_id']) is not None
        if session.get('role') == 'admin':
            cur = get_db()
            cur.execute("SELECT COUNT(*) FROM admin_notifications WHERE is_read=0")
            unread_notifications = cur.fetchone()[0]
            cur.close()
    return dict(
        categories=CATEGORIES,
        has_profile=has_profile,
        unread_notifications=unread_notifications
    )

# ── landing ──────────────────────────────────────────────────

@app.route('/landing')
def landing():
    cur = get_db()
    cur.execute("SELECT COUNT(*) FROM blogs WHERE status='approved'")
    total_blogs = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE role='author'")
    total_authors = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM comments")
    total_comments = cur.fetchone()[0]
    cur.execute("""
        SELECT b.id, b.title, b.description, b.image_path, b.category,
               b.author_id, b.created_at, u.username,
               (SELECT COUNT(*) FROM likes WHERE blog_id=b.id) as like_count,
               (SELECT COUNT(*) FROM comments WHERE blog_id=b.id) as comment_count,
               ap.photo, ap.full_name
        FROM blogs b JOIN users u ON b.author_id=u.id
        LEFT JOIN author_profiles ap ON ap.user_id=u.id
        WHERE b.status='approved'
        ORDER BY b.created_at DESC LIMIT 3
    """)
    latest_blogs = cur.fetchall()
    cur.close()
    return render_template('landing.html',
        total_blogs=total_blogs, total_authors=total_authors,
        total_comments=total_comments, latest_blogs=latest_blogs)

# ── home ─────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('landing'))
    category = request.args.get('category')
    search   = request.args.get('search')
    author   = request.args.get('author')
    cur = get_db()
    base = """
        SELECT b.id, b.title, b.description, b.image_path, b.category,
               b.author_id, b.created_at, u.username as author_name,
               (SELECT COUNT(*) FROM likes WHERE blog_id=b.id) as like_count,
               (SELECT COUNT(*) FROM comments WHERE blog_id=b.id) as comment_count,
               ap.photo as author_photo, ap.full_name as author_full_name,
               u.is_trusted
        FROM blogs b JOIN users u ON b.author_id=u.id
        LEFT JOIN author_profiles ap ON ap.user_id=u.id
        WHERE b.status='approved'
    """
    if category:
        cur.execute(base + " AND b.category=%s ORDER BY b.created_at DESC", (category,))
    elif search:
        cur.execute(base + " AND b.title LIKE %s ORDER BY b.created_at DESC", (f"%{search}%",))
    elif author:
        cur.execute(base + " AND (u.username LIKE %s OR ap.full_name LIKE %s) ORDER BY b.created_at DESC",
                    (f"%{author}%", f"%{author}%"))
    else:
        cur.execute(base + " ORDER BY b.created_at DESC")
    blogs = cur.fetchall()
    cur.execute(base + " ORDER BY like_count DESC LIMIT 5")
    most_liked = cur.fetchall()
    cur.execute("""
        SELECT u.id, u.username, ap.full_name, ap.bio, ap.photo,
               COUNT(b.id) as blog_count, u.is_trusted,
               (SELECT COUNT(*) FROM likes l JOIN blogs b2 ON l.blog_id=b2.id
                WHERE b2.author_id=u.id) as total_likes
        FROM users u JOIN author_profiles ap ON ap.user_id=u.id
        LEFT JOIN blogs b ON b.author_id=u.id AND b.status='approved'
        GROUP BY u.id, u.username, ap.full_name, ap.bio, ap.photo, u.is_trusted
        ORDER BY blog_count DESC LIMIT 4
    """)
    featured_authors = cur.fetchall()
    cur.close()
    return render_template('index.html', blogs=blogs, current_category=category,
                           search_term=search, author_term=author,
                           most_liked=most_liked, featured_authors=featured_authors)

# ── register ─────────────────────────────────────────────────

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        email    = request.form['email'].strip()
        password = request.form['password']
        confirm  = request.form.get('confirm_password', '')
        role     = request.form.get('role', 'reader')
        agreed   = request.form.get('agree_terms')   # content agreement checkbox

        if role not in ('reader', 'author'):
            role = 'reader'

        # ── agreement check ──
        if not agreed:
            flash('You must agree to the Content Policy to register.', 'error')
            return redirect(url_for('register'))

        # ── password validation ──
        errors = []
        if len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        if not re.search(r'[A-Z]', password):
            errors.append('Password must contain at least one uppercase letter.')
        if not re.search(r'[a-z]', password):
            errors.append('Password must contain at least one lowercase letter.')
        if not re.search(r'[0-9]', password):
            errors.append('Password must contain at least one number.')
        if not re.search(r'[!@#$%^&*()\-_=+\[\]{};:\'",.<>/?\\|`~]', password):
            errors.append('Password must contain at least one special character.')
        if password != confirm:
            errors.append('Passwords do not match.')
        if errors:
            for e in errors:
                flash(e, 'error')
            return redirect(url_for('register'))

        cur = get_db()
        cur.execute("SELECT id FROM users WHERE email=%s OR username=%s", (email, username))
        if cur.fetchone():
            flash('Username or email already exists.', 'error')
            cur.close()
            return redirect(url_for('register'))

        hashed = generate_password_hash(password)
        cur.execute(
            "INSERT INTO users (username, email, password, role, agreed_terms, created_at) "
            "VALUES (%s,%s,%s,%s,1,%s)",
            (username, email, hashed, role, datetime.now())
        )
        mysql.connection.commit()
        cur.close()
        flash('Registration successful! Please login.', 'success')
        if role == 'author':
            flash('As an author, please login and set up your profile.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

# ── login / logout ───────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form['email'].strip()
        password = request.form['password']
        cur = get_db()
        cur.execute(
            "SELECT id, username, password, role, account_status FROM users WHERE email=%s",
            (email,)
        )
        user = cur.fetchone()
        cur.close()
        if user and check_password_hash(user[2], password):
            if user[4] == 'banned':
                flash('Your account has been banned. Contact support.', 'error')
                return redirect(url_for('login'))
            if user[4] == 'suspended':
                flash('Your account is currently suspended.', 'error')
                return redirect(url_for('login'))
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['role'] = user[3]
            flash(f'Welcome back, {user[1]}!', 'success')
            if user[3] == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif user[3] == 'author':
                return redirect(url_for('author_profile', username=user[1]))
            return redirect(url_for('index'))
        flash('Invalid email or password.', 'error')
        return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('landing'))

# ── author profile ───────────────────────────────────────────

@app.route('/author/<username>')
def author_profile(username):
    cur = get_db()
    cur.execute(
        "SELECT id, username, email, role, is_trusted, account_status FROM users WHERE username=%s",
        (username,)
    )
    user = cur.fetchone()
    if not user:
        flash('Author not found.', 'error')
        cur.close()
        return redirect(url_for('index'))
    cur.execute("SELECT * FROM author_profiles WHERE user_id=%s", (user[0],))
    profile = cur.fetchone()

    # show pending/rejected blogs only to the author themselves or admin
    if session.get('user_id') == user[0] or session.get('role') == 'admin':
        cur.execute("""
            SELECT b.id, b.title, b.description, b.image_path, b.category,
                   b.created_at, b.status,
                   (SELECT COUNT(*) FROM likes WHERE blog_id=b.id) as like_count,
                   (SELECT COUNT(*) FROM comments WHERE blog_id=b.id) as comment_count
            FROM blogs b WHERE b.author_id=%s ORDER BY b.created_at DESC
        """, (user[0],))
    else:
        cur.execute("""
            SELECT b.id, b.title, b.description, b.image_path, b.category,
                   b.created_at, b.status,
                   (SELECT COUNT(*) FROM likes WHERE blog_id=b.id) as like_count,
                   (SELECT COUNT(*) FROM comments WHERE blog_id=b.id) as comment_count
            FROM blogs b WHERE b.author_id=%s AND b.status='approved'
            ORDER BY b.created_at DESC
        """, (user[0],))

    blogs = cur.fetchall()
    cur.close()
    return render_template('author_profile.html', author=user, profile=profile, blogs=blogs)

# ── profile create / edit ────────────────────────────────────

@app.route('/profile/create', methods=['GET', 'POST'])
@author_required
def create_profile():
    if get_author_profile(session['user_id']):
        return redirect(url_for('edit_profile'))
    if request.method == 'POST':
        full_name = request.form['full_name'].strip()
        bio   = request.form['bio'].strip()
        photo = save_file(request.files.get('photo'), 'profiles')
        cur = get_db()
        cur.execute(
            "INSERT INTO author_profiles (user_id, full_name, bio, photo) VALUES (%s,%s,%s,%s)",
            (session['user_id'], full_name, bio, photo)
        )
        mysql.connection.commit()
        cur.close()
        flash('Profile created!', 'success')
        return redirect(url_for('author_profile', username=session['username']))
    return render_template('create_profile.html')

@app.route('/profile/edit', methods=['GET', 'POST'])
@author_required
def edit_profile():
    profile = get_author_profile(session['user_id'])
    if not profile:
        return redirect(url_for('create_profile'))
    if request.method == 'POST':
        full_name = request.form['full_name'].strip()
        bio   = request.form['bio'].strip()
        photo = save_file(request.files.get('photo'), 'profiles')
        cur = get_db()
        if photo:
            cur.execute(
                "UPDATE author_profiles SET full_name=%s, bio=%s, photo=%s WHERE user_id=%s",
                (full_name, bio, photo, session['user_id'])
            )
        else:
            cur.execute(
                "UPDATE author_profiles SET full_name=%s, bio=%s WHERE user_id=%s",
                (full_name, bio, session['user_id'])
            )
        mysql.connection.commit()
        cur.close()
        flash('Profile updated!', 'success')
        return redirect(url_for('author_profile', username=session['username']))
    return render_template('edit_profile.html', profile=profile)

# ── create blog ──────────────────────────────────────────────

@app.route('/create', methods=['GET', 'POST'])
@author_required
def create_blog():
    if not get_author_profile(session['user_id']):
        flash('Please create your author profile before publishing.', 'error')
        return redirect(url_for('create_profile'))
    if request.method == 'POST':
        title       = request.form['title'].strip()
        description = request.form['description'].strip()
        category    = request.form['category']
        image_path  = save_file(request.files.get('image'))

    # ── validation ──
        if len(title) < 5:
            flash('Title must be at least 5 characters.', 'error')
            return redirect(url_for('create_blog'))
        if len(title) > 255:
            flash('Title is too long (max 255 characters).', 'error')
            return redirect(url_for('create_blog'))
        if len(description) < 20:
            flash('Content is too short. Please write at least 20 characters.', 'error')
            return redirect(url_for('create_blog'))
        if category not in CATEGORIES:
            flash('Invalid category selected.', 'error')
            return redirect(url_for('create_blog'))

        # trusted authors go live immediately; others are pending
        cur = get_db()
        cur.execute("SELECT is_trusted FROM users WHERE id=%s", (session['user_id'],))
        is_trusted = cur.fetchone()[0]
        status = 'approved' if is_trusted else 'pending'

        cur.execute(
            "INSERT INTO blogs (title, description, image_path, category, "
            "author_id, created_at, status) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (title, description, image_path, category,
             session['user_id'], datetime.now(), status)
        )
        blog_id = cur.lastrowid
        mysql.connection.commit()
        cur.close()

        if status == 'pending':
            push_notification(
                'new_blog',
                f'New blog pending review: "{title}" by {session["username"]}',
                f'/admin/blogs'
            )
            flash('Blog submitted! It will go live after admin review.', 'success')
        else:
            flash('Blog published successfully!', 'success')
            check_and_grant_trust(session['user_id'])

        return redirect(url_for('author_profile', username=session['username']))
    return render_template('create_blog.html')

# ── edit blog ────────────────────────────────────────────────

@app.route('/edit/<int:blog_id>', methods=['GET', 'POST'])
@author_required
def edit_blog(blog_id):
    cur = get_db()
    cur.execute("SELECT * FROM blogs WHERE id=%s", (blog_id,))
    blog = cur.fetchone()
    if not blog:
        flash('Blog not found.', 'error')
        cur.close()
        return redirect(url_for('index'))
    # blog[5] = author_id
    if blog[5] != session['user_id'] and session.get('role') != 'admin':
        flash('Permission denied.', 'error')
        cur.close()
        return redirect(url_for('index'))

    if request.method == 'POST':
        title       = request.form['title'].strip()
        description = request.form['description']
        category    = request.form['category']
        new_image   = save_file(request.files.get('image'))

        if new_image:
            # delete old image file
            if blog[3]:
                old_img = os.path.join('static', blog[3])
                if os.path.exists(old_img):
                    os.remove(old_img)
            cur.execute(
                "UPDATE blogs SET title=%s, description=%s, category=%s, "
                "image_path=%s, updated_at=%s WHERE id=%s",
                (title, description, category, new_image, datetime.now(), blog_id)
            )
        else:
            cur.execute(
                "UPDATE blogs SET title=%s, description=%s, category=%s, "
                "updated_at=%s WHERE id=%s",
                (title, description, category, datetime.now(), blog_id)
            )
        mysql.connection.commit()
        cur.close()

        # notify admin that a blog was edited
        push_notification(
            'blog_edited',
            f'Blog edited: "{title}" by {session["username"]}',
            f'/blog/{blog_id}'
        )
        flash('Blog updated successfully!', 'success')
        return redirect(url_for('blog_detail', blog_id=blog_id))

    cur.close()
    return render_template('edit_blog.html', blog=blog, categories=CATEGORIES)

# ── delete blog ──────────────────────────────────────────────

@app.route('/delete/<int:blog_id>', methods=['POST'])
@login_required
def delete_blog(blog_id):
    cur = get_db()
    cur.execute("SELECT author_id, image_path FROM blogs WHERE id=%s", (blog_id,))
    blog = cur.fetchone()
    if not blog:
        flash('Blog not found.', 'error')
        cur.close()
        return redirect(url_for('index'))
    if blog[0] != session['user_id'] and session.get('role') != 'admin':
        flash('Permission denied.', 'error')
        cur.close()
        return redirect(url_for('index'))
    if blog[1]:
        img = os.path.join('static', blog[1])
        if os.path.exists(img):
            os.remove(img)
    cur.execute("DELETE FROM blogs WHERE id=%s", (blog_id,))
    mysql.connection.commit()
    cur.close()
    flash('Blog deleted.', 'success')
    return redirect(url_for('index'))

# ── blog detail ──────────────────────────────────────────────

@app.route('/blog/<int:blog_id>')
def blog_detail(blog_id):
    cur = get_db()
    cur.execute("""
        SELECT b.id, b.title, b.description, b.image_path, b.category,
               b.author_id, b.created_at, u.username as author_name,
               (SELECT COUNT(*) FROM likes WHERE blog_id=b.id) as like_count,
               ap.photo as author_photo, ap.full_name as author_full_name,
               ap.bio as author_bio, u.is_trusted, b.status, b.updated_at, b.admin_note
        FROM blogs b JOIN users u ON b.author_id=u.id
        LEFT JOIN author_profiles ap ON ap.user_id=u.id
        WHERE b.id=%s
    """, (blog_id,))
    blog = cur.fetchone()
    if not blog:
        flash('Blog not found.', 'error')
        cur.close()
        return redirect(url_for('index'))

    # block public access to non-approved blogs (author and admin can still view)
    if blog[13] != 'approved':
        if session.get('user_id') != blog[5] and session.get('role') != 'admin':
            flash('This blog is not available.', 'error')
            cur.close()
            return redirect(url_for('index'))

    cur.execute("""
        SELECT c.id, c.blog_id, c.content, c.created_at, u.username, u.id as uid, ap.photo
        FROM comments c JOIN users u ON c.user_id=u.id
        LEFT JOIN author_profiles ap ON ap.user_id=u.id
        WHERE c.blog_id=%s ORDER BY c.created_at DESC
    """, (blog_id,))
    comments = cur.fetchall()

    liked = False
    user_reported = False
    if 'user_id' in session:
        cur.execute("SELECT 1 FROM likes WHERE blog_id=%s AND user_id=%s",
                    (blog_id, session['user_id']))
        liked = cur.fetchone() is not None
        cur.execute("SELECT 1 FROM reports WHERE blog_id=%s AND user_id=%s",
                    (blog_id, session['user_id']))
        user_reported = cur.fetchone() is not None

    cur.execute("""
        SELECT b.id, b.title, b.image_path, b.category,
               (SELECT COUNT(*) FROM likes WHERE blog_id=b.id) as like_count, u.username
        FROM blogs b JOIN users u ON b.author_id=u.id
        WHERE b.category=%s AND b.id!=%s AND b.status='approved'
        ORDER BY b.created_at DESC LIMIT 3
    """, (blog[4], blog_id))
    related = cur.fetchall()
    cur.close()
    return render_template('blog_detail.html', blog=blog, comments=comments,
                           liked=liked, related=related,
                           report_reasons=REPORT_REASONS,
                           user_reported=user_reported,
                           blog_admin_note=blog[15])

# ── like ─────────────────────────────────────────────────────

@app.route('/like/<int:blog_id>', methods=['POST'])
@login_required
def like_blog(blog_id):
    cur = get_db()
    cur.execute("SELECT 1 FROM likes WHERE blog_id=%s AND user_id=%s",
                (blog_id, session['user_id']))
    if cur.fetchone():
        cur.execute("DELETE FROM likes WHERE blog_id=%s AND user_id=%s",
                    (blog_id, session['user_id']))
    else:
        cur.execute("INSERT INTO likes (blog_id, user_id) VALUES (%s,%s)",
                    (blog_id, session['user_id']))
    mysql.connection.commit()
    cur.close()
    return redirect(url_for('blog_detail', blog_id=blog_id))

# ── comment ──────────────────────────────────────────────────

@app.route('/comment/<int:blog_id>', methods=['POST'])
@login_required
def add_comment(blog_id):
    content = request.form['content'].strip()
    if not content:
        flash('Comment cannot be empty.', 'error')
        return redirect(url_for('blog_detail', blog_id=blog_id))
    if len(content) > 1000:                                          
        flash('Comment too long (max 1000 characters).', 'error')   
        return redirect(url_for('blog_detail', blog_id=blog_id))    
    cur = get_db()
    cur.execute(
        "INSERT INTO comments (blog_id, user_id, content, created_at) VALUES (%s,%s,%s,%s)",
        (blog_id, session['user_id'], content, datetime.now())
    )
    mysql.connection.commit()
    cur.close()
    flash('Comment posted!', 'success')
    return redirect(url_for('blog_detail', blog_id=blog_id))

@app.route('/comment/delete/<int:comment_id>', methods=['POST'])
@login_required
def delete_comment(comment_id):
    cur = get_db()
    cur.execute("SELECT user_id, blog_id FROM comments WHERE id=%s", (comment_id,))
    comment = cur.fetchone()
    if not comment:
        flash('Comment not found.', 'error')
        cur.close()
        return redirect(url_for('index'))
    blog_id = comment[1]
    if comment[0] != session['user_id'] and session.get('role') != 'admin':
        flash('Permission denied.', 'error')
        cur.close()
        return redirect(url_for('blog_detail', blog_id=blog_id))
    cur.execute("DELETE FROM comments WHERE id=%s", (comment_id,))
    mysql.connection.commit()
    cur.close()
    flash('Comment deleted.', 'success')
    return redirect(url_for('blog_detail', blog_id=blog_id))

# ── report blog ──────────────────────────────────────────────

@app.route('/report/<int:blog_id>', methods=['POST'])
@login_required
def report_blog(blog_id):
    reason = request.form.get('reason', '').strip()
    if not reason:
        flash('Please select a reason for reporting.', 'error')
        return redirect(url_for('blog_detail', blog_id=blog_id))
    cur = get_db()
    cur.execute("SELECT 1 FROM reports WHERE blog_id=%s AND user_id=%s",
                (blog_id, session['user_id']))
    if cur.fetchone():
        flash('You have already reported this blog.', 'error')
        cur.close()
        return redirect(url_for('blog_detail', blog_id=blog_id))

    cur.execute(
        "INSERT INTO reports (blog_id, user_id, reason, created_at) VALUES (%s,%s,%s,%s)",
        (blog_id, session['user_id'], reason, datetime.now())
    )

    # count total reports on this blog to show urgency
    cur.execute("SELECT COUNT(*) FROM reports WHERE blog_id=%s AND status='pending'", (blog_id,))
    report_count = cur.fetchone()[0]
    cur.execute("SELECT title FROM blogs WHERE id=%s", (blog_id,))
    blog_title = cur.fetchone()[0]
    mysql.connection.commit()
    cur.close()

    push_notification(
        'new_report',
        f'Blog reported ({report_count} report{"s" if report_count>1 else ""}): '
        f'"{blog_title}" — Reason: {reason}',
        f'/admin/reports'
    )
    flash('Thank you. Your report has been submitted for review.', 'success')
    return redirect(url_for('blog_detail', blog_id=blog_id))


# ── admin: warn author about wrong image / content ───────────
@app.route('/admin/blogs/warn/<int:blog_id>', methods=['POST'])
@admin_required
def admin_warn_blog(blog_id):
    note = request.form.get('note', '').strip()
    if not note:
        flash('Please enter a warning message.', 'error')
        return redirect(url_for('admin_reports_queue'))
    cur = get_db()
    cur.execute("SELECT title, author_id FROM blogs WHERE id=%s", (blog_id,))
    blog_row = cur.fetchone()
    cur.execute("UPDATE blogs SET admin_note=%s WHERE id=%s", (note, blog_id))
    cur.execute("UPDATE reports SET status='reviewed' WHERE blog_id=%s AND status='pending'", (blog_id,))
    mysql.connection.commit()
    cur.close()
    if blog_row:
        push_notification('blog_edited',
            f'Admin warning sent for blog: "{blog_row[0]}" — "{note}"', f'/blog/{blog_id}')
    flash('Warning sent. Author will see it on their blog.', 'success')
    return redirect(url_for('admin_reports_queue'))

# ── category page ────────────────────────────────────────────

@app.route('/category/<category_name>')
def category_page(category_name):
    if category_name not in CATEGORIES:
        flash('Invalid category.', 'error')
        return redirect(url_for('index'))
    cur = get_db()
    cur.execute("""
        SELECT b.id, b.title, b.description, b.image_path, b.category, b.created_at,
               u.username, u.is_trusted,
               (SELECT COUNT(*) FROM likes WHERE blog_id=b.id) as like_count,
               (SELECT COUNT(*) FROM comments WHERE blog_id=b.id) as comment_count,
               ap.photo as author_photo
        FROM blogs b JOIN users u ON b.author_id=u.id
        LEFT JOIN author_profiles ap ON ap.user_id=u.id
        WHERE b.category=%s AND b.status='approved'
        ORDER BY b.created_at DESC
    """, (category_name,))
    blogs = cur.fetchall()
    cur.close()
    return render_template('category.html', blogs=blogs, category_name=category_name)

# ── admin: dashboard ─────────────────────────────────────────

@app.route('/admin')
@admin_required
def admin_dashboard():
    cur = get_db()
    cur.execute("SELECT COUNT(*) FROM users");             total_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM blogs WHERE status='approved'"); total_blogs = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM comments");          total_comments = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM likes");             total_likes = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM blogs WHERE status='pending'"); pending_blogs = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM reports WHERE status='pending'"); pending_reports = cur.fetchone()[0]
    cur.execute("""
        SELECT b.id, b.title, b.created_at, u.username,
               (SELECT COUNT(*) FROM likes WHERE blog_id=b.id) as lc
        FROM blogs b JOIN users u ON b.author_id=u.id
        WHERE b.status='approved' ORDER BY b.created_at DESC LIMIT 5
    """)
    recent_blogs = cur.fetchall()
    cur.execute("SELECT id, username, email, role, created_at FROM users ORDER BY created_at DESC LIMIT 5")
    recent_users = cur.fetchall()
    cur.execute("SELECT category, COUNT(*) as cnt FROM blogs WHERE status='approved' GROUP BY category ORDER BY cnt DESC")
    category_stats = cur.fetchall()
    cur.execute("""
        SELECT n.id, n.type, n.message, n.link, n.is_read, n.created_at
        FROM admin_notifications n ORDER BY n.created_at DESC LIMIT 10
    """)
    notifications = cur.fetchall()
    cur.execute("UPDATE admin_notifications SET is_read=1")
    mysql.connection.commit()
    cur.close()
    return render_template('admin/dashboard.html',
        total_users=total_users, total_blogs=total_blogs,
        total_comments=total_comments, total_likes=total_likes,
        pending_blogs=pending_blogs, pending_reports=pending_reports,
        recent_blogs=recent_blogs, recent_users=recent_users,
        category_stats=category_stats, notifications=notifications)

# ── admin: blogs (pending queue + all) ───────────────────────

@app.route('/admin/blogs')
@admin_required
def admin_blogs():
    cur = get_db()
    cur.execute("""
        SELECT b.id, b.title, b.category, b.created_at, b.status,
               u.username, u.id as uid,
               (SELECT COUNT(*) FROM likes WHERE blog_id=b.id) as lc,
               (SELECT COUNT(*) FROM comments WHERE blog_id=b.id) as cc
        FROM blogs b JOIN users u ON b.author_id=u.id
        ORDER BY FIELD(b.status,'pending','approved','rejected','removed'),
                 b.created_at DESC
    """)
    blogs = cur.fetchall()
    pending_count = sum(1 for b in blogs if b[4] == 'pending')
    return render_template('admin/blogs.html', blogs=blogs, pending_count=pending_count)

@app.route('/admin/blogs/approve/<int:blog_id>', methods=['POST'])
@admin_required
def admin_approve_blog(blog_id):
    cur = get_db()
    cur.execute("SELECT author_id, title FROM blogs WHERE id=%s", (blog_id,))
    row = cur.fetchone()
    cur.execute("UPDATE blogs SET status='approved' WHERE id=%s", (blog_id,))
    mysql.connection.commit()
    cur.close()
    if row:
        check_and_grant_trust(row[0])
    flash('Blog approved and is now live.', 'success')
    return redirect(url_for('admin_blogs'))

@app.route('/admin/blogs/reject/<int:blog_id>', methods=['POST'])
@admin_required
def admin_reject_blog(blog_id):
    cur = get_db()
    cur.execute("UPDATE blogs SET status='rejected' WHERE id=%s", (blog_id,))
    mysql.connection.commit()
    cur.close()
    flash('Blog rejected.', 'success')
    return redirect(url_for('admin_blogs'))

@app.route('/admin/blogs/remove/<int:blog_id>', methods=['POST'])
@admin_required
def admin_remove_blog(blog_id):
    cur = get_db()
    cur.execute("SELECT author_id FROM blogs WHERE id=%s", (blog_id,))
    row = cur.fetchone()
    cur.execute("UPDATE blogs SET status='removed' WHERE id=%s", (blog_id,))
    if row:
        # revoke trusted status when a blog is forcibly removed
        cur.execute("UPDATE users SET is_trusted=0 WHERE id=%s", (row[0],))
    mysql.connection.commit()
    cur.close()
    flash('Blog removed and author trusted status revoked.', 'success')
    return redirect(url_for('admin_reports_queue'))

# ── admin: reports queue ─────────────────────────────────────

@app.route('/admin/reports')
@admin_required
def admin_reports_queue():
    cur = get_db()
    cur.execute("""
        SELECT b.id, b.title, b.status as blog_status,
               u.id as author_id,
               u.username as author,
               COUNT(r.id) as report_count,
               GROUP_CONCAT(DISTINCT r.reason ORDER BY r.created_at SEPARATOR ' | ') as reasons,
               MAX(r.created_at) as latest_report
        FROM reports r
        JOIN blogs b ON r.blog_id = b.id
        JOIN users u ON b.author_id = u.id
        WHERE r.status = 'pending'
        GROUP BY b.id, b.title, b.status,u.id, u.username
        ORDER BY report_count DESC, latest_report DESC
    """)
    reported_blogs = cur.fetchall()
    cur.close()
    return render_template('admin/reports.html', reported_blogs=reported_blogs)

@app.route('/admin/reports/dismiss/<int:blog_id>', methods=['POST'])
@admin_required
def admin_dismiss_reports(blog_id):
    cur = get_db()
    cur.execute("UPDATE reports SET status='dismissed' WHERE blog_id=%s", (blog_id,))
    mysql.connection.commit()
    cur.close()
    flash('Reports dismissed. Blog stays live.', 'success')
    return redirect(url_for('admin_reports_queue'))

# ── admin: users ─────────────────────────────────────────────

@app.route('/admin/users')
@admin_required
def admin_users():
    cur = get_db()
    cur.execute("""
        SELECT u.id, u.username, u.email, u.role, u.created_at,
               u.is_trusted, u.account_status,
               (SELECT COUNT(*) FROM blogs WHERE author_id=u.id AND status='approved') as blog_count
        FROM users u ORDER BY u.created_at DESC
    """)
    users = cur.fetchall()
    cur.close()
    return render_template('admin/users.html', users=users)

@app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    if user_id == session['user_id']:
        flash('Cannot delete your own account.', 'error')
        return redirect(url_for('admin_users'))
    cur = get_db()
    cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
    mysql.connection.commit()
    cur.close()
    flash('User deleted.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/role/<int:user_id>', methods=['POST'])
@admin_required
def admin_change_role(user_id):
    new_role = request.form.get('role')
    if new_role not in ('reader', 'author', 'admin'):
        flash('Invalid role.', 'error')
        return redirect(url_for('admin_users'))
    cur = get_db()
    cur.execute("UPDATE users SET role=%s WHERE id=%s", (new_role, user_id))
    mysql.connection.commit()
    cur.close()
    flash('Role updated.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/suspend/<int:user_id>', methods=['POST'])
@admin_required
def admin_suspend_user(user_id):
    if user_id == session['user_id']:
        flash('Cannot suspend your own account.', 'error')
        return redirect(url_for('admin_users'))
    cur = get_db()
    cur.execute("UPDATE users SET account_status='suspended' WHERE id=%s", (user_id,))
    # hide all their blogs
    cur.execute("UPDATE blogs SET status='removed' WHERE author_id=%s AND status='approved'", (user_id,))
    mysql.connection.commit()
    cur.close()
    flash('User suspended and their blogs hidden.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/unsuspend/<int:user_id>', methods=['POST'])
@admin_required
def admin_unsuspend_user(user_id):
    cur = get_db()
    cur.execute("UPDATE users SET account_status='active' WHERE id=%s", (user_id,))
    mysql.connection.commit()
    cur.close()
    flash('User unsuspended.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/ban/<int:user_id>', methods=['POST'])
@admin_required
def admin_ban_user(user_id):
    if user_id == session['user_id']:
        flash('Cannot ban your own account.', 'error')
        return redirect(url_for('admin_users'))
    cur = get_db()
    cur.execute("UPDATE users SET account_status='banned', is_trusted=0 WHERE id=%s", (user_id,))
    cur.execute("UPDATE blogs SET status='removed' WHERE author_id=%s", (user_id,))
    mysql.connection.commit()
    cur.close()
    flash('User banned and all their blogs removed.', 'success')
    return redirect(url_for('admin_users'))

# ── admin: comments ──────────────────────────────────────────

@app.route('/admin/comments')
@admin_required
def admin_comments():
    cur = get_db()
    cur.execute("""
        SELECT c.id, c.content, c.created_at, u.username, b.title, b.id as blog_id
        FROM comments c JOIN users u ON c.user_id=u.id JOIN blogs b ON c.blog_id=b.id
        ORDER BY c.created_at DESC
    """)
    comments = cur.fetchall()
    cur.close()
    return render_template('admin/comments.html', comments=comments)

# ── admin: reports page (analytics) ─────────────────────────

@app.route('/admin/analytics')
@admin_required
def admin_analytics():
    cur = get_db()
    cur.execute("SELECT COUNT(*) FROM users");             total_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM blogs WHERE status='approved'"); total_blogs = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM comments");          total_comments = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM likes");             total_likes = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE role='author'"); total_authors = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE role='reader'"); total_readers = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE is_trusted=1"); total_trusted = cur.fetchone()[0]
    cur.execute("SELECT category, COUNT(*) as cnt FROM blogs WHERE status='approved' GROUP BY category ORDER BY cnt DESC")
    blogs_by_category = cur.fetchall()
    cur.execute("""
        SELECT b.id, b.title, b.category, u.username,
               COUNT(l.id) as likes,
               (SELECT COUNT(*) FROM comments WHERE blog_id=b.id) as comments
        FROM blogs b JOIN users u ON b.author_id=u.id
        LEFT JOIN likes l ON l.blog_id=b.id
        WHERE b.status='approved'
        GROUP BY b.id, b.title, b.category, u.username
        ORDER BY likes DESC LIMIT 5
    """)
    top_blogs = cur.fetchall()
    cur.execute("""
        SELECT u.username, ap.full_name, u.is_trusted,
               COUNT(DISTINCT b.id) as blog_count,
               COUNT(DISTINCT l.id) as total_likes
        FROM users u LEFT JOIN blogs b ON b.author_id=u.id AND b.status='approved'
        LEFT JOIN likes l ON l.blog_id=b.id
        LEFT JOIN author_profiles ap ON ap.user_id=u.id
        WHERE u.role IN ('author','admin')
        GROUP BY u.id, u.username, ap.full_name, u.is_trusted
        ORDER BY blog_count DESC LIMIT 5
    """)
    top_authors = cur.fetchall()
    cur.execute("""
        SELECT b.id, b.title, u.username, COUNT(c.id) as comment_count
        FROM blogs b JOIN users u ON b.author_id=u.id
        LEFT JOIN comments c ON c.blog_id=b.id
        WHERE b.status='approved'
        GROUP BY b.id, b.title, u.username
        ORDER BY comment_count DESC LIMIT 5
    """)
    most_commented = cur.fetchall()
    cur.close()
    return render_template('admin/reports.html',
        total_users=total_users, total_blogs=total_blogs,
        total_comments=total_comments, total_likes=total_likes,
        total_authors=total_authors, total_readers=total_readers,
        total_trusted=total_trusted,
        blogs_by_category=blogs_by_category, top_blogs=top_blogs,
        top_authors=top_authors, most_commented=most_commented)

# ── help ─────────────────────────────────────────────────────

@app.route('/help')
def help_page():
    return render_template('help.html')

# ── run ──────────────────────────────────────────────────────

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(os.path.join(UPLOAD_FOLDER, 'profiles'), exist_ok=True)
    app.run(debug=False)