import json

from magnus_tutor import courses as C
from magnus_tutor import store
from magnus_tutor.db import get_db
from magnus_tutor.quiz import Quizzer, attempt_score, observe


async def test_quiz_question_grade_and_mastery(p, fake_models):
    mm, fp = fake_models
    course = C.create_course("Electricity and Magnetism", short="E&M", topics=["Gauss's law"], p=p)
    db = get_db(p)

    def reply(messages, fmt=None, **kw):
        props = (fmt or {}).get("properties", {}) if isinstance(fmt, dict) else {}
        if "question" in props:
            return json.dumps({"question": "A 3 nC charge; field at 0.2 m?", "answer": "674 N/C", "concept": "Coulomb's law", "source": "general knowledge", "kind": "computational"})
        return json.dumps({"grade": 0.3, "feedback": "Check your units.", "missing": ""})

    fp.reply = reply
    sid = store.create_session(db, course.slug, "quiz")
    qz = Quizzer(p, db, mm)
    q = await qz.next_question(sid, course)
    assert q["concept"] == "Coulomb's law" and "answer" not in q
    g = await qz.grade(q["id"], "I got E = 675 N/C", course)
    assert g["grade"] >= 0.9, "a matching numeric answer is right even if the model under-grades it"
    m = db.one("SELECT * FROM mastery WHERE course = ? AND concept = ?", (course.slug, "Coulomb's law"))
    assert m and m["score"] > 0.5 and m["evidence"] == 1
    q2 = await qz.next_question(sid, course)
    g2 = await qz.grade(q2["id"], "E = 135 N/C", course)
    assert g2["grade"] <= 0.5
    assert db.one("SELECT evidence FROM mastery WHERE course = ? AND concept = ?", (course.slug, "Coulomb's law"))["evidence"] == 2


def test_attempt_scores_and_ema(p):
    db = get_db(p)
    assert attempt_score(0, True, False) == 1.0
    assert attempt_score(3, True, False) == 0.55
    assert attempt_score(2, False, True) == 0.2
    assert attempt_score(1, False, False) is None
    for _ in range(5):
        observe(db, "em", ["Gauss's law"], 0.0)
    assert db.one("SELECT score FROM mastery WHERE concept = ?", ("Gauss's law",))["score"] < 0.1
