"""
ממשק צ'אט לאג'נט: כותבים בשפה חופשית, והאג'נט עונה כמו בשיחה.

    python -m streamlit run chat.py

השיחה עוברת בין שלבים:
  goal    - מחכים לבקשה (או למידע שחסר בה)
  confirm - האג'נט הציג איך הבין, ומחכה ל"כן" או לתיקון
  done    - יש תפריט. אפשר לבקש מתכונים, או לכתוב תיקון ("בלי סלמון") ולתכנן מחדש

השיחות נשמרות בתיקייה conversations (לא עולה ל-Git), ואפשר לחזור לכל אחת מסרגל הצד.
"""
import traceback

import streamlit as st
from openai import APIError

from agent import MenuAgent, short_args, summarize
from chat_logic import (STATE_KEYS, cost_answer, delete_conversation, diners, format_recipe, is_cost_question, is_thanks,
                        is_yes, list_conversations, load_conversation, menu_card_html, new_conversation_id,
                        requested_dishes, save_conversation, shopping_html, steps_html, user_html, wants_recipes)
from hebrew_page import mark_page_hebrew
from goal_parser import parse_goal, unknown_dislikes, unknown_likes
from llm import ConfigError, create_client, daily_quota_message, fallback_models
from tools import ALLERGENS, DIETS, MenuRequest, MenuTools, describe_courses, menu_dishes

WELCOME = """היי! אני אג'נט שמתכנן תפריט לארוחה 🍽️

ספרי לי מי מגיע, מה התקציב, ומה כל אחד אוכל או לא אוכל. למשל:

> *אני מארחת 10 איש בשישי, יש לי בערך 150 ש"ח. אחותי טבעונית, הבן שלה אלרגי לאגוזים, ואבא שלי לא אוהב פטריות.*

אפשר גם לבחור מה יהיה בתפריט - למשל *"מרק, עיקרית אחת ושני קינוחים"*. אם לא תגידי, אתכנן 2 עיקריות, תוספת, סלט וקינוח.

אני אגיד לך איך הבנתי, ואחרי שתאשרי - אתכנן תפריט שעומד בתקציב, עם רשימת קניות ומתכונים."""

st.set_page_config(page_title="מתכנן תפריט", page_icon="🍽️")
mark_page_hebrew()  # אחרת הדפדפן "מתרגם" את העברית ומשבש אותה
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;700&display=swap');
html, body, .stApp, button, textarea, input { font-family: 'Heebo', 'Segoe UI', Arial, sans-serif !important; }
.stApp, [data-testid="stSidebar"] { direction: rtl; }
[data-testid="stChatMessage"], [data-testid="stChatMessageContent"], [data-testid="stMarkdownContainer"],
[data-testid="stChatInput"] textarea, [data-testid="stCaptionContainer"], [data-testid="stAlert"],
[data-testid="stSidebar"] button, [data-testid="stSidebar"] label { direction: rtl; text-align: right; }
[data-testid="stMarkdownContainer"] ul, [data-testid="stMarkdownContainer"] ol { padding-right: 1.2em; padding-left: 0; }
[data-testid="stMarkdownContainer"] li, [data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] h3 { direction: rtl; text-align: right; }
[data-testid="stMarkdownContainer"] blockquote { border-right: 3px solid #F3C9A8; border-left: none;
  padding-right: 1em; padding-left: 0; color: #6b5b50; }
code { direction: ltr; unicode-bidi: embed; }

/* בועות */
[data-testid="stChatMessage"] { background: #ffffff; border-radius: 16px; padding: 0.9rem 1.1rem;
  box-shadow: 0 1px 4px rgba(120, 70, 30, 0.08); margin-bottom: 0.7rem; }
[data-testid="stChatMessage"]:has(.user-msg) { background: #FFE9DA; box-shadow: none; }

/* כותרת */
.app-title { text-align: center; margin: 0.2rem 0 1.2rem; }
.app-title h1 { font-size: 1.9rem; margin: 0; color: #2B2B2B; }
.app-title p { color: #8A6A55; margin: 0.2rem 0 0; }

/* כרטיס התפריט */
.menu-card { border: 1px solid #F3D9C4; border-radius: 14px; padding: 14px 16px; background: #FFFDFB; }
.menu-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.menu-title { font-weight: 700; font-size: 1.15rem; }
.price { background: #E8590C; color: #fff; border-radius: 999px; padding: 2px 14px; font-weight: 700; }
.menu-sub { color: #8A6A55; font-size: 0.9rem; margin: 4px 0 8px; }
.menu-row { display: flex; gap: 12px; padding: 7px 0; border-bottom: 1px dashed #F1E1D3; }
.menu-row:last-of-type { border-bottom: none; }
.menu-row .label { min-width: 92px; color: #A0522D; font-weight: 600; }
.prefs { margin-top: 10px; padding-top: 8px; border-top: 1px solid #F3D9C4; }
.pref { margin: 5px 0; }
.pref .why { display: block; color: #7d7d7d; font-size: 0.88rem; margin-right: 1.7em; }

/* חלונות מתקפלים (details של הדפדפן) */
details.box { margin-top: 10px; border: 1px solid #F0E2D6; border-radius: 12px; padding: 6px 12px; background: #FFFAF5; }
details.box summary { cursor: pointer; font-weight: 600; color: #8A4B22; padding: 2px 0; }
details.box[open] summary { margin-bottom: 6px; }
table.shop { width: 100%; border-collapse: collapse; }
table.shop th { color: #8A6A55; font-weight: 500; font-size: 0.9rem; }
table.shop td, table.shop th { text-align: right; padding: 5px 6px; border-bottom: 1px solid #F3E7DC; }
ul.steps { margin: 0; padding-right: 1.2em; font-size: 0.9rem; color: #555; }
ul.steps li { margin: 3px 0; }
.hint { margin-top: 10px; color: #6b5b50; font-size: 0.93rem; }
</style>""", unsafe_allow_html=True)


# ---------- מצב השיחה ----------

def reset():
    st.session_state.conversation_id = new_conversation_id()
    st.session_state.messages = [{"role": "assistant", "content": WELCOME}]
    st.session_state.stage = "goal"
    st.session_state.goal_text = ""      # הבקשה הראשונה, עם תשובות על מידע שחסר בה
    st.session_state.goal = None         # ההבנה האחרונה (ParsedGoal)
    st.session_state.model = None        # המודל שעבד - אם היה מעבר לגיבוי, ממשיכים איתו
    st.session_state.menu = None         # התפריט האחרון שאושר, עם הבקשה שלו - בשביל מתכונים
    st.session_state.previous_menu = None  # נקודת התחלה לתכנון הבא
    st.session_state.kosher = False      # הגדרה: ארוחה כשרה (בלי בשר וחלב יחד)


def load(conversation_id: str):
    data = load_conversation(conversation_id)
    if data is None:
        st.toast("לא הצלחתי לפתוח את השיחה")
        return
    st.session_state.conversation_id = conversation_id
    for key in STATE_KEYS:
        setattr(st.session_state, key, data.get(key))
    st.session_state.kosher = bool(data.get("kosher"))  # שיחות מלפני שהיה מתג כשרות


def persist():
    save_conversation(st.session_state.conversation_id, {k: st.session_state[k] for k in STATE_KEYS})


if "messages" not in st.session_state:
    reset()
    if st.query_params.get("c"):  # קישור ישיר לשיחה: ?c=<מזהה>
        try:
            load(st.query_params["c"])
        except ValueError:  # מזהה לא תקין
            pass


def say(content: str, **extra):
    """הודעה של האג'נט: נשמרת בהיסטוריה ומוצגת."""
    message = {"role": "assistant", "content": content, **extra}
    st.session_state.messages.append(message)
    render_message(message)


AVATARS = {"assistant": "🍽️", "user": "🙂"}


def render_message(message: dict):
    with st.chat_message(message["role"], avatar=AVATARS[message["role"]]):
        if message["role"] == "user":
            st.markdown(user_html(message["content"]), unsafe_allow_html=True)  # escape - הטקסט של המשתמשת
            return
        st.markdown(message["content"], unsafe_allow_html=message.get("html", False))
        if message.get("shopping_list"):
            st.markdown(shopping_html(message["shopping_list"]), unsafe_allow_html=True)
        if message.get("events"):
            st.markdown(steps_html(message["events"], message["steps"]), unsafe_allow_html=True)


# ---------- הבנת הבקשה ----------

def describe_goal(goal) -> str:
    request = MenuRequest.from_dict(goal.request)
    budget = f"תקציב {request.budget:g} ש\"ח"
    if request.target_budget is not None:
        budget = f"תקציב בערך {request.target_budget:g} ש\"ח (עד {request.budget:g} אם אין ברירה)"
    lines = [f"**{diners(request.num_guests)}, {budget}**", f"🍽️ בתפריט: {describe_courses(request.courses)}"]
    if request.kosher or st.session_state.kosher:
        lines.append("🕍 ארוחה כשרה - בלי בשר וחלב יחד")
    if request.exclude_dishes:
        lines.append(f"🚫 בלי: {', '.join(request.exclude_dishes)}")
    for g in request.guests:
        parts = [f"אלרגיה ל{', '.join(ALLERGENS[a] for a in g.allergies)}" if g.allergies else "",
                 DIETS[g.diet] if g.diet else "",
                 f"לא אוהב/ת {', '.join(g.dislikes)}" if g.dislikes else "",
                 f"אוהב/ת {', '.join(g.likes)}" if g.likes else ""]
        lines.append(f"- **{g.name}:** " + ("; ".join(p for p in parts if p) or "בלי הגבלות"))
    if goal.assumptions:
        lines.append("\n*הנחתי ש:* " + " ".join(goal.assumptions))
    if goal.unsupported:
        lines.append("\n⚠️ **את זה אני לא יודע לבדוק, והתפריט לא יתחשב בזה:** " + ", ".join(goal.unsupported))
    if goal.warnings:
        lines.append("\n⚠️ **ייתכן שלא הבנתי נכון:**\n" + "\n".join(f"- {w}" for w in goal.warnings))
    unknown = unknown_dislikes(goal.request)
    if unknown:
        lines.append(f"\n👍 *{', '.join(unknown)} - אין באף מתכון שלי, אז ממילא לא יהיה בתפריט.*")
    missing_likes = unknown_likes(goal.request)
    if missing_likes:
        lines.append(f"\n🤍 *אין לי מתכונים עם {', '.join(missing_likes)}, אז לא אוכל לשלב את זה בתפריט.*")
    return "\n".join(lines)


def understand(text: str):
    """
    בקשה חדשה - מפענחים את כל מה שנכתב עליה (כולל תשובות לשאלות).
    עדכון לבקשה קיימת - המודל מקבל את הבקשה המובנית + ההודעה הזו בלבד, והקוד מראה מה השתנה.
    """
    previous = st.session_state.goal if st.session_state.goal and st.session_state.goal.status == "ok" else None
    if previous is None:
        st.session_state.goal_text = (st.session_state.goal_text + "\n" + text).strip()
    try:
        client, default_model = create_client()
        with st.spinner("קוראת את הבקשה..."):
            shown = st.session_state.menu["approved"]["menu"] if st.session_state.menu else None
            goal = parse_goal(client, st.session_state.model or default_model,
                              text if previous else st.session_state.goal_text,
                              fallbacks=fallback_models(), previous=previous,
                              menu=menu_dishes(shown) if shown else None)
    except ConfigError as e:
        return say(f"❌ {e}")
    except APIError as e:
        return say(model_error(e, "נסי לשלוח שוב בעוד דקה."))
    st.session_state.model = goal.model or st.session_state.model

    if goal.status == "needs_info":
        return say("כמעט! חסר לי עוד קצת מידע:\n\n" + "\n".join(f"- {q}" for q in goal.questions))
    if goal.status == "off_topic":
        if previous:
            return say("לא הבנתי מה לשנות 🙂 אפשר לכתוב למשל \"בלי סלמון\", \"התקציב 200\", או \"מתכונים\".")
        st.session_state.goal_text = ""
        return say("אני יודע רק לתכנן ארוחות 🙂 ספרי לי מי מגיע ומה התקציב.")
    if goal.status != "ok":
        if not previous:
            st.session_state.goal_text = ""
        return say(f"לא הצלחתי להבין את הבקשה ({goal.error}). אפשר לנסח אותה מחדש?")

    st.session_state.goal = goal
    st.session_state.stage = "confirm"
    ask = "**להתחיל לתכנן?** אפשר לענות \"כן\", או לכתוב מה לתקן."
    if goal.unsupported or goal.warnings:
        ask = "**שימי לב לאזהרות למעלה.** אם הכל בסדר - \"כן\". אם לא - כתבי מה לתקן."
    if previous:
        changed = ("עדכנתי:\n" + "\n".join(f"- {c}" for c in goal.changes)) if goal.changes \
            else "לא מצאתי מה לשנות בבקשה - אם התכוונת למשהו אחר, נסי לנסח אחרת."
        return say(changed + "\n\nעכשיו הבקשה היא:\n\n" + describe_goal(goal) + "\n\n" + ask)
    say("הבנתי כך:\n\n" + describe_goal(goal) + "\n\n" + ask)


def model_error(error: APIError, retry_hint: str) -> str:
    """במכסה יומית "נסי שוב" מטעה - ניסיון חוזר לא יעזור עד שהיא מתחדשת."""
    daily = daily_quota_message(error)
    if daily:
        return f"😕 {daily}"
    return f"😕 המודל לא זמין כרגע ({type(error).__name__}). {retry_hint}"


# ---------- התכנון ----------

def event_line(kind: str, data: dict) -> str | None:
    if kind == "thought":
        return f"💭 {data['text']}"
    if kind == "tool":
        target = f" ({short_args(data['args'])})" if data["args"] and "menu" in data["args"] else ""
        return f"🔧 `{data['name']}`{target} ← " + summarize(data["name"], data["result"]).replace("\n     - ", " • ")
    if kind == "fallback":
        return f"🔁 המודל {data['old']} לא זמין - עובר ל-{data['new']}"
    if kind == "rate_limit":
        return f"⏳ ממתין {data['seconds']:.0f} שניות (מכסת בקשות)"
    if kind in ("nudge", "low_steps"):
        return "↩ " + ("מבקש מהמודל להמשיך" if kind == "nudge" else f"נשארו {data['remaining']} צעדים")
    return None


def plan():
    # כשרות: מהבקשה עצמה ("ארוחה כשרה"), או מההגדרה בסרגל הצד
    request_data = dict(st.session_state.goal.request,
                        kosher=bool(st.session_state.goal.request.get("kosher") or st.session_state.kosher))
    request = MenuRequest.from_dict(request_data)
    tools = MenuTools(request)
    lines: list[str] = []
    with st.chat_message("assistant", avatar=AVATARS["assistant"]):
        status = st.status("מתכנן את התפריט...", expanded=True)

        def on_event(kind, data):
            line = event_line(kind, data)
            if line:
                lines.append(line)
                status.markdown(line)

        try:
            client, default_model = create_client()
            agent = MenuAgent(client, st.session_state.model or default_model, tools, on_event=on_event,
                              fallbacks=fallback_models(), previous_menu=st.session_state.previous_menu)
            success = agent.run()
        except APIError as e:
            status.update(label="המודל לא זמין", state="error", expanded=False)
            return say(model_error(e, "כתבי \"כן\" כדי לנסות שוב."))
        status.update(label=f"תכננתי ב-{agent.steps_used} צעדים", state="complete", expanded=False)

    # החלון החי מוצג רק עכשיו. בהיסטוריה, הצעדים נשמרים בתוך הודעת התשובה (בחלון מתקפל)
    st.session_state.stage = "done"
    if success:
        approved = tools.approved_menu
        st.session_state.menu = {"approved": approved, "request": request_data}
        st.session_state.previous_menu = {"menu": approved["menu"], "total_cost": approved["total_cost"]}
        say(menu_card_html(approved, request_data, unknown_likes(request_data)) + NEXT_STEPS, html=True,
            events=lines, steps=agent.steps_used, shopping_list=approved["shopping_list"])
    else:
        st.session_state.stage = "confirm"  # "כן" = לנסות שוב עם אותה בקשה
        if agent.best_cost:  # בפעם הבאה (למשל אחרי שהתקציב עלה) - מתחילים מהתפריט הזול ביותר שנמצא
            st.session_state.previous_menu = {"menu": agent.best_cost["menu"],
                                              "total_cost": agent.best_cost["total_cost"]}
        say(failure_answer(agent), events=lines, steps=agent.steps_used)


def failure_answer(agent) -> str:
    lines = ["😕 **לא מצאתי תפריט שעומד בכל הדרישות.**", "", agent.failure_reason or ""]
    if agent.best_cost:
        cost = agent.best_cost["total_cost"]
        lines += ["", f"התפריט התקין הזול ביותר שמצאתי עולה **{cost:.0f} ש\"ח** ({short_args(agent.best_cost)})."]
        if cost > agent.best_cost["budget"]:
            lines.append(f"עם תקציב של {int(cost) + 1} ש\"ח זה יסתדר - רוצה שאתכנן ככה? "
                         f"כתבי \"התקציב הוא {int(cost) + 1}\".")
        else:  # התפריט בתקציב - הכישלון לא בגלל הכסף (למשל נגמרו הצעדים), אז אין טעם להציע תקציב
            lines.append("הוא בתוך התקציב - כתבי \"כן\" ואנסה שוב, מהתפריט הזה.")
    return "\n".join(lines)


def show_recipes(text: str):
    saved = st.session_state.menu
    tools = MenuTools(MenuRequest.from_dict(saved["request"]))  # הכמויות - לפי הבקשה שהתפריט תוכנן לה
    cards = [format_recipe(tools.recipe_card(dish)) for dish in requested_dishes(text, saved["approved"]["menu"])]
    say("\n\n---\n\n".join(cards))


NEXT_STEPS = ('<div class="hint">📖 <b>רוצה מתכונים?</b> כתבי "מתכונים", או למשל "מתכון לקינוח". '
              'אפשר גם לשנות משהו - "בלי סלמון", "התקציב בעצם 200", "תוסיף מנה ראשונה" - או לשאול "כמה זה יוצא לסועד?"</div>')


# ---------- הדף ----------

def delete(conversation_id: str):
    delete_conversation(conversation_id)
    if conversation_id == st.session_state.conversation_id:
        reset()


# הכפתורים פועלים דרך on_click: הוא רץ לפני שהדף מצויר, ולכן מותר לו לשנות גם את מתג הכשרות
with st.sidebar:
    st.header("🍽️ מתכנן תפריט")
    st.button("➕ שיחה חדשה", width="stretch", on_click=reset)
    st.toggle("🕍 ארוחה כשרה", key="kosher", on_change=persist,
              help="בלי מנה בשרית ומנה חלבית באותה ארוחה. פרווה ודגים - מותר עם הכל")
    conversations = list_conversations()
    if conversations:
        st.caption("שיחות קודמות")
    for conversation in conversations:
        current = conversation["id"] == st.session_state.conversation_id
        open_col, delete_col = st.columns([5, 1])
        open_col.button(conversation["title"], key=f"open_{conversation['id']}", width="stretch",
                        type="primary" if current else "secondary", help=conversation["updated"].replace("T", " "),
                        on_click=load, args=(conversation["id"],))
        delete_col.button("🗑", key=f"delete_{conversation['id']}", help="מחיקת השיחה",
                          on_click=delete, args=(conversation["id"],))

st.markdown('<div class="app-title"><h1>🍽️ מתכנן תפריט</h1>'
            '<p>מי מגיע, מה התקציב ומה כל אחד אוכל - ואני אתכנן ארוחה שמתאימה לכולם</p></div>',
            unsafe_allow_html=True)

for message in st.session_state.messages:
    render_message(message)

if text := st.chat_input("כתבי כאן..."):
    st.session_state.messages.append({"role": "user", "content": text})
    render_message(st.session_state.messages[-1])

    stage = st.session_state.stage
    try:
        if stage == "confirm" and is_yes(text):
            plan()
        elif st.session_state.menu and wants_recipes(text):
            show_recipes(text)
        elif st.session_state.menu and is_cost_question(text):
            say(cost_answer(st.session_state.menu))
        elif stage == "done" and is_thanks(text):
            say("בשמחה! 😊 בתיאבון. אם תרצי מתכונים או שינוי בתפריט - רק תכתבי.")
        else:
            understand(text)  # בקשה חדשה, תשובה לשאלה, או תיקון לבקשה קיימת
    except Exception:  # רשת ביטחון אחרונה: הודעה ידידותית במקום traceback. הפרטים - בטרמינל
        traceback.print_exc()
        say("😕 משהו השתבש אצלי - תקלה פנימית, לא בגללך. אפשר לנסות לנסח שוב?")
    persist()
