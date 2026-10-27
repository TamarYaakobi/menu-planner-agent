"""
הלוגיקה של הצ'אט, בלי תצוגה - כדי שאפשר יהיה לבדוק אותה ישירות.
chat.py מציג; כאן מחליטים ומנסחים.
"""
import json
import math
import os
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from goal_parser import ParsedGoal

YES = {"כן", "כ", "יאללה", "בטח", "סבבה", "אוקיי", "אוקי", "ok", "okay", "yes", "y", "sure", "go", "מאשר", "מאשרת",
       "מעולה", "נכון", "אחלה", "מצוין", "מצויין", "טוב", "בסדר", "קדימה", "סגור", "יופי", "בדיוק", "נהדר",
       "תתחיל", "תתחילי", "תכנן", "👍", "✅"}
# מילים שמותר שיופיעו לצד "כן" בלי להפוך אותו לתיקון: "כן תודה", "אחלה, יאללה תתחיל"
YES_FILLERS = {"תודה", "בבקשה", "אז", "נו", "לגמרי", "ממש", "הכל", "זה", "את", "התפריט", "לתכנן"}


def is_yes(text: str) -> bool:
    """
    "כן" / "אחלה" / "כן, תודה!" = אישור. "כן אבל התקציב 200" = תיקון -
    כל מילה שאינה אישור או מילת נימוס הופכת את ההודעה לתיקון, כדי שלא יתעלמו ממנה.
    """
    words = re.findall(r"[\w👍✅]+", text.lower())
    return any(w in YES for w in words) and all(w in YES or w in YES_FILLERS for w in words)


THANKS = {"תודה", "רבה", "תודות", "מעולה", "אחלה", "יופי", "מושלם", "סבבה", "thanks", "thank", "you", "🙏", "❤️", "😊"}


def is_thanks(text: str) -> bool:
    """"תודה רבה!" אחרי התפריט - לא בקשה לשנות משהו."""
    words = re.findall(r"[\w🙏❤️😊]+", text.lower())
    return bool(words) and all(w in THANKS for w in words)


# ---------- שאלות על התפריט ----------

# שאלה ("כמה יעלה לסועד?", "מה המחיר?") - לא בקשה לשנות ("תוריד את העלות" הוא תיקון, לא שאלה)
_COST_QUESTION = re.compile(r"(כמה|מה ה(מחיר|עלות)|how much|what.*cost)", re.IGNORECASE)
_COST_WORDS = re.compile(r"(עול|יעל|עלה|עלות|מחיר|לסועד|לאדם|cost|price|per (person|guest|head))", re.IGNORECASE)


def is_cost_question(text: str) -> bool:
    return bool(_COST_QUESTION.search(text) and _COST_WORDS.search(text))


def cost_answer(saved_menu: dict) -> str:
    """פירוט עלות התפריט שאושר - מחושב בקוד, מאותם מחירים שהאג'נט עבד איתם."""
    from tools import MenuRequest, MenuTools  # כאן ולא למעלה: chat_logic נטען גם בלי הכלים
    tools = MenuTools(MenuRequest.from_dict(saved_menu["request"]))
    cost = tools.calculate_cost(saved_menu["approved"]["menu"])
    guests = tools.request.num_guests
    lines = [f"💰 **{cost['cost_per_guest']:.2f} ש\"ח לסועד** ({cost['total_cost']:.2f} ש\"ח ל-{guests} סועדים)", ""]
    lines += [f"- {d['name']}: {d['cost'] / guests:.2f} ש\"ח" for d in cost["dishes"]]
    packages = sum(item["packages_cost"] for item in saved_menu["approved"]["shopping_list"])
    lines += ["", f"*זו עלות המצרכים שנכנסים לארוחה. בקופה, באריזות שלמות, זה יוצא בערך {packages:.0f} ש\"ח - "
                  f"וחלק מהמצרכים (שמן, תבלינים) יישארו בבית.*"]
    return "\n".join(lines)


# ---------- היסטוריית שיחות ----------

# מה נשמר מכל שיחה - מספיק כדי להמשיך בדיוק מאיפה שהפסקנו
STATE_KEYS = ("messages", "stage", "goal_text", "goal", "model", "menu", "previous_menu")


def history_dir() -> Path:
    """נקרא בכל פעם (לא פעם אחת בטעינה), כדי שבדיקות יוכלו להפנות לתיקייה זמנית."""
    return Path(os.getenv("CHAT_HISTORY_DIR") or Path(__file__).parent / "conversations")


def new_conversation_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6]


def _path(conversation_id: str) -> Path:
    if not re.fullmatch(r"[\w-]+", conversation_id):  # שם קובץ בלבד - בלי "../"
        raise ValueError(f"מזהה שיחה לא תקין: {conversation_id!r}")
    return history_dir() / f"{conversation_id}.json"


def save_conversation(conversation_id: str, state: dict):
    """שומר שיחה לקובץ. שיחה שעוד אין בה הודעה של המשתמשת - לא נשמרת."""
    first = next((m["content"] for m in state["messages"] if m["role"] == "user"), None)
    if first is None:
        return
    data = {key: state.get(key) for key in STATE_KEYS}
    data["goal"] = asdict(state["goal"]) if state.get("goal") else None
    data.update(id=conversation_id, title=first.strip().replace("\n", " ")[:50],
                updated=datetime.now().isoformat(timespec="seconds"))
    path = _path(conversation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def load_conversation(conversation_id: str) -> dict | None:
    """None אם אין כזו שיחה או שהקובץ פגום. מזהה לא תקין (כמו "../") זורק ValueError - לא קוראים שום קובץ."""
    path = _path(conversation_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # קובץ חסר או פגום
        return None
    data["goal"] = ParsedGoal(**data["goal"]) if data.get("goal") else None
    return data


def list_conversations() -> list[dict]:
    """[{id, title, updated}], מהחדשה לישנה. קובץ פגום מדולג - הוא לא אמור להפיל את כל הרשימה."""
    conversations = []
    for path in history_dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            conversations.append({"id": data["id"], "title": data["title"], "updated": data["updated"]})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(conversations, key=lambda c: c["updated"], reverse=True)


def delete_conversation(conversation_id: str):
    _path(conversation_id).unlink(missing_ok=True)


# ---------- מתכונים ----------

# "מתכון לקינוח", "את המתכון של הסלט" - מילה שמצביעה על מקום בתפריט
SLOT_WORDS = {"עיקרית": "mains", "עיקריות": "mains", "תוספת": "side", "סלט": "vegetable", "ירקות": "vegetable",
              "קינוח": "dessert"}


def wants_recipes(text: str) -> bool:
    return re.search(r"מתכונ|מתכון|recipe|איך מכינים|איך להכין", text, re.IGNORECASE) is not None


def requested_dishes(text: str, menu: dict) -> list[str]:
    """
    אילו מנות מהתפריט ביקשו: "מתכון לשניצל" -> שניצל, "מתכון לקינוח" -> הקינוח.
    בלי התאמה - כל התפריט ("תביא את המתכונים").
    """
    dishes = menu["mains"] + [menu["side"], menu["vegetable"], menu["dessert"]]
    # "לשניצל" -> לשניצל / שניצל / ניצל: לא יודעים כמה אותיות הן קידומת, אז מנסים את כולן.
    # גרש הוא חלק מהמילה ("מג'דרה")
    words = {variant for w in re.findall(r"[\w'׳]+", text) for variant in _without_prefixes(w)}
    dish_words = {w for w in words if w not in SLOT_WORDS and len(w) >= 3}
    chosen = [d for d in dishes if dish_words & set(d.split())]
    for word, slot in SLOT_WORDS.items():
        # "סלט" מצביע על מנת הירקות - אלא אם כבר נבחרה מנה ספציפית שזה חלק משמה ("סלט הפירות")
        if word in words and not any(word in d.split() for d in chosen):
            chosen += menu[slot] if slot == "mains" else [menu[slot]]
    return list(dict.fromkeys(chosen)) or dishes  # בלי כפילויות


def _without_prefixes(word: str) -> list[str]:
    variants = [word]
    for i in (1, 2):
        if len(word) - i >= 3 and all(c in "ולהבמש" for c in word[:i]):
            variants.append(word[i:])
    return variants


def format_amount(amount: float, unit: str) -> str:
    """כמויות שנוח לקנות ולבשל איתן: "1.8 ק"ג", "250 גרם", "1.5 יח'"."""
    if unit in ("g", "ml"):
        big, small = ('ק"ג', "גרם") if unit == "g" else ("ליטר", 'מ"ל')
        if amount >= 1000:
            return f"{_nice(amount / 1000)} {big}"
        rounded = round(amount / 5) * 5 if amount >= 50 else round(amount)
        return f"{_nice(rounded)} {small}" if rounded else "קורט"
    return f"{_nice(math.ceil(amount * 2) / 2)} יח'"  # יחידות - בחצאים, כלפי מעלה


def _nice(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def format_recipe(card: dict) -> str:
    portion = " (60% מנה לסועד, כי יש שתי עיקריות)" if card["main_portion"] else ""
    lines = [f"### 🍳 {card['name']}",
             f"⏱️ {card['time_minutes']} דקות · ל-{card['num_guests']} סועדים{portion}", "",
             "**מצרכים:**"]
    lines += [f"- {format_amount(i['amount'], i['unit'])} {i['name']}" for i in card["ingredients"]]
    lines += ["", "**הכנה:**"] + [f"{n}. {step}" for n, step in enumerate(card["steps"], 1)]
    return "\n".join(lines)
