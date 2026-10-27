"""
השופט: בודק אם האג'נט קיבל את ההחלטה הנכונה.

זה *לא* חלק מהאג'נט. המאגר שלנו קטן (כ-4,200 צירופי תפריט), אז אפשר פשוט לנסות
את כולם ולדעת בוודאות מה התפריט התקין הזול ביותר - ומזה, אם בכלל היה פתרון.
עם מאגר אמיתי (אלפי מתכונים, החלפות מרכיבים) חיפוש כזה כבר לא אפשרי,
ובשביל זה יש אג'נט. כאן הוא משמש רק כ"תשובון" לבדיקה.

הרצת תרחישים מהטרמינל, עם טבלת תוצאות:
    python evaluate.py                          # כל התרחישים
    python evaluate.py edge_budget conflicting  # רק אלה
"""
import argparse
import sys
import time
from itertools import combinations, product

from openai import APIError, RateLimitError

from agent import MAX_STEPS, MenuAgent, print_event
from llm import ConfigError, create_client
from scenarios import SCENARIOS
from tools import DataError, MenuRequest, MenuTools


def find_best_menu(tools: MenuTools) -> dict:
    """מוצא את התפריט התקין הזול ביותר (מחיר מלא + כל האורחים מסודרים), בלי קשר לתקציב."""
    by_category = {c: [n for n, r in tools.recipes.items() if r["category"] == c]
                   for c in ("main", "side", "vegetable", "dessert")}
    best, valid_count, within_budget_count = None, 0, 0

    for mains, side, vegetable, dessert in product(combinations(by_category["main"], 2),
                                                   by_category["side"], by_category["vegetable"],
                                                   by_category["dessert"]):
        menu = {"mains": list(mains), "side": side, "vegetable": vegetable, "dessert": dessert}
        cost = tools.calculate_cost(menu)
        if not cost["complete"] or not tools.check_guests(menu)["all_satisfied"]:
            continue
        valid_count += 1
        within_budget_count += cost["within_budget"]
        if best is None or cost["total_cost"] < best["total_cost"]:
            best = {"menu": menu, "total_cost": cost["total_cost"]}

    return {
        "best": best,                                   # None = אין תפריט תקין בכלל
        "solvable": within_budget_count > 0,
        "valid_menus": valid_count,
        "valid_within_budget": within_budget_count,
    }


def judge(agent_success: bool, agent_cost: float | None, truth: dict) -> tuple[str, str]:
    """
    משווה את תוצאת האג'נט לתשובה הנכונה. מחזיר (pass/partial/fail/bug, הסבר).
    agent_cost: בהצלחה - עלות התפריט שאושר. בכישלון - הניסיון הזול ביותר שהאג'נט בדק (או None).
    """
    best = truth["best"]
    if agent_success and truth["solvable"]:
        extra = agent_cost - best["total_cost"]
        note = "בדיוק התפריט הזול ביותר" if extra < 0.01 else f"יקר ב-{extra:.0f} ש\"ח מהתפריט הזול ביותר האפשרי"
        return "pass", f"האג'נט מצא תפריט תקין ({note})"
    if not agent_success and not truth["solvable"]:
        if best is None:
            return "pass", "האג'נט צדק שאין פתרון: אין אף תפריט שמתאים לכל האורחים"
        text = f"האג'נט צדק שאין פתרון: התפריט התקין הזול ביותר עולה {best['total_cost']:.0f} ש\"ח"
        # המסקנה נכונה - אבל האם הוא בדק את החלופות הזולות לפני שוויתר?
        if agent_cost is not None and agent_cost - best["total_cost"] >= 0.01:
            return "partial", (f"{text}. אבל הוא ויתר מוקדם מדי: הניסיון הזול ביותר שלו עלה "
                               f"{agent_cost:.0f} ש\"ח, כך שהדיווח שלו לא מדויק")
        return "pass", text
    if not agent_success and truth["solvable"]:
        return "fail", (f"האג'נט פספס: היו {truth['valid_within_budget']} תפריטים תקינים בתקציב, "
                        f"הזול ביותר ב-{best['total_cost']:.0f} ש\"ח")
    # finalize_menu לא אמור לאשר תפריט כשאין פתרון - אם זה קרה, יש באג בכלים
    return "bug", "האג'נט אישר תפריט למרות שלפי השופט אין פתרון - יש באג בכלים!"


def run_scenario(request_data: dict, model: str | None = None, max_steps: int = MAX_STEPS,
                 on_event=None) -> dict:
    """מריץ את האג'נט על בקשה אחת ומחזיר סיכום כולל פסק דין. on_event מקבל כל צעד בזמן אמת."""
    events: list[tuple[str, dict]] = []

    def record(kind, data):
        events.append((kind, data))
        if on_event:
            on_event(kind, data)

    result = {"events": events, "request": request_data, "verdict": None}
    try:
        tools = MenuTools(MenuRequest.from_dict(request_data))
    except (ValueError, DataError) as e:
        result.update(status="invalid", error=str(e),
                      verdict=("pass", "הקלט נפסל לפני שהאג'נט רץ - בלי לבזבז קריאות למודל"))
        return result

    truth = find_best_menu(tools)
    result["truth"] = truth
    try:
        client, default_model = create_client()
    except ConfigError as e:
        result.update(status="error", error=str(e))
        return result

    agent = MenuAgent(client, model or default_model, tools, max_steps=max_steps, on_event=record)
    start = time.time()
    try:
        success = agent.run()
    except RateLimitError:
        success, error = None, "נגמרה מכסת הבקשות למודל. אפשר להחליף מודל או לנסות שוב מאוחר יותר."
    except APIError as e:
        success, error = None, f"שגיאה בתקשורת עם המודל ({type(e).__name__}): {e}"

    tool_calls = sum(kind == "tool" for kind, _ in events)
    result.update(duration=time.time() - start, steps=agent.steps_used, tool_calls=tool_calls)
    if success is None:
        result.update(status="error", error=error)
        return result

    approved = tools.approved_menu
    if success:
        agent_cost = approved["total_cost"]
    else:
        agent_cost = agent.best_cost and agent.best_cost["total_cost"]
    result.update(status="success" if success else "failure", approved=approved,
                  failure_reason=agent.failure_reason, best_cost=agent.best_cost,
                  verdict=judge(success, agent_cost, truth))
    return result


VERDICT_ICON = {"pass": "✅", "partial": "⚠️", "fail": "❌", "bug": "🐞"}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    by_id = {s["id"]: s for s in SCENARIOS}
    parser = argparse.ArgumentParser(description="הרצת תרחישי בדיקה ושיפוט התוצאות")
    parser.add_argument("scenarios", nargs="*", metavar="SCENARIO",
                        help=f"מזהי תרחישים (ברירת מחדל: כולם). אפשרויות: {', '.join(by_id)}")
    parser.add_argument("--model", help="מודל אחר מזה שב-.env")
    parser.add_argument("--verbose", action="store_true", help="להציג כל צעד של האג'נט")
    args = parser.parse_args()
    unknown = [sid for sid in args.scenarios if sid not in by_id]
    if unknown:
        parser.error(f"תרחישים לא מוכרים: {unknown}. אפשרויות: {', '.join(by_id)}")

    rows = []
    for sid in args.scenarios or list(by_id):
        print(f"▶ {by_id[sid]['title']}...", flush=True)
        r = run_scenario(by_id[sid]["request"], args.model, on_event=print_event if args.verbose else None)
        status, text = r["verdict"] or ("fail", r.get("error", ""))
        rows.append((sid, status, r.get("steps", 0), r.get("duration", 0)))
        print(f"  {VERDICT_ICON[status]} {text}")
        if "steps" in r:
            print(f"  צעדים: {r['steps']}, הפעלות כלים: {r['tool_calls']}, זמן: {r['duration']:.0f} שניות")

    ran = [row for row in rows if row[2]]  # תרחישים שהאג'נט באמת רץ בהם
    print("\n" + "═" * 50)
    print(f"עברו: {sum(row[1] == 'pass' for row in rows)}/{len(rows)}")
    if ran:
        print(f"ממוצע צעדים: {sum(row[2] for row in ran) / len(ran):.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
