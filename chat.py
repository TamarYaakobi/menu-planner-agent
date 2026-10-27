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

import pandas as pd
import streamlit as st
from openai import APIError

from agent import MenuAgent, short_args, summarize
from chat_logic import (STATE_KEYS, cost_answer, delete_conversation, format_recipe, is_cost_question, is_thanks,
                        is_yes, list_conversations, load_conversation, new_conversation_id, requested_dishes,
                        save_conversation, wants_recipes)
from goal_parser import parse_goal, unknown_dislikes, unknown_likes
from llm import ConfigError, create_client, fallback_models
from tools import ALLERGENS, DIETS, MenuRequest, MenuTools

WELCOME = """היי! אני אג'נט שמתכנן תפריט לארוחה 🍽️

ספרי לי מי מגיע, מה התקציב, ומה כל אחד אוכל או לא אוכל. למשל:

> *אני מארחת 10 איש בשישי, יש לי בערך 150 ש"ח. אחותי טבעונית, הבן שלה אלרגי לאגוזים, ואבא שלי לא אוהב פטריות.*

אני אגיד לך איך הבנתי, ואחרי שתאשרי - אתכנן תפריט שעומד בתקציב, עם רשימת קניות ומתכונים."""

st.set_page_config(page_title="מתכנן תפריט", page_icon="🍽️")
st.markdown("""<style>
.stApp, [data-testid="stSidebar"] { direction: rtl; }
[data-testid="stChatMessage"], [data-testid="stChatMessageContent"], [data-testid="stMarkdownContainer"],
[data-testid="stChatInput"] textarea, [data-testid="stExpander"], [data-testid="stCaptionContainer"],
[data-testid="stAlert"], [data-testid="stSidebar"] button { direction: rtl; text-align: right; }
[data-testid="stMarkdownContainer"] ul, [data-testid="stMarkdownContainer"] ol { padding-right: 1.2em; padding-left: 0; }
[data-testid="stMarkdownContainer"] li, [data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] h3 { direction: rtl; text-align: right; }
[data-testid="stMarkdownContainer"] blockquote { border-right: 3px solid #ccc; border-left: none;
  padding-right: 1em; padding-left: 0; }
code { direction: ltr; unicode-bidi: embed; }
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


def load(conversation_id: str):
    data = load_conversation(conversation_id)
    if data is None:
        st.toast("לא הצלחתי לפתוח את השיחה")
        return
    st.session_state.conversation_id = conversation_id
    for key in STATE_KEYS:
        setattr(st.session_state, key, data.get(key))


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


def render_message(message: dict):
    with st.chat_message(message["role"]):
        if message.get("events"):
            with st.expander(f"🔍 איך הגעתי לזה ({message['steps']} צעדים)"):
                for line in message["events"]:
                    st.markdown(line)
        st.markdown(message["content"])
        if message.get("shopping_list"):
            with st.expander("🛒 רשימת קניות"):
                st.dataframe(pd.DataFrame([{
                    "מוצר": i["item"], "צריך": f"{i['needed']:g} {i['unit']}",
                    "לקנות": f"{i['packages']} × {i['package_size']:g} {i['unit']}"} for i in message["shopping_list"]]),
                    hide_index=True, width="stretch")


# ---------- הבנת הבקשה ----------

def describe_goal(goal) -> str:
    request = MenuRequest.from_dict(goal.request)
    budget = f"תקציב {request.budget:g} ש\"ח"
    if request.target_budget is not None:
        budget = f"תקציב בערך {request.target_budget:g} ש\"ח (עד {request.budget:g} אם אין ברירה)"
    lines = [f"**{request.num_guests} סועדים, {budget}**"]
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
        lines.append(f"\n*לא מצאתי במתכונים שלי:* {', '.join(unknown)}")
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
            goal = parse_goal(client, st.session_state.model or default_model,
                              text if previous else st.session_state.goal_text,
                              fallbacks=fallback_models(), previous=previous)
    except ConfigError as e:
        return say(f"❌ {e}")
    except APIError as e:
        return say(f"😕 המודל לא זמין כרגע ({type(e).__name__}). נסי לשלוח שוב בעוד דקה.")
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
    request = MenuRequest.from_dict(st.session_state.goal.request)
    tools = MenuTools(request)
    lines: list[str] = []
    with st.chat_message("assistant"):
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
            return say(f"😕 המודל לא זמין כרגע ({type(e).__name__}). כתבי \"כן\" כדי לנסות שוב.")
        status.update(label=f"תכננתי ב-{agent.steps_used} צעדים", state="complete", expanded=False)

    # החלון החי מוצג רק עכשיו. בהיסטוריה, הצעדים נשמרים בתוך הודעת התשובה (בחלון מתקפל)
    st.session_state.stage = "done"
    if success:
        approved = tools.approved_menu
        st.session_state.menu = {"approved": approved, "request": st.session_state.goal.request}
        st.session_state.previous_menu = {"menu": approved["menu"], "total_cost": approved["total_cost"]}
        say(menu_answer(approved, request), events=lines, steps=agent.steps_used,
            shopping_list=approved["shopping_list"])
    else:
        if agent.best_cost:  # בפעם הבאה (למשל אחרי שהתקציב עלה) - מתחילים מהתפריט הזול ביותר שנמצא
            st.session_state.previous_menu = {"menu": agent.best_cost["menu"],
                                              "total_cost": agent.best_cost["total_cost"]}
        say(failure_answer(agent), events=lines, steps=agent.steps_used)


def menu_answer(approved: dict, request) -> str:
    menu = approved["menu"]
    cost = approved["total_cost"]
    if request.target_budget is not None:
        budget = (f"ביעד של {request.target_budget:g}" if cost <= request.target_budget
                  else f"מעל היעד של {request.target_budget:g}, אבל בתוך ה-{request.budget:g}")
    else:
        budget = f"מתוך {request.budget:g}"
    lines = [f"✅ **הנה התפריט** - {cost:.0f} ש\"ח {budget} ({approved['cost_per_guest']:.0f} ש\"ח לסועד)", "",
             f"- 🍗 **עיקריות:** {menu['mains'][0]}, {menu['mains'][1]}",
             f"- 🥔 **תוספת:** {menu['side']}",
             f"- 🥗 **ירקות:** {menu['vegetable']}",
             f"- 🍰 **קינוח:** {menu['dessert']}"]
    likes = approved.get("likes", [])
    if likes:
        lines.append("")
        for like in likes:
            if like["in_menu"]:
                where = f"יש ב{', '.join(like['dishes'])}"
            elif like["likes"] in unknown_likes(st.session_state.goal.request):
                where = "אין לי מתכון עם זה"
            else:
                where = "לא הצלחתי לשלב בתקציב ובאילוצים"
            lines.append(f"{'❤️' if like['in_menu'] else '🤍'} {like['guest']} אוהב/ת {like['likes']} - {where}")
    lines += ["", "📖 **רוצה מתכונים?** כתבי \"מתכונים\", או למשל \"מתכון לקינוח\".",
              "אפשר גם לשנות משהו - למשל \"בלי סלמון\" או \"התקציב הוא בעצם 200\"."]
    return "\n".join(lines)


def failure_answer(agent) -> str:
    lines = ["😕 **לא מצאתי תפריט שעומד בכל הדרישות.**", "", agent.failure_reason or ""]
    if agent.best_cost:
        cost = agent.best_cost["total_cost"]
        lines += ["", f"התפריט התקין הזול ביותר שמצאתי עולה **{cost:.0f} ש\"ח** ({short_args(agent.best_cost)}).",
                  f"עם תקציב של {int(cost) + 1} ש\"ח זה יסתדר - רוצה שאתכנן ככה? כתבי \"התקציב הוא {int(cost) + 1}\"."]
    return "\n".join(lines)


def show_recipes(text: str):
    saved = st.session_state.menu
    tools = MenuTools(MenuRequest.from_dict(saved["request"]))  # הכמויות - לפי הבקשה שהתפריט תוכנן לה
    cards = [format_recipe(tools.recipe_card(dish)) for dish in requested_dishes(text, saved["approved"]["menu"])]
    say("\n\n---\n\n".join(cards))


# ---------- הדף ----------

with st.sidebar:
    st.header("🍽️ מתכנן תפריט")
    if st.button("➕ שיחה חדשה", width="stretch"):
        reset()
        st.rerun()
    conversations = list_conversations()
    if conversations:
        st.caption("שיחות קודמות")
    for conversation in conversations:
        current = conversation["id"] == st.session_state.conversation_id
        open_col, delete_col = st.columns([5, 1])
        if open_col.button(conversation["title"], key=f"open_{conversation['id']}", width="stretch",
                           type="primary" if current else "secondary", help=conversation["updated"].replace("T", " ")):
            load(conversation["id"])
            st.rerun()
        if delete_col.button("🗑", key=f"delete_{conversation['id']}", help="מחיקת השיחה"):
            delete_conversation(conversation["id"])
            if current:
                reset()
            st.rerun()

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
