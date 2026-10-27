"""
ממשק בדיקה לאג'נט.

    python -m streamlit run ui.py

שלוש לשוניות:
  1. הרצת האג'נט על בקשה אחת, עם כל צעד בזמן אמת ופסק דין של השופט.
  2. ארגז חול: להפעיל את הכלים ידנית, בלי AI ובלי לבזבז מכסה.
  3. תרחישי קצה: להריץ כמה תרחישים ברצף ולקבל טבלת ציונים.
"""
import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from openai import APIError

from agent import MAX_STEPS, SYSTEM_PROMPT, describe_request, short_args, summarize
from evaluate import VERDICT_ICON, find_best_menu, run_scenario
from hebrew_page import mark_page_hebrew
from goal_parser import parse_goal, unknown_dislikes
from llm import DEFAULT_MODEL, ConfigError, create_client, fallback_models
from scenarios import SCENARIOS
from tools import ALLERGENS, CATEGORIES, DIETS, MenuRequest, MenuTools

st.set_page_config(page_title="בודק האג'נט", page_icon="🍽️", layout="wide")
mark_page_hebrew()  # אחרת הדפדפן "מתרגם" את העברית ומשבש אותה
st.markdown("""<style>
.stApp, [data-testid="stSidebar"] { direction: rtl; text-align: right; }
textarea, pre, code, [data-testid="stJson"] { direction: ltr; text-align: left; }
</style>""", unsafe_allow_html=True)

ALLERGEN_BY_HEBREW = {v: k for k, v in ALLERGENS.items()}
DIET_BY_HEBREW = {v: k for k, v in DIETS.items()}
SCENARIO_BY_ID = {s["id"]: s for s in SCENARIOS}
EXPECT_TEXT = {"success": "תפריט מאושר", "failure": "דיווח שאין פתרון", "invalid": "פסילת הקלט"}
COLUMNS = ["שם", "אלרגיות", "תזונה", "לא אוהב/ת", "אוהב/ת"]


# ---------- עריכת הבקשה ----------

def _cell(value) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip()


def _split(text: str) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def guests_to_table(guests: list[dict]) -> pd.DataFrame:
    rows = [{
        "שם": g.get("name", ""),
        "אלרגיות": ", ".join(ALLERGENS.get(a, a) for a in g.get("allergies", [])),
        "תזונה": DIETS.get(g.get("diet"), g.get("diet") or ""),
        "לא אוהב/ת": ", ".join(g.get("dislikes", [])),
        "אוהב/ת": ", ".join(g.get("likes", [])),
    } for g in guests]
    return pd.DataFrame(rows, columns=COLUMNS)


def table_to_guests(table: pd.DataFrame) -> list[dict]:
    guests = []
    for row in table.to_dict("records"):
        name, allergies = _cell(row.get("שם")), _split(_cell(row.get("אלרגיות")))
        diet, dislikes = _cell(row.get("תזונה")), _split(_cell(row.get("לא אוהב/ת")))
        likes = _split(_cell(row.get("אוהב/ת")))
        if not (name or allergies or diet or dislikes or likes):
            continue  # שורה ריקה
        guests.append({
            "name": name,
            # אפשר לכתוב "אגוזים" או "nuts". ערך לא מוכר עובר כמו שהוא, והבדיקה תפסול אותו
            "allergies": [ALLERGEN_BY_HEBREW.get(a, a) for a in allergies],
            "diet": DIET_BY_HEBREW.get(diet, diet) or None,
            "dislikes": dislikes,
            "likes": likes,
        })
    return guests


def request_editor(scenario: dict) -> dict:
    """טופס עריכה. המפתחות כוללים את מזהה התרחיש, כך שהחלפת תרחיש (או פענוח חדש) מאפסת את הטופס."""
    sid, data = scenario["id"], scenario["request"]
    c1, c2 = st.columns(2)
    num_guests = c1.number_input("מספר סועדים", value=data["num_guests"], step=1, key=f"n_{sid}")
    budget = c2.number_input("תקציב (ש\"ח)", value=float(data["budget"]), step=10.0, key=f"b_{sid}")
    st.caption("אלרגיות אפשריות: " + ", ".join(f"{he} ({en})" for en, he in ALLERGENS.items())
               + ". כמה ערכים - מופרדים בפסיק.")
    table = st.data_editor(
        guests_to_table(data["guests"]), num_rows="dynamic", width="stretch", key=f"g_{sid}",
        column_config={"תזונה": st.column_config.SelectboxColumn(options=["", *DIETS.values()])},
    )
    request = {"num_guests": int(num_guests), "budget": budget, "guests": table_to_guests(table)}
    if st.checkbox("🕍 ארוחה כשרה (בלי בשר וחלב יחד)", value=bool(data.get("kosher")), key=f"k_{sid}"):
        request["kosher"] = True
    if data.get("target_budget") is not None:  # "בערך" - היעד נשמר, והתקציב בטופס הוא המקסימום
        st.caption(f"התקציב בקירוב: יעד {data['target_budget']:g} ש\"ח, ומקסימום כפי שמופיע למעלה")
        request["target_budget"] = min(data["target_budget"], budget)
    return request


# ---------- הרצה והצגה ----------

def render_event(box, kind: str, data: dict):
    """מציג אירוע אחד של האג'נט. נקרא בזמן אמת, ושוב כשמציגים ריצה שמורה."""
    if kind == "step":
        box.markdown(f"**צעד {data['step']}/{data['max_steps']}**")
    elif kind == "thought":
        box.markdown(f"💭 {data['text']}")
    elif kind == "tool":
        name, result = data["name"], data["result"]
        icon = "⚠️" if "error" in result else "🔧"
        label = summarize(name, result).replace("\n     - ", " • ")
        target = f"({short_args(data['args'])})" if data["args"] else ""
        expander = box.expander(f"{icon} {name}{target} ← {label}")
        if data["args"] is not None:
            expander.caption("מה המודל שלח")
            expander.json(data["args"])
        expander.caption("מה הכלי החזיר")
        expander.json(result)
    elif kind == "nudge":
        box.warning("↩ המודל עצר בלי תפריט מאושר - מבקשים ממנו להמשיך")
    elif kind == "low_steps":
        box.warning(f"↩ נשארו {data['remaining']} צעדים - מזהירים את המודל")
    elif kind == "rate_limit":
        box.warning(f"⏳ חריגה ממכסת הבקשות לדקה - ממתין {data['seconds']:.0f} שניות")
    elif kind == "fallback":
        box.warning(f"🔁 המודל {data['old']} לא זמין ({data['reason']}) - עובר ל-{data['new']}")


def run_agent(request_data: dict, model: str, max_steps: int, log) -> dict:
    """מריץ את האג'נט ומציג כל צעד ב-log בזמן אמת."""
    return run_scenario(request_data, model, max_steps,
                        on_event=lambda kind, data: render_event(log, kind, data), fallbacks=fallback_models())


def format_duration(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes}:{secs:02d} דקות" if minutes else f"{secs} שניות"


def show_result(r: dict):
    if r.get("verdict"):
        status, text = r["verdict"]
        show = {"pass": st.success, "partial": st.warning, "fail": st.error, "bug": st.error}[status]
        show(f"{VERDICT_ICON[status]} **שופט:** {text}")

    if r["status"] == "invalid":
        st.info(f"הודעת השגיאה: {r['error']}")
        return
    if r["status"] == "error":
        st.error(r["error"])
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("צעדים (קריאות למודל)", r["steps"])
    c2.metric("הפעלות כלים", r["tool_calls"])
    c3.metric("משך הריצה", format_duration(r["duration"]),
              help="במסלול החינמי יש עד 5 בקשות בדקה, אז ריצה ארוכה כוללת המתנות")
    budget = r["request"]["budget"]
    if r["status"] == "success":
        c4.metric("עלות", f"{r['approved']['total_cost']:.0f} ש\"ח", f"{budget - r['approved']['total_cost']:.0f} מתחת לתקציב")
        menu = r["approved"]["menu"]
        st.subheader("✅ התפריט")
        st.table(pd.DataFrame([
            {"סוג": "עיקרית", "מנה": menu["mains"][0]}, {"סוג": "עיקרית", "מנה": menu["mains"][1]},
            {"סוג": "תוספת", "מנה": menu["side"]}, {"סוג": "ירקות", "מנה": menu["vegetable"]},
            {"סוג": "קינוח", "מנה": menu["dessert"]},
        ]))
        for like in r["approved"].get("likes", []):
            where = f"✓ ב{', '.join(like['dishes'])}" if like["in_menu"] else "✗ לא נכנס לתפריט"
            st.write(f"{'❤️' if like['in_menu'] else '🤍'} {like['guest']} אוהב/ת {like['likes']}: {where}")
        with st.expander("🛒 רשימת קניות"):
            st.dataframe(pd.DataFrame(r["approved"]["shopping_list"]), width="stretch")
    else:
        if r["best_cost"]:
            c4.metric("הניסיון הזול ביותר", f"{r['best_cost']['total_cost']:.0f} ש\"ח",
                      f"{budget - r['best_cost']['total_cost']:.0f} מול התקציב")
        st.subheader("❌ האג'נט לא השלים את המשימה")
        st.write(f"**הסיבה שדווחה:** {r['failure_reason']}")
        if r["best_cost"]:
            st.write(f"**התפריט התקין הזול ביותר שבדק:** {short_args(r['best_cost'])} "
                     f"({r['best_cost']['total_cost']:.0f} ש\"ח)")

    with st.expander("🔍 מה השופט מצא (בדיקה של כל הצירופים)"):
        truth = r["truth"]
        st.write(f"תפריטים תקינים בכלל: **{truth['valid_menus']}**, מתוכם בתקציב: **{truth['valid_within_budget']}**")
        if truth["best"]:
            st.write(f"הזול ביותר: {short_args(truth['best'])} - **{truth['best']['total_cost']:.2f} ש\"ח**")


# ---------- הלשוניות ----------

def goal_input(model: str) -> tuple[dict | None, str, bool]:
    """
    מטרה בשפה חופשית: המודל מפענח, ואת התוצאה אפשר לערוך בטופס לפני ההרצה.
    מחזיר (בקשה, כותרת, אפשר להריץ).
    """
    text = st.text_area("מה התוכנית?", key="goal_text", height=100,
                        placeholder="למשל: אני מארחת 12 איש בשישי, יש לי 300 ש\"ח. אחותי טבעונית והבן שלה אלרגי לאגוזים.")
    if st.button("🧠 הבן את הבקשה"):
        try:
            client, default_model = create_client()
            with st.spinner("מפענח..."):
                result = parse_goal(client, model or default_model, text, fallbacks=fallback_models())
        except ConfigError as e:
            st.error(str(e))
            return None, "", False
        except APIError as e:  # כולל חריגה ממכסה
            st.error(f"שגיאה בתקשורת עם המודל ({type(e).__name__}). נסי מודל אחר בסרגל הצד.")
            return None, "", False
        st.session_state.goal = {"result": result, "n": st.session_state.get("goal", {}).get("n", 0) + 1}

    if "goal" not in st.session_state:
        return None, "", False
    result = st.session_state.goal["result"]
    if result.status == "needs_info":
        st.info("חסר מידע - הוסיפי אותו לטקסט ולחצי שוב:\n\n" + "\n".join(f"- {q}" for q in result.questions))
        return None, "", False
    if result.status != "ok":
        st.error(result.error)
        return None, "", False

    st.success(f"הבנתי את הבקשה (ניסיונות: {result.attempts}). אפשר לתקן בטופס לפני ההרצה.")
    for title, items, show in (("הנחות שעשיתי", result.assumptions, st.info),
                               ("הגבלות שהמערכת לא יודעת לבדוק - התפריט לא מתחשב בהן", result.unsupported, st.warning),
                               ("ייתכן שלא הבנתי נכון", result.warnings, st.warning),
                               ("לא מופיע באף מנה במאגר", unknown_dislikes(result.request), st.info)):
        if items:
            show(f"**{title}:**\n\n" + "\n".join(f"- {item}" for item in items))

    goal_scenario = {"id": f"goal{st.session_state.goal['n']}", "request": result.request}
    request_data = request_editor(goal_scenario)
    # הגבלה שלא נבדקת או חשד לטעות בפענוח - חייבים אישור של אדם
    confirmed = not (result.unsupported or result.warnings) or st.checkbox(
        "קראתי את האזהרות ואני מאשרת להמשיך", key=f"confirm_{goal_scenario['id']}")
    return request_data, "מטרה בשפה חופשית", confirmed


def agent_tab(scenario: dict, model: str, max_steps: int):
    source = st.radio("מקור הבקשה", ["תרחיש מוכן", "שפה חופשית"], horizontal=True)
    if source == "שפה חופשית":
        request_data, title, can_run = goal_input(model)
        if request_data is None:
            return
    else:
        st.markdown(f"**{scenario['title']}** - {scenario['description']}")
        st.caption(f"התוצאה הנכונה: {EXPECT_TEXT[scenario['expect']]}")
        request_data, title, can_run = request_editor(scenario), scenario["title"], True

    with st.expander("👁️ מה המודל מקבל"):
        try:
            st.code(describe_request(MenuRequest.from_dict(request_data)), language=None)
        except ValueError as e:
            st.warning(f"הבקשה לא תקינה, ולכן לא תישלח למודל: {e}")
        st.caption("System prompt:")
        st.code(SYSTEM_PROMPT, language=None)

    if st.button("▶️ הרץ את האג'נט", type="primary", disabled=not can_run):
        log = st.container(border=True)
        with st.spinner("האג'נט עובד..."):
            st.session_state.last_run = run_agent(request_data, model, max_steps, log)
            st.session_state.last_run["title"] = title
    elif "last_run" in st.session_state:
        st.caption(f"הריצה האחרונה ({st.session_state.last_run['title']}):")
        log = st.container(border=True)
        for kind, data in st.session_state.last_run["events"]:
            render_event(log, kind, data)

    if "last_run" in st.session_state:
        show_result(st.session_state.last_run)


def sandbox_tab(scenario: dict):
    st.markdown("הפעלה ידנית של הכלים, **בלי AI**. כאן בודקים שהכלים עצמם נכונים - "
                "בלי לבזבז מכסה, ובלי תלות בהחלטות של המודל.")
    try:
        tools = MenuTools(MenuRequest.from_dict(scenario["request"]))
    except ValueError as e:
        st.error(f"הבקשה של התרחיש לא תקינה: {e}")
        return
    st.caption(f"לפי התרחיש: {scenario['title']} ({scenario['request']['num_guests']} סועדים, "
               f"תקציב {scenario['request']['budget']} ש\"ח)")

    names = {c: [n for n, r in tools.recipes.items() if r["category"] == c] for c in CATEGORIES}
    mains = st.multiselect("2 מנות עיקריות", names["main"], max_selections=2, key="sb_mains")
    c1, c2, c3 = st.columns(3)
    menu = {
        "mains": mains,
        "side": c1.selectbox("תוספת", names["side"], key="sb_side"),
        "vegetable": c2.selectbox("ירקות", names["vegetable"], key="sb_veg"),
        "dessert": c3.selectbox("קינוח", names["dessert"], key="sb_dessert"),
    }

    *tool_columns, judge_column = st.columns(5)
    for col, name in zip(tool_columns, ("calculate_cost", "check_guests", "suggest_swaps", "finalize_menu")):
        if col.button(name, width="stretch"):
            st.session_state.sandbox = (name, getattr(tools, name)(menu))
    if judge_column.button("🔍 מה התשובה הנכונה?", width="stretch"):
        st.session_state.sandbox = ("שופט", find_best_menu(tools))

    if "sandbox" in st.session_state:
        name, result = st.session_state.sandbox
        if name != "שופט":
            st.info(summarize(name, result))
        st.json(result)


def batch_tab(model: str, max_steps: int):
    st.markdown("הרצת כמה תרחישים ברצף, וטבלת ציונים בסוף. **כל תרחיש צורך 5 עד 15 קריאות למודל** - "
                "שימי לב למכסה היומית.")
    st.dataframe(pd.DataFrame([{"תרחיש": s["title"], "מה בודקים": s["description"],
                                "תוצאה נכונה": EXPECT_TEXT[s["expect"]]} for s in SCENARIOS]),
                 width="stretch", hide_index=True)

    selected = st.multiselect("תרחישים להרצה", list(SCENARIO_BY_ID),
                              format_func=lambda sid: SCENARIO_BY_ID[sid]["title"])
    if st.button("▶️ הרץ את התרחישים שנבחרו", type="primary", disabled=not selected):
        rows, progress = [], st.progress(0.0)
        for i, sid in enumerate(selected):
            s = SCENARIO_BY_ID[sid]
            log = st.expander(f"יומן: {s['title']}").container()
            r = run_agent(s["request"], model, max_steps, log)
            verdict = r["verdict"] or ("fail", r.get("error", ""))
            rows.append({
                "תרחיש": s["title"],
                "שופט": f"{VERDICT_ICON[verdict[0]]} {verdict[1]}",
                "צעדים": r.get("steps", 0),
                "זמן (שנ')": round(r.get("duration", 0), 1),
                "עלות": r["approved"]["total_cost"] if r.get("approved") else None,
            })
            progress.progress((i + 1) / len(selected))
        st.session_state.batch = rows

    if "batch" in st.session_state:
        rows = st.session_state.batch
        passed = sum(row["שופט"].startswith("✅") for row in rows)
        st.metric("עברו", f"{passed}/{len(rows)}")
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


# ---------- הדף ----------

load_dotenv()
with st.sidebar:
    st.header("⚙️ הגדרות")
    scenario_id = st.selectbox("תרחיש", list(SCENARIO_BY_ID), format_func=lambda sid: SCENARIO_BY_ID[sid]["title"])
    model = st.text_input("מודל", value=os.getenv("LLM_MODEL") or DEFAULT_MODEL,
                          help="אפשר להחליף מודל אם נגמרה המכסה של הנוכחי")
    max_steps = st.slider("מקסימום צעדים", 5, 30, MAX_STEPS)

st.title("🍽️ בודק האג'נט")
tab_agent, tab_sandbox, tab_batch = st.tabs(["🤖 הרצת האג'נט", "🧪 ארגז חול לכלים", "📋 תרחישי קצה"])
with tab_agent:
    agent_tab(SCENARIO_BY_ID[scenario_id], model, max_steps)
with tab_sandbox:
    sandbox_tab(SCENARIO_BY_ID[scenario_id])
with tab_batch:
    batch_tab(model, max_steps)
