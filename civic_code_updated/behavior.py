import re

BAD_WORDS = [
    "hate", "idiot", "stupid", "dumb", "loser", "ugly", "trash", "moron",
    "freak", "pathetic", "disgusting", "worthless", "shut up", "die",
    "kill", "horrible", "terrible", "awful", "jerk", "ass", "damn",
    "crap", "hell", "bastard", "bitch", "cunt", "fuck", "shit", "retard"
]

GOOD_WORDS = [
    "love", "amazing", "great", "awesome", "kind", "beautiful", "wonderful",
    "inspiring", "support", "respect", "thank", "please", "help", "care",
    "proud", "happy", "excellent", "brilliant", "fantastic", "incredible",
    "nice", "good", "well", "smart", "creative", "talented", "genuine",
    "honest", "empathy", "compassion", "encourage", "uplift", "celebrate"
]


def analyze_text(text: str):
    text_lower = text.lower()
    bad_count = sum(1 for w in BAD_WORDS if re.search(r'\b' + re.escape(w) + r'\b', text_lower))
    good_count = sum(1 for w in GOOD_WORDS if re.search(r'\b' + re.escape(w) + r'\b', text_lower))
    delta = (good_count * 2) - (bad_count * 5)
    return delta, bad_count > 0


def update_behavior(conn, user_id: int, text: str, reason: str = "post"):
    delta, has_bad = analyze_text(text)
    if delta == 0:
        return 0, has_bad

    conn.execute(
        "UPDATE users SET behavior_score = MAX(0, MIN(100, behavior_score + ?)) WHERE id = ?",
        (delta, user_id)
    )
    conn.execute(
        "INSERT INTO behavior_log (user_id, change, reason) VALUES (?, ?, ?)",
        (user_id, delta, reason)
    )
    return delta, has_bad


def get_vibe_label(score: int):
    if score >= 90:
        return "Beacon", "#00ff88"
    elif score >= 75:
        return "Positive", "#7fff00"
    elif score >= 55:
        return "Neutral", "#ffdd00"
    elif score >= 35:
        return "Shaky", "#ff8800"
    elif score >= 15:
        return "Toxic", "#ff4400"
    else:
        return "Banned Zone", "#ff0000"
