"""
האג'נט: מתכנן תפריט לארוחה בתקציב, שמתחשב באלרגיות ובהעדפות של האורחים.

    python agent.py                                  # בקשת ברירת המחדל
    python agent.py examples/impossible_budget.json  # בקשה אחרת

איך זה עובד:
  1. המודל מקבל מטרה (אורחים, תקציב), כללים ורשימת כלים.
  2. בכל צעד המודל מחליט איזה כלי להפעיל. הקוד מריץ אותו ומחזיר את התוצאה.
  3. חוזרים על זה עד שאחד מתנאי הסיום מתקיים:
       הצלחה - finalize_menu אישר את התפריט (נבדק בקוד, לא לפי דברי המודל)
       כישלון - המודל קרא ל-report_failure, או שנגמרו הצעדים.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Callable

from openai import APIError, RateLimitError

from llm import ConfigError, create_client
from tools import ALLERGENS, CATEGORIES, DIETS, DataError, MenuRequest, MenuTools, load_json

MAX_STEPS = 15         # מגבלת קריאות למודל - מונעת לולאה אינסופית ועלות לא צפויה
MAX_NUDGES = 1         # כמה פעמים להזכיר למודל להמשיך אם עצר בלי לסיים
LOW_STEPS_WARNING = 3  # כשנשארים כך צעדים, מזהירים את המודל כדי שיחליט אם לוותר
RATE_LIMIT_MAX_WAIT = 60  # שניות. המתנה ארוכה יותר = מכסה יומית, ואין טעם לחכות
RATE_LIMIT_RETRIES = 5
DEFAULT_REQUEST = Path(__file__).parent / "examples" / "friday_dinner.json"

SYSTEM_PROMPT = """אתה אג'נט שמתכנן תפריט לארוחה משפחתית.
המטרה: להרכיב תפריט שעומד בתקציב ומתאים לכל האורחים, ולקבל עליו אישור מהכלי finalize_menu.

מבנה התפריט: 2 מנות עיקריות שונות (mains), תוספת אחת (side), מנת ירקות אחת (vegetable) וקינוח אחד (dessert).
מותר לבחור רק מנות שהופיעו בתוצאות של search_recipes, בדיוק בשם שהופיע שם.

כללי האורחים:
- אילוצי חובה (אלרגיה, צמחוני/טבעוני): לכל אורח חייבת להיות לפחות מנה עיקרית אחת שמותר לו לאכול.
  התוספת, הירקות והקינוח חייבים להתאים לכל האורחים.
- העדפות (מרכיבים שאורח לא אוהב): לכל אורח צריכה להיות מנה עיקרית שמותר לו לאכול ושאין בה מרכיב שהוא לא אוהב,
  ולכל היותר מנה אחת מהשאר עם מרכיב שהוא לא אוהב.

איך לעבוד:
- אתה מחליט באיזה סדר להשתמש בכלים ומה לבדוק.
- מספר הצעדים מוגבל. בצעד אחד אפשר להפעיל כמה כלים במקביל - למשל לחפש בכל הקטגוריות בבת אחת.
- אל תחשב מחירים ואל תבדוק אילוצים בעצמך - בשביל זה יש כלים, והם מדויקים.
- לפני כל קריאה לכלי, כתוב משפט קצר אחד: מה אתה עושה ולמה.
- אם כלי מחזיר בעיה (חריגה מתקציב, הפרת אילוץ, מחיר חסר, שגיאה) - שנה את התפריט בהתאם ובדוק שוב.
  suggest_swaps מראה לכל מנה אילו מנות אפשר לשים במקומה בלי לפגוע באורחים, ומה תהיה העלות אחרי ההחלפה -
  השתמש בו כדי להחליט מה להחליף, במקום לנחש.
  ההחלפות שהוא מציע כבר נבדקו מול כל האורחים, ו-new_total הוא העלות אחרי ההחלפה - אין צורך לבדוק אותן שוב.
  החלפות בתוספת, בירקות ובקינוח לא משפיעות זו על זו, אז אפשר לבצע כמה מהן יחד ולבדוק פעם אחת.
- המשימה מסתיימת בהצלחה רק כש-finalize_menu מחזיר approved=true.
- report_failure רק אחרי ש-suggest_swaps הראה שאין עוד החלפה שמוזילה את התפריט, ועדיין אין פתרון.
  במקרה כזה, ציין בהסבר את התפריט הזול ביותר שהגעת אליו ואת העלות שלו.
"""

_MENU_SCHEMA = {
    "type": "object",
    "description": "התפריט המלא",
    "properties": {
        "mains": {"type": "array", "items": {"type": "string"},
                  "description": "בדיוק 2 מנות עיקריות שונות"},
        "side": {"type": "string", "description": "תוספת אחת"},
        "vegetable": {"type": "string", "description": "מנת ירקות אחת"},
        "dessert": {"type": "string", "description": "קינוח אחד"},
    },
    "required": ["mains", "side", "vegetable", "dessert"],
}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


# התיאורים האלה הם כל מה שהמודל יודע על הכלים - לכן הם מפורטים
TOOL_SCHEMAS = [
    _tool("search_recipes",
          "מחפש מנות במאגר המתכונים לפי קטגוריה, עם סינון אופציונלי. מחזיר שמות, אלרגנים ומרכיבים (בלי מחירים).",
          {
              "category": {"type": "string", "enum": list(CATEGORIES)},
              "exclude_allergens": {"type": "array", "items": {"type": "string", "enum": list(ALLERGENS)},
                                    "description": "להוציא מנות שמכילות אחד מהאלרגנים האלה"},
              "diet": {"type": "string", "enum": list(DIETS),
                       "description": "להחזיר רק מנות שמתאימות לתזונה הזו"},
              "exclude_ingredients": {"type": "array", "items": {"type": "string"},
                                      "description": "להוציא מנות שמכילות מרכיבים אלה (התאמה חלקית של שם)"},
          },
          ["category"]),
    _tool("calculate_cost",
          "מחשב את עלות התפריט לפי מחירי המרכיבים בקטלוג ומספר הסועדים. "
          "מחזיר עלות כוללת, האם בתקציב, ועלות כל מנה מהיקרה לזולה. מדווח על מרכיבים בלי מחיר.",
          {"menu": _MENU_SCHEMA}, ["menu"]),
    _tool("check_guests",
          "בודק את התפריט מול האילוצים וההעדפות של כל האורחים. מחזיר רשימת הפרות "
          "(type=hard לאילוץ חובה, type=soft להעדפה).",
          {"menu": _MENU_SCHEMA}, ["menu"]),
    _tool("suggest_swaps",
          "לכל מנה בתפריט: עד 3 מנות מאותה קטגוריה שאפשר לשים במקומה כך שהתפריט יתאים לכל האורחים, "
          "ממוינות מהזולה, עם העלות החדשה והחיסכון. מחזיר גם את ההחלפה המשתלמת ביותר (best_swap). "
          "אם אין החלפה בודדת שמוזילה - מחזיר החלפה של שתי מנות יחד (best_double_swap), או הודעה שאין. "
          "מתאים כשחורגים מהתקציב או כשיש הפרה של אילוץ.",
          {"menu": _MENU_SCHEMA}, ["menu"]),
    _tool("finalize_menu",
          "מאשר את התפריט הסופי. בודק מחדש תקציב, מחירים ואורחים, ואם הכל תקין מחזיר approved=true "
          "ורשימת קניות. אם לא - מחזיר approved=false עם הסיבות.",
          {"menu": _MENU_SCHEMA}, ["menu"]),
    _tool("report_failure",
          "לקרוא רק אם אין שום תפריט שעומד בכל הכללים, אחרי שבדקת חלופות. מסיים את המשימה בכישלון.",
          {"reason": {"type": "string", "description": "למה אי אפשר, ומה הכי קרוב שהגעת"}},
          ["reason"]),
]

# רק הכלים האלה מותר להפעיל ב-MenuTools - המודל לא יכול לקרוא לשום מתודה אחרת
MENU_TOOLS = {"search_recipes", "calculate_cost", "check_guests", "suggest_swaps", "finalize_menu"}


def describe_request(request: MenuRequest) -> str:
    """המטרה, כטקסט שנשלח למודל."""
    lines = [f"מספר סועדים: {request.num_guests}", f"תקציב: {request.budget:g} ש\"ח"]
    if request.guests:
        lines.append("אורחים עם אילוצים:")
    for g in request.guests:
        parts = []
        if g.allergies:
            parts.append("אלרגיה: " + ", ".join(f"{ALLERGENS[a]} ({a})" for a in g.allergies))
        if g.diet:
            parts.append(f"תזונה: {DIETS[g.diet]} ({g.diet})")
        if g.dislikes:
            parts.append("לא אוהב/ת: " + ", ".join(g.dislikes))
        lines.append(f"- {g.name}: " + "; ".join(parts or ["אין הגבלות"]))
    lines.append("\nתכנן את התפריט.")
    return "\n".join(lines)


class MenuAgent:
    """
    on_event מקבל כל אירוע בזמן אמת: (סוג, נתונים). בטרמינל זה print_event,
    ובממשק הבדיקה זו פונקציה שמציגה את האירוע בדפדפן. האג'נט עצמו לא יודע מי מציג.
    """

    def __init__(self, client, model: str, tools: MenuTools, max_steps: int = MAX_STEPS,
                 on_event: Callable[[str, dict], None] | None = None):
        self.client = client
        self.model = model
        self.tools = tools
        self.max_steps = max_steps
        self.on_event = on_event or print_event
        self.steps_used = 0
        self.failure_reason: str | None = None
        self.best_cost: dict | None = None  # התפריט התקין הזול ביותר שנבדק - לבדיקת ויתור ולדיווח

    def _emit(self, kind: str, **data):
        self.on_event(kind, data)

    def run(self) -> bool:
        """מריץ את הלולאה. מחזיר True אם התפריט אושר."""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": describe_request(self.tools.request)
             + f"\nיש לך עד {self.max_steps} צעדים."},
        ]
        nudges = 0

        for step in range(1, self.max_steps + 1):
            self.steps_used = step
            self._emit("step", step=step, max_steps=self.max_steps)
            message = self._ask_model(messages)
            # מחזירים למודל את ההודעה שלו כמו שהיא, כדי שיזכור מה כבר עשה
            messages.append(message.model_dump(exclude_none=True))

            if message.content:
                self._emit("thought", text=message.content.strip())

            if not message.tool_calls:
                # המודל עצר בלי לסיים ובלי להודיע על כישלון - מזכירים לו פעם אחת
                if nudges < MAX_NUDGES:
                    nudges += 1
                    self._emit("nudge")
                    messages.append({"role": "user", "content":
                                     "התפריט עדיין לא אושר. המשך לעבוד עם הכלים עד ש-finalize_menu מאשר, "
                                     "או קרא ל-report_failure אם אין פתרון."})
                    continue
                self.failure_reason = "המודל הפסיק לעבוד בלי תפריט מאושר"
                return False

            for call in message.tool_calls:
                result = self._execute(call.function.name, call.function.arguments)
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(result, ensure_ascii=False)})

            # תנאי הסיום נבדקים בקוד, אחרי שהכלים רצו
            if self.tools.approved_menu:
                return True
            if self.failure_reason:
                return False

            remaining = self.max_steps - step
            if remaining == LOW_STEPS_WARNING:
                self._emit("low_steps", remaining=remaining)
                messages.append({"role": "user", "content":
                                 f"נשארו לך רק {remaining} צעדים. אם יש תפריט תקין - אשר אותו עם finalize_menu. "
                                 "אם התפריט הזול ביותר שמצאת עדיין לא עומד בכללים, קרא ל-report_failure."})

        self.failure_reason = f"הגעתי למגבלה של {self.max_steps} צעדים בלי תפריט מאושר"
        return False

    def _ask_model(self, messages: list):
        """שולח את השיחה למודל. בחריגה ממכסה לדקה - מחכה וממשיך; במכסה יומית - זורק הלאה."""
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model, messages=messages, tools=TOOL_SCHEMAS)
                return response.choices[0].message
            except RateLimitError as e:
                wait = _retry_delay(e)
                if wait is None or wait > RATE_LIMIT_MAX_WAIT or attempt == RATE_LIMIT_RETRIES:
                    raise
                self._emit("rate_limit", seconds=wait)
                time.sleep(wait + 1)

    def _execute(self, name: str, raw_args: str) -> dict:
        """מריץ כלי אחד. כל בעיה חוזרת למודל כ-error, כדי שיוכל לתקן."""
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            args = None
        if not isinstance(args, dict):
            result = {"error": "הפרמטרים צריכים להיות אובייקט JSON תקין"}
            self._emit("tool", name=name, args=None, result=result)
            return result

        if name == "report_failure":
            result = self._check_give_up(args)
        elif name not in MENU_TOOLS:
            result = {"error": f"אין כלי בשם '{name}'"}
        else:
            try:
                result = getattr(self.tools, name)(**args)
            except TypeError as e:  # פרמטר חסר או מיותר
                result = {"error": f"פרמטרים שגויים לכלי {name}: {e}"}

        # שני כלים מחשבים מחיר של תפריט שלם - שניהם נספרים כ"תפריט שנבדק"
        if name == "calculate_cost" and result.get("complete"):
            self._track_best(args["menu"], result["total_cost"])
        elif name == "suggest_swaps" and result.get("current_complete"):
            self._track_best(args["menu"], result["current_total"])
        self._emit("tool", name=name, args=args, result=result)
        return result

    def _track_best(self, menu: dict, total: float):
        """שומר את התפריט הזול ביותר שנבדק - רק אם הוא מתאים לכל האורחים."""
        if self.best_cost and total >= self.best_cost["total_cost"]:
            return
        if self.tools.check_guests(menu).get("all_satisfied"):
            self.best_cost = {"menu": menu, "total_cost": total, "budget": self.tools.request.budget}

    def _check_give_up(self, args: dict) -> dict:
        """
        המודל רוצה לוותר. כמו שהצלחה נבדקת בקוד (finalize_menu), גם ויתור נבדק:
        אם לתפריט הזול ביותר שנבדק יש עוד החלפה שמוזילה אותו - הוויתור נדחה.
        """
        if self.best_cost:
            swaps = self.tools.suggest_swaps(self.best_cost["menu"])
            swap = swaps.get("best_swap") or swaps.get("best_double_swap")
            if swap:
                return {"error": f"עוד לא מוצו האפשרויות: בתפריט הזול ביותר שבדקת "
                                 f"({self.best_cost['total_cost']:.0f} ש\"ח) אפשר {describe_swap(swap)} "
                                 f"ולרדת ל-{swap['new_total']:.0f} ש\"ח. נסה את זה לפני שאתה מוותר."}
        self.failure_reason = args.get("reason") or "המודל לא פירט סיבה"
        return {"ok": True}


def describe_swap(swap: dict) -> str:
    """'להחליף את X ב-Y' - להחלפה בודדת או כפולה."""
    if isinstance(swap["replace"], str):
        return f"להחליף את '{swap['replace']}' ב-'{swap['with']}'"
    pairs = [f"'{old}' ב-'{new}'" for old, new in zip(swap["replace"], swap["with"])]
    return "להחליף יחד את " + " ואת ".join(pairs)


def _retry_delay(error: RateLimitError) -> float | None:
    """Gemini כותב בגוף השגיאה כמה לחכות: 'retry in 5.7s'. מכסה יומית ('20h34m') לא תתאים."""
    match = re.search(r"retry in ([\d.]+)s", str(error))
    return float(match.group(1)) if match else None


# ---------- הצגה ----------

def short_args(args) -> str:
    if not isinstance(args, dict):
        return repr(args)
    menu = args.get("menu")
    if isinstance(menu, dict):
        dishes = list(menu.get("mains") or []) + [menu.get(k) for k in ("side", "vegetable", "dessert")]
        return " | ".join(str(d) for d in dishes if d)
    return ", ".join(f"{k}={v}" for k, v in args.items())


def summarize(name: str, r: dict) -> str:
    """שורה אחת קריאה במקום JSON מלא."""
    if "error" in r:
        return "⚠ " + r["error"] + ("".join(f"\n     - {d}" for d in r.get("details", [])))
    if name == "search_recipes":
        return f"{r['count']} מנות: " + ", ".join(x["name"] for x in r["recipes"])
    if name == "calculate_cost":
        status = "✓ בתקציב" if r["within_budget"] else f"✗ חריגה של {-r['difference']:.0f} ש\"ח"
        text = f"{r['total_cost']:.0f} ש\"ח מתוך {r['budget']:g} {status}"
        if not r["complete"]:
            text += " | ⚠ חסר מחיר: " + ", ".join(m["ingredient"] for m in r["missing_prices"])
        return text
    if name == "check_guests":
        if r["all_satisfied"]:
            return "✓ כל האורחים מסודרים"
        return "✗ " + "".join(f"\n     - [{v['type']}] {v['guest']}: {v['problem']}" for v in r["violations"])
    if name == "suggest_swaps":
        swap = r.get("best_swap") or r.get("best_double_swap")
        if swap:
            return f"הכי משתלם: {describe_swap(swap)} (חוסך {swap['saving']:.0f} ש\"ח → {swap['new_total']:.0f})"
        return "אין החלפה שמוזילה את התפריט"
    if name == "finalize_menu":
        if r["approved"]:
            return "✓ התפריט אושר"
        return "✗ לא אושר" + "".join(f"\n     - {x}" for x in r["reasons"])
    return "התקבל"


def print_event(kind: str, data: dict):
    """מציג אירוע של האג'נט בטרמינל."""
    if kind == "step":
        print(f"\n── צעד {data['step']}/{data['max_steps']} " + "─" * 30)
    elif kind == "thought":
        print(f"💭 {data['text']}")
    elif kind == "tool":
        call = data["name"] if data["args"] is None else f"{data['name']}({short_args(data['args'])})"
        print(f"🔧 {call}")
        print(f"   ← {summarize(data['name'], data['result'])}")
    elif kind == "nudge":
        print("↩ המודל עצר בלי תפריט מאושר - מבקשים ממנו להמשיך")
    elif kind == "low_steps":
        print(f"↩ נשארו {data['remaining']} צעדים - מזהירים את המודל")
    elif kind == "rate_limit":
        print(f"⏳ חריגה ממכסת הבקשות לדקה - ממתין {data['seconds']:.0f} שניות")


def print_success(approved: dict):
    menu = approved["menu"]
    print("\n" + "═" * 50)
    print("✅ התפריט אושר\n")
    print(f"  עיקריות: {menu['mains'][0]}, {menu['mains'][1]}")
    print(f"  תוספת:   {menu['side']}")
    print(f"  ירקות:   {menu['vegetable']}")
    print(f"  קינוח:   {menu['dessert']}")
    print(f"\n  עלות: {approved['total_cost']:.2f} ש\"ח ({approved['cost_per_guest']:.2f} לסועד)")
    print("\n🛒 רשימת קניות:")
    for item in approved["shopping_list"]:
        print(f"  • {item['item']}: {item['needed']:g} {item['unit']} "
              f"→ {item['packages']} × {item['package_size']:g} {item['unit']}")


def print_failure(reason: str, best_cost: dict | None):
    print("\n" + "═" * 50)
    print("❌ לא הצלחתי להשלים את המשימה")
    print(f"\n  סיבה: {reason}")
    if best_cost:
        print(f"\n  התפריט התקין הזול ביותר שנבדק: {short_args(best_cost)}")
        print(f"  עלות: {best_cost['total_cost']:.2f} ש\"ח (תקציב: {best_cost['budget']:g})")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # כדי שעברית ואימוג'י יודפסו נכון ב-Windows
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="אג'נט לתכנון תפריט בתקציב")
    parser.add_argument("request", nargs="?", default=str(DEFAULT_REQUEST),
                        help="קובץ JSON עם האורחים והתקציב")
    args = parser.parse_args()

    # שגיאות הגדרה עוצרות לפני שמתחילים - האג'נט לא יכול לתקן אותן
    try:
        request = MenuRequest.from_dict(load_json(Path(args.request)))
        tools = MenuTools(request)
        client, model = create_client()
    except (DataError, ValueError, ConfigError) as e:
        print(f"שגיאה: {e}")
        return 2

    print("🎯 המטרה:\n" + describe_request(request))
    agent = MenuAgent(client, model, tools)
    try:
        success = agent.run()
    except RateLimitError:
        print("\n❌ נגמרה מכסת הבקשות למודל. אפשר לנסות שוב מאוחר יותר, "
              "או להחליף מודל ב-LLM_MODEL שבקובץ .env")
        return 1
    except APIError as e:  # אחרי שהספרייה כבר ניסתה שוב כמה פעמים
        print(f"\n❌ שגיאה בתקשורת עם המודל ({type(e).__name__}): {e}")
        return 1

    if success:
        print_success(tools.approved_menu)
        return 0
    print_failure(agent.failure_reason, agent.best_cost)
    return 1


if __name__ == "__main__":
    sys.exit(main())
