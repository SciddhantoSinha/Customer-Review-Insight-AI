from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
import os
from datetime import datetime
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import logging

# -------------------------
# Basic app + DB setup (unchanged logic)
# -------------------------
app = Flask(__name__)
app.secret_key = "supersecretkey"

DB_NAME = "users.db"

# ---------- Helpers ----------
def get_conn():
    return sqlite3.connect(DB_NAME)

# ---------- Initialize DB ----------
def init_db():
    conn = get_conn()
    c = conn.cursor()

    # Users table
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    # Reviews table
    c.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            review_text TEXT,
            filename TEXT,
            uploaded_at TEXT
        )
    """)

    conn.commit()
    conn.close()

init_db()

# ---------- File Upload Config ----------
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# -------------------------
# Sentiment (NLTK VADER) setup — safe guarded
# -------------------------
SIA = None
try:
    import nltk
    from nltk.sentiment import SentimentIntensityAnalyzer

    try:
        # Try to construct analyzer (will raise LookupError if lexicon missing)
        SIA = SentimentIntensityAnalyzer()
    except LookupError:
        # Attempt to download the lexicon programmatically (only if downloader allowed)
        try:
            nltk.download("vader_lexicon")
            SIA = SentimentIntensityAnalyzer()
        except Exception as e:
            logging.warning("Failed to download vader_lexicon automatically: %s", e)
            SIA = None
except Exception as e:
    # NLTK not installed or other import error
    logging.warning("NLTK/Sentiment not available: %s", e)
    SIA = None

def analyze_sentiment(text: str):
    """
    Return (label, confidence_percent)
    label is one of 'positive', 'neutral', 'negative'.
    confidence_percent is float 0..100 (approx from VADER's compound score).
    If the analyzer is not available, returns ('neutral', 0.0) (graceful fallback).
    """
    if not text:
        return ("neutral", 0.0)

    if SIA is None:
        # Sentiment engine not available; keep app running but return neutral/fallback.
        return ("neutral", 0.0)

    try:
        scores = SIA.polarity_scores(text)
        compound = scores.get("compound", 0.0)
        if compound >= 0.05:
            label = "positive"
        elif compound <= -0.05:
            label = "negative"
        else:
            label = "neutral"

        confidence = round(abs(compound) * 100, 2)  # map -1..1 -> 0..100
        return (label, confidence)
    except Exception as e:
        logging.exception("Sentiment analysis error:")
        return ("neutral", 0.0)

# -------------------------
# Existing routes (unchanged)
# -------------------------

# ---------- HOME ----------
@app.route("/")
def index():
    return render_template("index.html")

# ---------- REGISTER ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username").strip()
        email = request.form.get("email").strip()
        password = request.form.get("password").strip()
        confirm_password = request.form.get("confirm_password").strip()

        if password != confirm_password:
            flash("Passwords do not match!", "danger")
            return redirect(url_for("register"))

        hashed_password = generate_password_hash(password)

        try:
            conn = get_conn()
            c = conn.cursor()
            c.execute(
                "INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
                (username, email, hashed_password)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            flash("Username or Email already exists!", "danger")
            return redirect(url_for("register"))
        finally:
            conn.close()

        flash("Account created successfully! Please login.", "success")
        return redirect(url_for("user_login"))

    return render_template("register.html")

# ---------- USER LOGIN ----------
@app.route("/user_login", methods=["GET", "POST"])
def user_login():
    if request.method == "POST":
        email = request.form.get("email").strip()
        password = request.form.get("password").strip()

        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT password FROM users WHERE email=?", (email,))
        row = c.fetchone()
        conn.close()

        if row is None:
            flash("Email not registered!", "danger")
            return redirect(url_for("user_login"))

        stored_password = row[0]

        if check_password_hash(stored_password, password):
            session["user"] = email
            flash("Login successful!", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Incorrect password!", "danger")
            return redirect(url_for("user_login"))

    return render_template("login.html")

# ---------- DASHBOARD ----------
@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "user" not in session:
        flash("Please login first", "warning")
        return redirect(url_for("user_login"))

    if request.method == "POST":
        review_text = request.form.get("review").strip()
        file = request.files.get("file")
        filename = None

        if file and file.filename:
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

        conn = get_conn()
        c = conn.cursor()
        c.execute(
            "INSERT INTO reviews (email, review_text, filename, uploaded_at) VALUES (?, ?, ?, ?)",
            (session["user"], review_text, filename, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
        conn.close()

        flash("Review submitted successfully!", "success")
        return redirect(url_for("dashboard"))

    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT review_text, filename, uploaded_at FROM reviews WHERE email=? ORDER BY id DESC",
        (session["user"],)
    )
    reviews = c.fetchall()
    conn.close()

    return render_template("dashboard.html", user=session["user"], reviews=reviews)

# ---------- ADMIN LOGIN ----------
@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username").strip()
        password = request.form.get("password").strip()

        if username == "admin" and password == "admin123":
            session["admin"] = True
            flash("Welcome Admin!", "success")
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Invalid admin credentials!", "danger")
            return redirect(url_for("admin_login"))

    return render_template("admin_login.html")

# ---------- ADMIN DASHBOARD (updated to include sentiment + users + chart data) ----------
@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"):
        flash("Please login as admin first!", "warning")
        return redirect(url_for("admin_login"))

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT email, review_text, filename, uploaded_at FROM reviews ORDER BY id DESC")
    rows = c.fetchall()

    reviews = []
    pos = neg = neu = 0
    for row in rows:
        email = row[0]
        review_text = row[1] if row[1] is not None else ""
        filename = row[2]
        uploaded_at = row[3]

        label, confidence = analyze_sentiment(review_text)
        if label == "positive":
            pos += 1
        elif label == "negative":
            neg += 1
        else:
            neu += 1

        reviews.append({
            "email": email,
            "review_text": review_text,
            "filename": filename,
            "uploaded_at": uploaded_at,
            "sentiment": label,
            "confidence": confidence
        })

    # Fetch users to show in Users tab
    c.execute("SELECT id, username, email FROM users ORDER BY id DESC")
    user_rows = c.fetchall()
    users = [{"id": u[0], "username": u[1], "email": u[2]} for u in user_rows]

    conn.close()

    chart_data = {"positive": pos, "negative": neg, "neutral": neu}
    sentiment_available = SIA is not None

    # Pass chart_data dict and let template render it with tojson filter
    return render_template("admin_dashboard.html",
                           reviews=reviews,
                           users=users,
                           chart_data=chart_data,
                           sentiment_available=sentiment_available)

# ---------- LOGOUT ----------
@app.route("/logout")
def logout():
    session.pop("user", None)
    session.pop("admin", None)
    flash("Logged out successfully!", "info")
    return redirect(url_for("index"))

if __name__ == "__main__":
    # debug=True left as before to help you while developing — remove in production
    app.run(debug=True)
