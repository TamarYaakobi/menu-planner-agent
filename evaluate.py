"""
השופט: בודק אם האג'נט קיבל את ההחלטה הנכונה.

זה *לא* חלק מהאג'נט. המאגר שלנו קטן (כ-4,200 צירופי תפריט), אז אפשר פשוט לנסות
את כולם ולדעת בוודאות מה התפריט התקין הזול ביותר - ומזה, אם בכלל היה פתרון.
עם מאגר אמיתי (אלפי מתכונים, החלפות מרכיבים) חיפוש כזה כבר לא אפשרי,
ובשביל זה יש אג'נט. כאן הוא משמש רק כ"תשובון" לבדיקה.
"""
from itertools import combinations, product

from tools import MenuTools


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
