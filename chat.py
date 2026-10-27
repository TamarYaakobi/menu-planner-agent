"""
ממשק צ'אט לאג'נט: כותבים בשפה חופשית, והאג'נט עונה כמו בשיחה.

    python -m streamlit run chat.py

השיחה עוברת בין שלבים:
  goal    - מחכים לבקשה (או למידע שחסר בה)
  confirm - האג'נט הציג איך הבין, ומחכה ל"כן" או לתיקון
  done    - יש תפריט. הודעה נוספת = תיקון לבקשה ("בלי סלמון"), ומתכננים מחדש
"""
import pandas as pd
import streamlit as st
from openai import APIError

from agent import MenuAgent, short_args, summarize
from goal_parser import parse_goal, unknown_dislikes
from llm import ConfigError, create_client, fallback_models
from tools import ALLERGENS, DIETS, MenuRequest, MenuTools

YES = {"כן", "כ", "יאללה", "בטח", "סבבה", "אוקיי", "ok", "yes", "y", "מאשר", "מאשרת", "מעולה", "נכון"}
WELCOME = """היי! אני אג'נט שמתכנן תפריט לארוחה 🍽️

ספרי לי מי מגיע, מה התקציב, ומה כל אחד אוכל או לא אוכל. למשל:

> *אני מארחת 10 איש בשישי, יש לי 150 ש"ח. אחותי טבעונית, הבן שלה אלרגי לאגוזים, ואבא שלי לא אוהב פטריות.*

אני אגיד לך איך הבנתי, ואחרי שתאשרי - אתכנן תפריט שעומד בתקציב, עם רשימת קניות."""

st.set_page_config(page_title="מתכנן תפריט", page_icon="🍽️")
st.markdown("""<style>
.stApp, [data-testid="stSidebar"], [data-testid="stChatInput"] textarea { direction: rtl; text-align: right; }
</style>""", unsafe_allow_html=True)


# ---------- מצב השיחה ----------

def reset():
    st.session_state.messages = [{"role": "assistant", "content": WELCOME}]
    st.session_state.stage = "goal"
    st.session_state.goal_text = ""   # כל מה שהמשתמשת כתבה על הבקשה, כולל תיקונים ותשובות
    st.session_state.goal = None      # ההבנה האחרונה (ParsedGoal)
    st.session_state.model = None     # המודל שעבד - אם היה מעבר לגיבוי, ממשיכים איתו


if "messages" not in st.session_state:
    reset()


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
    lines = [f"**{request.num_guests} סועדים, תקציב {request.budget:g} ש\"ח**"]
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
    return "\n".join(lines)


def understand(text: str):
    """מפענח את כל מה שנכתב עד עכשיו, ועונה: שאלה, הבנה לאישור, או הסבר למה לא."""
    st.session_state.goal_text = (st.session_state.goal_text + "\n" + text).strip()
    try:
        client, default_model = create_client()
        with st.spinner("קוראת את הבקשה..."):
            goal = parse_goal(client, st.session_state.model or default_model, st.session_state.goal_text,
                              fallbacks=fallback_models())
    except ConfigError as e:
        return say(f"❌ {e}")
    except APIError as e:
        return say(f"😕 המודל לא זמין כרגע ({type(e).__name__}). נסי לשלוח שוב בעוד דקה.")
    st.session_state.model = goal.model

    if goal.status == "needs_info":
        return say("כמעט! חסר לי עוד קצת מידע:\n\n" + "\n".join(f"- {q}" for q in goal.questions))
    if goal.status == "off_topic":
        st.session_state.goal_text = ""
        return say("אני יודע רק לתכנן ארוחות 🙂 ספרי לי מי מגיע ומה התקציב.")
    if goal.status != "ok":
        st.session_state.goal_text = ""
        return say(f"לא הצלחתי להבין את הבקשה ({goal.error}). אפשר לנסח אותה מחדש?")

    st.session_state.goal = goal
    st.session_state.stage = "confirm"
    ask = "**להתחיל לתכנן?** אפשר לענות \"כן\", או לכתוב מה לתקן."
    if goal.unsupported or goal.warnings:
        ask = "**שימי לב לאזהרות למעלה.** אם הכל בסדר - \"כן\". אם לא - כתבי מה לתקן."
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
            agent = MenuAgent(client, st.session_state.model or default_model, tools,
                              on_event=on_event, fallbacks=fallback_models())
            success = agent.run()
        except APIError as e:
            status.update(label="המודל לא זמין", state="error", expanded=False)
            return say(f"😕 המודל לא זמין כרגע ({type(e).__name__}). כתבי \"כן\" כדי לנסות שוב.")
        status.update(label=f"תכננתי ב-{agent.steps_used} צעדים", state="complete", expanded=False)

    # החלון החי מוצג רק עכשיו. בהיסטוריה, הצעדים נשמרים בתוך הודעת התשובה (בחלון מתקפל)
    st.session_state.stage = "done"
    if success:
        say(menu_answer(tools.approved_menu, request.budget), events=lines, steps=agent.steps_used,
            shopping_list=tools.approved_menu["shopping_list"])
    else:
        say(failure_answer(agent, request.budget), events=lines, steps=agent.steps_used)


def menu_answer(approved: dict, budget: float) -> str:
    menu = approved["menu"]
    lines = [f"✅ **הנה התפריט** - {approved['total_cost']:.0f} ש\"ח מתוך {budget:g} "
             f"({approved['cost_per_guest']:.0f} ש\"ח לסועד)", "",
             f"- 🍗 **עיקריות:** {menu['mains'][0]}, {menu['mains'][1]}",
             f"- 🥔 **תוספת:** {menu['side']}",
             f"- 🥗 **ירקות:** {menu['vegetable']}",
             f"- 🍰 **קינוח:** {menu['dessert']}"]
    likes = approved.get("likes", [])
    if likes:
        lines.append("")
        for like in likes:
            where = f"יש ב{', '.join(like['dishes'])}" if like["in_menu"] else "לא הצלחתי לשלב בתקציב ובאילוצים"
            lines.append(f"{'❤️' if like['in_menu'] else '🤍'} {like['guest']} אוהב/ת {like['likes']} - {where}")
    lines += ["", "רוצה לשנות משהו? כתבי למשל \"בלי סלמון\" או \"התקציב הוא בעצם 200\"."]
    return "\n".join(lines)


def failure_answer(agent, budget: float) -> str:
    lines = ["😕 **לא מצאתי תפריט שעומד בכל הדרישות.**", "", agent.failure_reason or ""]
    if agent.best_cost:
        cost = agent.best_cost["total_cost"]
        lines += ["", f"התפריט התקין הזול ביותר שמצאתי עולה **{cost:.0f} ש\"ח** ({short_args(agent.best_cost)}).",
                  f"עם תקציב של {int(cost) + 1} ש\"ח זה יסתדר - רוצה שאתכנן ככה? כתבי \"התקציב הוא {int(cost) + 1}\"."]
    return "\n".join(lines)


# ---------- הדף ----------

with st.sidebar:
    st.header("🍽️ מתכנן תפריט")
    if st.button("🔄 שיחה חדשה", width="stretch"):
        reset()
        st.rerun()
    st.caption("האג'נט מבין בקשה בשפה חופשית, מבקש אישור, ומתכנן תפריט בתקציב שמתאים לכל האורחים.")

for message in st.session_state.messages:
    render_message(message)

if text := st.chat_input("כתבי כאן..."):
    st.session_state.messages.append({"role": "user", "content": text})
    render_message(st.session_state.messages[-1])

    stage = st.session_state.stage
    if stage == "confirm" and (text.strip().lower() in YES or text.strip().startswith("כן")):
        plan()
    else:
        # בשלב האישור או אחרי תפריט - הודעה שאינה "כן" היא תיקון לבקשה
        understand(text if stage == "goal" else f"תיקון: {text}")
