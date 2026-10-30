"""
השופט: בודק אם האג'נט קיבל את ההחלטה הנכונה.

זה *לא* חלק מהאג'נט. המאגר שלנו קטן מספיק כדי לעבור על כל צירופי התפריט, ולדעת בוודאות
מה התפריט התקין הזול ביותר - ומזה, אם בכלל היה פתרון.
עם מאגר אמיתי (אלפי מתכונים, החלפות מרכיבים) חיפוש כזה כבר לא אפשרי,
ובשביל זה יש אג'נט. כאן הוא משמש רק כ"תשובון" לבדיקה.

הרצת תרחישים מהטרמינל, עם טבלת תוצאות:
    python evaluate.py                          # כל התרחישים
    python evaluate.py edge_budget conflicting  # רק אלה
    python evaluate.py --goals                  # פענוח מטרות בשפה חופשית
"""
import argparse
import bisect
import math
import sys
import time
from itertools import combinations, product

from openai import APIError, RateLimitError

from agent import MAX_STEPS, MenuAgent, print_event
from goal_cases import GOAL_CASES, UPDATE_CASES, check_goal
from goal_parser import ParsedGoal, parse_goal
from llm import ConfigError, create_client, fallback_models
from scenarios import SCENARIOS
from tools import MAX_DISLIKED_NON_MAIN, DataError, MenuRequest, MenuTools, kashrut


def find_best_menu(tools: MenuTools) -> dict:
    """
    מוצא את התפריט התקין הזול ביותר (מחיר מלא + כל האורחים מסודרים), בלי קשר לתקציב.
    עם מנה ראשונה יש כבר כחצי מיליון צירופים, אז לא בודקים כל תפריט בנפרד:
      - לכל סוג מנה עוברים על כל הצירופים שלו (למשל כל זוג עיקריות), ומקבצים צירופים שנראים
        אותו דבר מבחינת הכללים: אילו אורחים "נתקעים" רק עם מנות שהם לא אוהבים, בשרי/חלבי,
        ואילו "אוהב/ת" יש בהם. צירוף שמישהו לא יכול לאכול ממנו כלום - נפסל מיד.
      - עוברים על כל הצירופים של קבוצות (מעטים), ובכל אחד הזול ביותר הוא סכום הזולים.
    את התפריט הזול ביותר שנמצא בודק גם check_guests - אם הם לא מסכימים, זה באג.
    """
    request = tools.request
    guests = request.guests
    costs = {}
    for name in tools.recipes:
        cost, missing = tools.dish_cost(name)
        costs[name] = None if missing else cost
    likes = [(g, like) for g in guests for like in g.likes]

    groups = []  # לכל סוג מנה: {חתימה: [(עלות, צירוף), ...] מהזול ליקר}
    for category, count in request.courses.items():
        names = [n for n, r in tools.recipes.items() if r["category"] == category and costs[n] is not None
                 and n not in request.exclude_dishes]
        by_signature: dict[tuple, list] = {}
        for combo in combinations(names, count):
            recipes = [tools.recipes[n] for n in combo]
            stuck = []  # לכל אורח: כל מה שמותר לו כאן מכיל משהו שהוא לא אוהב
            for g in guests:
                edible = [r for r in recipes if not g.hard_conflicts(r)]
                if not edible:
                    break
                stuck.append(all(g.disliked_ingredients(r) for r in edible))
            else:
                if category == "main" and any(stuck):
                    continue
                signature = (tuple(stuck), frozenset(kashrut(r) for r in recipes) & {"meat", "dairy"},
                             tuple(any(like in g.liked_in(r) for r in recipes) for g, like in likes))
                by_signature.setdefault(signature, []).append((round(sum(costs[n] for n in combo), 2), combo))
        groups.append([(sig, sorted(options)) for sig, options in by_signature.items()])

    best, valid_count, within_budget_count, max_likes = None, 0, 0, 0
    for chosen in product(*groups):
        signatures = [sig for sig, _ in chosen]
        stuck_courses = [sum(sig[0][i] for sig in signatures) for i in range(len(guests))]
        if any(n > MAX_DISLIKED_NON_MAIN for n in stuck_courses):
            continue
        if request.kosher and {"meat", "dairy"} <= set().union(*(sig[1] for sig in signatures)):
            continue
        options = [opts for _, opts in chosen]
        valid_count += math.prod(len(o) for o in options)
        total = round(sum(o[0][0] for o in options), 2)
        if total <= request.budget:
            within_budget_count += _count_within([[c for c, _ in o] for o in options], request.budget)
            max_likes = max(max_likes, sum(any(sig[2][i] for sig in signatures) for i in range(len(likes))))
        if best is None or total < best["total_cost"]:
            best = {"menu": {c: list(o[0][1]) for c, o in zip(request.courses, options)}, "total_cost": total}

    if best and not tools.check_guests(best["menu"])["all_satisfied"]:
        raise AssertionError(f"השופט והכלים לא מסכימים על התפריט {best['menu']} - יש באג באחד מהם")
    return {
        "best": best,                                   # None = אין תפריט תקין בכלל
        "solvable": best is not None and best["total_cost"] <= request.budget,
        "valid_menus": valid_count,
        "valid_within_budget": within_budget_count,
        "total_likes": len(likes),
        "max_likes": max_likes,                         # הכי הרבה "אוהב/ת" בתפריט תקין שבתקציב
    }


def _count_within(costs: list[list[float]], limit: float) -> int:
    """בכמה צירופים (מחיר אחד מכל רשימה ממוינת) הסכום לא עובר את limit - בלי לעבור על כולם."""
    first, rest = costs[0], costs[1:]
    if not rest:
        return bisect.bisect_right(first, limit + 1e-9)
    rest_min, rest_max = sum(r[0] for r in rest), sum(r[-1] for r in rest)
    rest_size = math.prod(len(r) for r in rest)
    count = 0
    for cost in first:
        if cost + rest_min > limit + 1e-9:
            break
        count += rest_size if cost + rest_max <= limit + 1e-9 else _count_within(rest, limit - cost)
    return count


def likes_note(agent_likes: int | None, truth: dict) -> str:
    """'אוהב/ת' הוא העדפה רכה - לא משנה את פסק הדין, אבל מדווחים כמה נכללו ומה היה אפשר."""
    if not truth.get("total_likes") or agent_likes is None:
        return ""
    possible = truth["max_likes"]
    note = f" | אוהב/ת: {agent_likes}/{truth['total_likes']}"
    if agent_likes < possible:
        return note + f" (אפשר היה {possible})"
    return note + (" (המקסימום האפשרי בתקציב)" if possible < truth["total_likes"] else "")


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
                 on_event=None, fallbacks: list[str] | None = None) -> dict:
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

    agent = MenuAgent(client, model or default_model, tools, max_steps=max_steps, on_event=record,
                      fallbacks=fallbacks)
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
    status, text = judge(success, agent_cost, truth)
    if success:
        text += likes_note(sum(x["in_menu"] for x in approved.get("likes", [])), truth)
    result.update(status="success" if success else "failure", approved=approved,
                  failure_reason=agent.failure_reason, best_cost=agent.best_cost, verdict=(status, text))
    return result


VERDICT_ICON = {"pass": "✅", "partial": "⚠️", "fail": "❌", "bug": "🐞"}


def run_goal_cases(model: str | None, verbose: bool) -> int:
    """מריץ את המפענח על כל משפטי הבדיקה ומשווה לתשובה הנכונה."""
    client, default_model = create_client()
    passed = 0
    for case in GOAL_CASES:
        try:
            result = parse_goal(client, model or default_model, case["text"], fallbacks=fallback_models())
        except APIError as e:  # תקלה בשרת - לא כישלון של הפענוח, אבל גם לא הצלחה
            print(f"⚠️ {case['text']}\n     - שגיאה בתקשורת עם המודל ({type(e).__name__})")
            continue
        problems = check_goal(case["expect"], result)
        passed += not problems
        print(f"{'✅' if not problems else '❌'} {case['text']}")
        for p in problems:
            print(f"     - {p}")
        if verbose or problems:
            print(f"     ← {result.status}, ניסיונות: {result.attempts}, בקשה: {result.request}")
            for label, items in (("הנחות", result.assumptions), ("לא נתמך", result.unsupported),
                                 ("אזהרות", result.warnings)):
                if items:
                    print(f"       {label}: {items}")
    print("\n── עדכונים לבקשה קיימת ──")
    for case in UPDATE_CASES:
        previous = ParsedGoal("ok", request=case["previous"])
        try:
            result = parse_goal(client, model or default_model, case["text"], previous=previous,
                                fallbacks=fallback_models())
        except APIError as e:
            print(f"⚠️ {case['text']}\n     - שגיאה בתקשורת עם המודל ({type(e).__name__})")
            continue
        problems = check_goal(case["expect"], result)
        passed += not problems
        print(f"{'✅' if not problems else '❌'} {case['text']}")
        for p in problems:
            print(f"     - {p}")
        if verbose or problems:
            print(f"     ← שינויים: {result.changes}, אזהרות: {result.warnings}, ניסיונות: {result.attempts}")
    print("\n" + "═" * 50)
    print(f"עברו: {passed}/{len(GOAL_CASES) + len(UPDATE_CASES)}")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    by_id = {s["id"]: s for s in SCENARIOS}
    parser = argparse.ArgumentParser(description="הרצת תרחישי בדיקה ושיפוט התוצאות")
    parser.add_argument("scenarios", nargs="*", metavar="SCENARIO",
                        help=f"מזהי תרחישים (ברירת מחדל: כולם). אפשרויות: {', '.join(by_id)}")
    parser.add_argument("--model", help="מודל אחר מזה שב-.env")
    parser.add_argument("--verbose", action="store_true", help="להציג כל צעד של האג'נט")
    parser.add_argument("--goals", action="store_true", help="לבדוק פענוח של מטרות בשפה חופשית")
    args = parser.parse_args()
    if args.goals:
        return run_goal_cases(args.model, args.verbose)
    unknown = [sid for sid in args.scenarios if sid not in by_id]
    if unknown:
        parser.error(f"תרחישים לא מוכרים: {unknown}. אפשרויות: {', '.join(by_id)}")

    rows = []
    for sid in args.scenarios or list(by_id):
        print(f"▶ {by_id[sid]['title']}...", flush=True)
        r = run_scenario(by_id[sid]["request"], args.model, on_event=print_event if args.verbose else None,
                         fallbacks=fallback_models())
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
