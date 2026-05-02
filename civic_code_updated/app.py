import os
import hashlib
import json
import base64
from datetime import datetime
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, flash, send_from_directory)
from flask_socketio import SocketIO, emit, join_room, leave_room
from database import init_db, get_db
from behavior import update_behavior, get_vibe_label

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "civic-code-secret-2024")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

CATEGORIES = [
    "Politics", "Technology", "Science", "Sports", "Entertainment",
    "Health", "Education", "Environment", "Finance", "Culture",
    "Travel", "Food", "Fashion", "Gaming", "Music", "Art", "General"
]

AI_BASE_URL = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL", "")
AI_API_KEY = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY", "")


def get_ai_client():
    from openai import OpenAI
    return OpenAI(base_url=AI_BASE_URL, api_key=AI_API_KEY)


def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def get_current_user():
    if "user_id" not in session:
        return None
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    db.close()
    return user


def save_upload(file_storage):
    if not file_storage or file_storage.filename == "":
        return ""
    ext = file_storage.filename.rsplit(".", 1)[-1].lower()
    if ext not in {"png", "jpg", "jpeg", "gif", "webp", "mp4", "mov"}:
        return ""
    fname = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}.{ext}"
    file_storage.save(os.path.join(UPLOAD_FOLDER, fname))
    return fname


@app.route("/")
def index():
    if "user_id" not in session:
        return render_template("landing.html")
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    cat = request.args.get("cat", "")
    search = request.args.get("q", "")

    query = """
        SELECT p.*, u.username, u.avatar, u.ai_name,
               (SELECT COUNT(*) FROM post_likes WHERE post_id = p.id) AS like_count,
               (SELECT COUNT(*) FROM post_comments WHERE post_id = p.id) AS comment_count,
               (SELECT 1 FROM post_likes WHERE post_id = p.id AND user_id = ?) AS liked
        FROM posts p JOIN users u ON p.user_id = u.id
    """
    params = [session["user_id"]]
    conditions = []
    if cat:
        conditions.append("p.category = ?")
        params.append(cat)
    if search:
        conditions.append("(p.content LIKE ? OR u.username LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY p.created_at DESC LIMIT 50"

    posts = db.execute(query, params).fetchall()

    stories_raw = db.execute("""
        SELECT s.*, u.username, u.avatar
        FROM stories s JOIN users u ON s.user_id = u.id
        WHERE s.expires_at > datetime('now')
        ORDER BY s.created_at DESC
    """).fetchall()

    story_users = {}
    for s in stories_raw:
        uid = s["user_id"]
        if uid not in story_users:
            story_users[uid] = {"username": s["username"], "avatar": s["avatar"], "stories": []}
        story_users[uid]["stories"].append(dict(s))

    unread = db.execute("""
        SELECT COUNT(*) AS cnt FROM messages
        WHERE receiver_id = ? AND is_read = 0
    """, (session["user_id"],)).fetchone()["cnt"]

    db.close()
    vibe_label, vibe_color = get_vibe_label(user["behavior_score"])
    return render_template("feed.html", user=user, posts=posts,
                           story_users=list(story_users.values()),
                           categories=CATEGORIES, selected_cat=cat,
                           search=search, unread=unread,
                           vibe_label=vibe_label, vibe_color=vibe_color)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip() or None
        phone = request.form.get("phone", "").strip() or None
        password = request.form.get("password", "")
        full_name = request.form.get("full_name", "").strip()
        address = request.form.get("address", "").strip()

        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template("register.html")
        if not email and not phone:
            flash("Provide at least an email or phone number.", "error")
            return render_template("register.html")

        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (username, email, phone, password, full_name, address) VALUES (?,?,?,?,?,?)",
                (username, email, phone, hash_password(password), full_name, address)
            )
            db.commit()
            user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            session["user_id"] = user["id"]
            db.close()
            return redirect(url_for("index"))
        except Exception as e:
            db.close()
            flash("Username, email or phone already taken.", "error")
            return render_template("register.html")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE (email=? OR phone=? OR username=?) AND password=?",
            (identifier, identifier, identifier, hash_password(password))
        ).fetchone()
        db.close()
        if user:
            session["user_id"] = user["id"]
            return redirect(url_for("index"))
        flash("Invalid credentials.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/post", methods=["POST"])
@login_required
def create_post():
    content = request.form.get("content", "").strip()
    category = request.form.get("category", "General")
    if not content:
        flash("Post cannot be empty.", "error")
        return redirect(url_for("index"))
    image = request.files.get("image")
    image_path = save_upload(image) if image else ""
    db = get_db()
    db.execute(
        "INSERT INTO posts (user_id, content, category, image_path) VALUES (?,?,?,?)",
        (session["user_id"], content, category, image_path)
    )
    delta, has_bad = update_behavior(db, session["user_id"], content, "post")
    db.commit()
    db.close()
    if has_bad:
        flash("⚠️ Watch your language! Your vibe score dropped.", "warning")
    return redirect(url_for("index"))


@app.route("/post/<int:post_id>/like", methods=["POST"])
@login_required
def like_post(post_id):
    db = get_db()
    existing = db.execute(
        "SELECT id FROM post_likes WHERE post_id=? AND user_id=?",
        (post_id, session["user_id"])
    ).fetchone()
    if existing:
        db.execute("DELETE FROM post_likes WHERE post_id=? AND user_id=?",
                   (post_id, session["user_id"]))
        liked = False
    else:
        db.execute("INSERT INTO post_likes (post_id, user_id) VALUES (?,?)",
                   (post_id, session["user_id"]))
        liked = True
    count = db.execute("SELECT COUNT(*) AS c FROM post_likes WHERE post_id=?", (post_id,)).fetchone()["c"]
    db.commit()
    db.close()
    return jsonify({"liked": liked, "count": count})


@app.route("/post/<int:post_id>/comment", methods=["POST"])
@login_required
def comment_post(post_id):
    content = request.form.get("content", "").strip()
    if not content:
        return jsonify({"error": "empty"}), 400
    db = get_db()
    db.execute(
        "INSERT INTO post_comments (post_id, user_id, content) VALUES (?,?,?)",
        (post_id, session["user_id"], content)
    )
    update_behavior(db, session["user_id"], content, "comment")
    db.commit()
    user = db.execute("SELECT username, avatar FROM users WHERE id=?", (session["user_id"],)).fetchone()
    db.close()
    return jsonify({"username": user["username"], "content": content,
                    "avatar": user["avatar"], "time": "just now"})


@app.route("/post/<int:post_id>/comments")
@login_required
def get_comments(post_id):
    db = get_db()
    comments = db.execute("""
        SELECT c.content, c.created_at, u.username, u.avatar
        FROM post_comments c JOIN users u ON c.user_id = u.id
        WHERE c.post_id = ? ORDER BY c.created_at ASC
    """, (post_id,)).fetchall()
    db.close()
    return jsonify([dict(c) for c in comments])


@app.route("/post/<int:post_id>/report", methods=["POST"])
@login_required
def report_post(post_id):
    reason = request.form.get("reason", "").strip()
    if not reason:
        return jsonify({"error": "Reason required"}), 400
    db = get_db()
    post = db.execute("SELECT user_id FROM posts WHERE id=?", (post_id,)).fetchone()
    db.execute(
        "INSERT INTO reports (reporter_id, reported_user_id, reported_post_id, reason) VALUES (?,?,?,?)",
        (session["user_id"], post["user_id"] if post else None, post_id, reason)
    )
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/story/add", methods=["POST"])
@login_required
def add_story():
    image = request.files.get("image")
    caption = request.form.get("caption", "")
    if not image:
        flash("Image required for story.", "error")
        return redirect(url_for("index"))
    image_path = save_upload(image)
    if not image_path:
        flash("Invalid file type.", "error")
        return redirect(url_for("index"))
    db = get_db()
    db.execute(
        "INSERT INTO stories (user_id, image_path, caption) VALUES (?,?,?)",
        (session["user_id"], image_path, caption)
    )
    db.commit()
    db.close()
    return redirect(url_for("index"))


@app.route("/messages")
@login_required
def messages():
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    conversations = db.execute("""
        SELECT DISTINCT
            CASE WHEN m.sender_id = ? THEN m.receiver_id ELSE m.sender_id END AS other_id,
            u.username, u.avatar,
            (SELECT content FROM messages
             WHERE (sender_id=? AND receiver_id=other_id) OR (sender_id=other_id AND receiver_id=?)
             ORDER BY created_at DESC LIMIT 1) AS last_msg,
            (SELECT COUNT(*) FROM messages WHERE sender_id=other_id AND receiver_id=? AND is_read=0) AS unread_count
        FROM messages m
        JOIN users u ON u.id = CASE WHEN m.sender_id=? THEN m.receiver_id ELSE m.sender_id END
        WHERE m.sender_id=? OR m.receiver_id=?
        ORDER BY m.created_at DESC
    """, (session["user_id"],) * 7).fetchall()
    all_users = db.execute(
        "SELECT id, username, avatar FROM users WHERE id != ? ORDER BY username",
        (session["user_id"],)
    ).fetchall()
    db.close()
    return render_template("messages.html", user=user, conversations=conversations,
                           all_users=all_users)


@app.route("/messages/<int:other_id>")
@login_required
def chat(other_id):
    db = get_db()
    db.execute(
        "UPDATE messages SET is_read=1 WHERE sender_id=? AND receiver_id=?",
        (other_id, session["user_id"])
    )
    db.commit()
    user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    other = db.execute("SELECT * FROM users WHERE id=?", (other_id,)).fetchone()
    if not other:
        db.close()
        return redirect(url_for("messages"))
    msgs = db.execute("""
        SELECT m.*, u.username, u.avatar FROM messages m
        JOIN users u ON m.sender_id = u.id
        WHERE (m.sender_id=? AND m.receiver_id=?) OR (m.sender_id=? AND m.receiver_id=?)
        ORDER BY m.created_at ASC LIMIT 100
    """, (session["user_id"], other_id, other_id, session["user_id"])).fetchall()
    db.close()
    return render_template("chat.html", user=user, other=other, msgs=msgs)


@app.route("/messages/send", methods=["POST"])
@login_required
def send_message():
    data = request.get_json()
    receiver_id = data.get("receiver_id")
    content = data.get("content", "").strip()
    if not content or not receiver_id:
        return jsonify({"error": "invalid"}), 400
    db = get_db()
    db.execute(
        "INSERT INTO messages (sender_id, receiver_id, content) VALUES (?,?,?)",
        (session["user_id"], receiver_id, content)
    )
    update_behavior(db, session["user_id"], content, "message")
    db.commit()
    user = db.execute("SELECT username, avatar FROM users WHERE id=?", (session["user_id"],)).fetchone()
    db.close()
    return jsonify({"ok": True, "username": user["username"],
                    "avatar": user["avatar"],
                    "time": datetime.utcnow().strftime("%H:%M")})


@app.route("/messages/poll/<int:other_id>")
@login_required
def poll_messages(other_id):
    after = request.args.get("after", "1970-01-01 00:00:00")
    db = get_db()
    db.execute(
        "UPDATE messages SET is_read=1 WHERE sender_id=? AND receiver_id=?",
        (other_id, session["user_id"])
    )
    db.commit()
    msgs = db.execute("""
        SELECT m.id, m.content, m.created_at, m.sender_id, u.username, u.avatar
        FROM messages m JOIN users u ON m.sender_id = u.id
        WHERE ((m.sender_id=? AND m.receiver_id=?) OR (m.sender_id=? AND m.receiver_id=?))
        AND m.created_at > ?
        ORDER BY m.created_at ASC
    """, (session["user_id"], other_id, other_id, session["user_id"], after)).fetchall()
    db.close()
    return jsonify([dict(m) for m in msgs])


@app.route("/find-by-phone", methods=["POST"])
@login_required
def find_by_phone():
    phone = request.form.get("phone", "").strip()
    db = get_db()
    user = db.execute(
        "SELECT id, username, avatar FROM users WHERE phone=? AND id != ?",
        (phone, session["user_id"])
    ).fetchone()
    db.close()
    if user:
        return redirect(url_for("chat", other_id=user["id"]))
    flash("No user found with that phone number.", "error")
    return redirect(url_for("messages"))


@app.route("/ai-chat")
@login_required
def ai_chat():
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    history = db.execute(
        "SELECT role, content, created_at FROM ai_messages WHERE user_id=? ORDER BY created_at DESC LIMIT 30",
        (session["user_id"],)
    ).fetchall()
    db.close()
    return render_template("ai_chat.html", user=user, history=list(reversed(history)))


@app.route("/ai-chat/send", methods=["POST"])
@login_required
def ai_chat_send():
    data = request.get_json()
    user_msg = data.get("message", "").strip()
    if not user_msg:
        return jsonify({"error": "empty"}), 400

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    ai_name = user["ai_name"] or "Aria"

    history = db.execute(
        "SELECT role, content FROM ai_messages WHERE user_id=? ORDER BY created_at ASC LIMIT 40",
        (session["user_id"],)
    ).fetchall()

    messages = [
        {
            "role": "system",
            "content": (
                f"You are {ai_name}, a loyal personal AI friend of {user['username']}. "
                "You remember everything about your conversations. You speak like a real Gen-Z friend — "
                "casual, warm, funny, supportive, never robotic. You use emojis sometimes but not excessively. "
                "You keep track of everything the user tells you about themselves — their life, feelings, "
                "interests, goals — and reference it naturally. You are always on their side."
            )
        }
    ]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_msg})

    try:
        client = get_ai_client()
        response = client.chat.completions.create(
            model="gpt-5",
            messages=messages,
            max_tokens=500
        )
        reply = response.choices[0].message.content
    except Exception as e:
        reply = f"Oops, I'm having a moment 😅 Try again? ({str(e)[:60]})"

    db.execute("INSERT INTO ai_messages (user_id, role, content) VALUES (?,?,?)",
               (session["user_id"], "user", user_msg))
    db.execute("INSERT INTO ai_messages (user_id, role, content) VALUES (?,?,?)",
               (session["user_id"], "assistant", reply))
    db.commit()
    db.close()
    return jsonify({"reply": reply, "ai_name": ai_name})


@app.route("/translate", methods=["POST"])
@login_required
def translate():
    data = request.get_json()
    text = data.get("text", "").strip()
    target_lang = data.get("lang", "Spanish")
    if not text:
        return jsonify({"error": "empty"}), 400
    try:
        client = get_ai_client()
        response = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": f"You are a translator. Translate the following text to {target_lang}. Output ONLY the translated text, nothing else."},
                {"role": "user", "content": text}
            ],
            max_tokens=300
        )
        translated = response.choices[0].message.content
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"translated": translated})


@app.route("/profile/<username>")
@login_required
def profile(username):
    db = get_db()
    me = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    target = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not target:
        db.close()
        return "User not found", 404
    posts = db.execute("""
        SELECT p.*,
               (SELECT COUNT(*) FROM post_likes WHERE post_id=p.id) AS like_count,
               (SELECT COUNT(*) FROM post_comments WHERE post_id=p.id) AS comment_count
        FROM posts p WHERE p.user_id=? ORDER BY p.created_at DESC
    """, (target["id"],)).fetchall()
    followers = db.execute("SELECT COUNT(*) AS c FROM follows WHERE following_id=?", (target["id"],)).fetchone()["c"]
    following = db.execute("SELECT COUNT(*) AS c FROM follows WHERE follower_id=?", (target["id"],)).fetchone()["c"]
    is_following = db.execute(
        "SELECT 1 FROM follows WHERE follower_id=? AND following_id=?",
        (session["user_id"], target["id"])
    ).fetchone()
    vibe_label, vibe_color = get_vibe_label(target["behavior_score"])
    db.close()
    return render_template("profile.html", me=me, user=target, posts=posts,
                           followers=followers, following=following,
                           is_following=bool(is_following),
                           vibe_label=vibe_label, vibe_color=vibe_color)


@app.route("/follow/<int:user_id>", methods=["POST"])
@login_required
def follow_user(user_id):
    if user_id == session["user_id"]:
        return jsonify({"error": "Cannot follow yourself"}), 400
    db = get_db()
    existing = db.execute(
        "SELECT 1 FROM follows WHERE follower_id=? AND following_id=?",
        (session["user_id"], user_id)
    ).fetchone()
    if existing:
        db.execute("DELETE FROM follows WHERE follower_id=? AND following_id=?",
                   (session["user_id"], user_id))
        following = False
    else:
        db.execute("INSERT INTO follows (follower_id, following_id) VALUES (?,?)",
                   (session["user_id"], user_id))
        following = True
    count = db.execute("SELECT COUNT(*) AS c FROM follows WHERE following_id=?", (user_id,)).fetchone()["c"]
    db.commit()
    db.close()
    return jsonify({"following": following, "count": count})


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if request.method == "POST":
        bio = request.form.get("bio", "").strip()
        ai_name = request.form.get("ai_name", "Aria").strip() or "Aria"
        theme_primary = request.form.get("theme_primary", "#ff006e")
        theme_bg = request.form.get("theme_bg", "#0a0a0a")
        avatar_file = request.files.get("avatar")
        avatar = save_upload(avatar_file) if avatar_file and avatar_file.filename else user["avatar"]
        db.execute(
            "UPDATE users SET bio=?, ai_name=?, theme_primary=?, theme_bg=?, avatar=? WHERE id=?",
            (bio, ai_name, theme_primary, theme_bg, avatar, session["user_id"])
        )
        db.commit()
        flash("Settings saved!", "success")
        db.close()
        return redirect(url_for("settings"))
    behavior_log = db.execute(
        "SELECT * FROM behavior_log WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
        (session["user_id"],)
    ).fetchall()
    vibe_label, vibe_color = get_vibe_label(user["behavior_score"])
    db.close()
    return render_template("settings.html", user=user, behavior_log=behavior_log,
                           vibe_label=vibe_label, vibe_color=vibe_color)


@app.route("/report/user/<int:user_id>", methods=["POST"])
@login_required
def report_user(user_id):
    reason = request.form.get("reason", "").strip()
    if not reason:
        return jsonify({"error": "Reason required"}), 400
    db = get_db()
    db.execute(
        "INSERT INTO reports (reporter_id, reported_user_id, reason) VALUES (?,?,?)",
        (session["user_id"], user_id, reason)
    )
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/search")
@login_required
def search_users():
    q = request.args.get("q", "").strip()
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    results = []
    if q:
        results = db.execute(
            "SELECT id, username, avatar, bio, behavior_score FROM users WHERE username LIKE ? AND id != ? LIMIT 20",
            (f"%{q}%", session["user_id"])
        ).fetchall()
    db.close()
    return render_template("search.html", user=user, results=results, q=q)


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route("/call/<int:other_id>")
@login_required
def call_page(other_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    other = db.execute("SELECT id, username, avatar FROM users WHERE id=?", (other_id,)).fetchone()
    db.close()
    if not other:
        return redirect(url_for("messages"))
    return render_template("call.html", user=user, other=other)


# ── WebRTC Signaling via SocketIO ─────────────────────────────────────────────

@socketio.on("join_call_room")
def on_join_call(data):
    room = data.get("room")
    if room:
        join_room(room)

@socketio.on("leave_call_room")
def on_leave_call(data):
    room = data.get("room")
    if room:
        leave_room(room)

@socketio.on("call_request")
def on_call_request(data):
    emit("incoming_call", data, room=data.get("room"))

@socketio.on("call_accepted")
def on_call_accepted(data):
    emit("call_accepted", data, room=data.get("room"))

@socketio.on("call_rejected")
def on_call_rejected(data):
    emit("call_rejected", data, room=data.get("room"))

@socketio.on("call_ended")
def on_call_ended(data):
    emit("call_ended", data, room=data.get("room"))

@socketio.on("webrtc_offer")
def on_offer(data):
    emit("webrtc_offer", data, room=data.get("room"))

@socketio.on("webrtc_answer")
def on_answer(data):
    emit("webrtc_answer", data, room=data.get("room"))

@socketio.on("webrtc_ice")
def on_ice(data):
    emit("webrtc_ice", data, room=data.get("room"))


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 3000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
