"""
הכלים של האג'נט.

אלה פונקציות Python רגילות, בלי AI. ה-LLM מחליט *איזה* כלי להפעיל ועם אילו
פרמטרים, אבל החישובים והבדיקות עצמם קורים כאן, בקוד: מודל שפה עלול לטעות
בחשבון או "לשכוח" אילוץ, וקוד לא.

כל כלי מחזיר dict שאפשר להמיר ל-JSON ולשלוח חזרה למודל.
טעות של המודל (למשל שם מנה שלא קיים) לא זורקת חריגה אלא חוזרת כתוצאה,
כדי שהאג'נט יוכל לקרוא אותה ולתקן את עצמו.
"""
from __future__ import annotations

import difflib
import json
import math
from dataclasses import dataclass, field
from itertools import combinations, product
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

CATEGORIES = {"starter": "מנה ראשונה", "main": "עיקרית", "side": "תוספת", "vegetable": "ירקות", "dessert": "קינוח"}
# לתיאור התפריט למשתמש: (אחת, כמה)
COURSE_NAMES = {"starter": ("מנה ראשונה", "מנות ראשונות"), "main": ("עיקרית", "עיקריות"),
                "side": ("תוספת", "תוספות"), "vegetable": ("מנת ירקות", "מנות ירקות"), "dessert": ("קינוח", "קינוחים")}
ALLERGENS = {"gluten": "גלוטן", "eggs": "ביצים", "dairy": "חלב", "nuts": "אגוזים", "fish": "דגים",
             "peanuts": "בוטנים", "sesame": "שומשום", "soy": "סויה"}
DIETS = {"vegetarian": "צמחונית", "vegan": "טבעונית"}
UNITS = {"g": "גרם", "ml": "מ\"ל", "unit": "יח'"}
KASHRUT = {"meat": "בשרי", "dairy": "חלבי", "parve": "פרווה"}


def kashrut(recipe: dict) -> str:
    """
    בשרי / חלבי / פרווה - נגזר מהתגיות הקיימות, כדי שלא יהיה שדה נוסף שאפשר לשכוח לעדכן:
    חלב -> חלבי. לא צמחוני ולא דג -> בשרי. כל השאר (כולל דגים) -> פרווה.
    """
    if "dairy" in recipe["allergens"]:
        return "dairy"
    if "vegetarian" not in recipe["diet"] and "fish" not in recipe["allergens"]:
        return "meat"
    return "parve"

# מה יש בתפריט כשהמשתמש לא אמר: כמה מנות מכל סוג
DEFAULT_COURSES = {"main": 2, "side": 1, "vegetable": 1, "dessert": 1}
MAX_PER_COURSE = 3
# כשיש כמה מנות מאותו סוג, כל סועד לוקח קצת מכל אחת - ביחד 1.2 מנות (שתי עיקריות: 60% מכל אחת)
SHARED_PORTIONS = 1.2
# בכמה סוגי מנות (חוץ מהעיקריות) מותר שכל מה שאורח יכול לאכול יכיל משהו שהוא לא אוהב
MAX_DISLIKED_NON_MAIN = 1


def portion(dishes_of_kind: int) -> float:
    """כמה מנה מכל מנה מחשבים לסועד, לפי מספר המנות מאותו סוג."""
    return 1.0 if dishes_of_kind <= 1 else SHARED_PORTIONS / dishes_of_kind


def describe_courses(courses: dict) -> str:
    """{"main": 2, "dessert": 1} -> "2 עיקריות, קינוח" """
    return ", ".join(COURSE_NAMES[c][0] if n == 1 else f"{n} {COURSE_NAMES[c][1]}" for c, n in courses.items())


def menu_dishes(menu: dict) -> list[str]:
    """כל המנות בתפריט, לפי סדר הארוחה."""
    return [d for c in CATEGORIES if isinstance(menu.get(c), list) for d in menu[c]]


# מילה כללית ("לא אוהב עוף") -> מה נחשב כזה במאגר. חיפוש פשוט של "עוף" לא תופס "פרגיות",
# ו"דגים" לא מופיע בשם של אף מרכיב. מילה ספציפית ("חזה עוף") נשארת ספציפית
FOOD_GROUPS = {word: members for words, members in (
    (("עוף", "עופות"), ("עוף", "פרגיות")),
    (("דג", "דגים"), ("דג", "סלמון", "אמנון")),
    (("גבינה", "גבינות"), ("גבינ", "פרמזן", "מוצרלה")),
    (("אגוז", "אגוזים"), ("אגוז", "שקדים")),
) for word in words}


def mentions(word: str, name: str) -> bool:
    """האם "עוף" / "פטריות" מתאים לשם המרכיב או המנה ("פרגיות", "פטריות שמפיניון")."""
    return any(part in name for part in FOOD_GROUPS.get(word, (word,)))


class DataError(Exception):
    """קובץ נתונים חסר או לא תקין - בעיה שהאג'נט לא יכול לתקן בעצמו."""


def load_json(path: Path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise DataError(f"קובץ נתונים חסר: {path}") from None
    except json.JSONDecodeError as e:
        raise DataError(f"הקובץ {path.name} אינו JSON תקין (שורה {e.lineno}): {e.msg}") from None


# ---------- המטרה: מי מגיע, מה התקציב, ומה כל אורח לא אוכל ----------

def _string_list(guest: dict, key: str, name: str) -> list[str]:
    """שדה שחייב להיות רשימת מחרוזות. מחרוזת בודדת ("nuts") הייתה מתפרשת אות אחרי אות."""
    value = guest.get(key, [])
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"{key} אצל {name} צריך להיות רשימה של מחרוזות, למשל [\"nuts\"]")
    return list(value)


@dataclass
class Guest:
    name: str
    allergies: list[str] = field(default_factory=list)  # אילוץ חובה
    diet: str | None = None                             # אילוץ חובה
    dislikes: list[str] = field(default_factory=list)   # העדפה בלבד
    likes: list[str] = field(default_factory=list)      # העדפה רכה: כדאי לכלול, לא חובה

    def liked_in(self, recipe: dict) -> list[str]:
        """מה מהדברים שהאורח אוהב יש במנה - לפי שם המנה ("שקשוקה") או מרכיב ("חסה")."""
        names = [recipe["name"]] + [ing["name"] for ing in recipe["ingredients"]]
        return [like for like in self.likes if any(mentions(like, n) for n in names)]

    def hard_conflicts(self, recipe: dict) -> list[str]:
        """הסיבות שבגללן האורח *לא יכול* לאכול את המנה (רשימה ריקה = יכול)."""
        reasons = [f"מכילה {ALLERGENS[a]}" for a in self.allergies if a in recipe["allergens"]]
        if self.diet and self.diet not in recipe["diet"]:
            reasons.append(f"לא {DIETS[self.diet]}")
        return reasons

    def disliked_ingredients(self, recipe: dict) -> list[str]:
        """מרכיבים במנה שהאורח לא אוהב. "פטריות" תתפוס גם "פטריות שמפיניון", ו"עוף" גם "פרגיות"."""
        return [ing["name"] for ing in recipe["ingredients"]
                if any(mentions(d, ing["name"]) for d in self.dislikes)]


@dataclass
class MenuRequest:
    num_guests: int
    budget: float                                      # תקרה קשיחה - finalize_menu לא יאשר מעליה
    guests: list[Guest] = field(default_factory=list)  # רק אורחים עם אילוצים או העדפות
    target_budget: float | None = None                 # "בערך 400": היעד. מותר לעבור אותו עד budget
    kosher: bool = False                               # בלי בשר וחלב באותה ארוחה
    courses: dict = field(default_factory=lambda: dict(DEFAULT_COURSES))  # כמה מנות מכל סוג, לפי סדר הארוחה
    exclude_dishes: list[str] = field(default_factory=list)  # מנות שהמשתמש ביקש להחליף ("אבל יש מרק עוף")

    @classmethod
    def from_dict(cls, data: dict) -> MenuRequest:
        """בונה בקשה מ-dict ובודק שהיא הגיונית. זורק ValueError עם הסבר."""
        if not isinstance(data, dict):
            raise ValueError("הבקשה צריכה להיות אובייקט JSON עם num_guests, budget ו-guests")
        num_guests = data.get("num_guests")
        budget = data.get("budget")
        if not isinstance(num_guests, int) or isinstance(num_guests, bool) or num_guests < 1:
            raise ValueError("מספר האורחים חייב להיות מספר שלם וחיובי")
        # math.isfinite: JSON של Python מקבל גם Infinity ו-NaN
        if (not isinstance(budget, (int, float)) or isinstance(budget, bool)
                or not math.isfinite(budget) or budget <= 0):
            raise ValueError("התקציב חייב להיות מספר חיובי")
        if not isinstance(data.get("guests", []), list):
            raise ValueError("guests צריך להיות רשימה של אורחים")

        guests = []
        for g in data.get("guests", []):
            if not isinstance(g, dict):
                raise ValueError(f"כל אורח צריך להיות אובייקט עם name, ולא: {g!r}")
            name = g.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("לכל אורח ברשימה צריך להיות שם")
            allergies = _string_list(g, "allergies", name)
            bad = [a for a in allergies if a not in ALLERGENS]
            if bad:
                raise ValueError(f"אלרגיה לא מוכרת אצל {name}: {bad}. אפשרויות: {list(ALLERGENS)}")
            diet = g.get("diet")
            if diet is not None and (not isinstance(diet, str) or diet not in DIETS):  # גם רשימה - לא רק ערך לא מוכר
                raise ValueError(f"תזונה לא מוכרת אצל {name}: '{diet}'. אפשרויות: {list(DIETS)}")
            # העדפה ריקה הייתה "מתאימה" לכל מרכיב ("" in "עגבניות" == True), אז מסננים אותה
            dislikes = [d.strip() for d in _string_list(g, "dislikes", name) if d.strip()]
            likes = [x.strip() for x in _string_list(g, "likes", name) if x.strip()]
            guests.append(Guest(name.strip(), allergies, diet, dislikes, likes))

        if len(guests) > num_guests:
            raise ValueError(f"ברשימה יש {len(guests)} אורחים, יותר ממספר האורחים ({num_guests})")

        target = data.get("target_budget")
        if target is not None:
            if (not isinstance(target, (int, float)) or isinstance(target, bool)
                    or not math.isfinite(target) or not 0 < target <= budget):
                raise ValueError("יעד התקציב חייב להיות מספר חיובי, ולא גבוה מהתקציב המקסימלי")
            target = float(target)
        kosher = data.get("kosher", False)
        if not isinstance(kosher, bool):
            raise ValueError("kosher צריך להיות true או false")
        exclude = data.get("exclude_dishes", [])
        if not isinstance(exclude, list) or not all(isinstance(x, str) for x in exclude):
            raise ValueError("exclude_dishes צריך להיות רשימה של שמות מנות")
        return cls(num_guests, float(budget), guests, target, kosher, _courses(data.get("courses")),
                   [x.strip() for x in exclude if x.strip()])


def _courses(value) -> dict:
    """{"main": 2, "dessert": 1} -> אותו דבר, לפי סדר הארוחה ובלי סוגים עם 0. None = ברירת המחדל."""
    if value is None:
        return dict(DEFAULT_COURSES)
    if not isinstance(value, dict):
        raise ValueError('courses צריך להיות אובייקט: כמה מנות מכל סוג, למשל {"main": 2, "dessert": 1}')
    bad = [c for c in value if c not in CATEGORIES]
    if bad:
        raise ValueError(f"סוג מנה לא מוכר: {bad}. אפשרויות: {list(CATEGORIES)}")
    for c, n in value.items():
        if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= MAX_PER_COURSE:
            raise ValueError(f"מספר המנות מסוג {CATEGORIES[c]} צריך להיות בין 0 ל-{MAX_PER_COURSE}")
    courses = {c: value[c] for c in CATEGORIES if value.get(c)}
    if not courses:
        raise ValueError("בתפריט צריכה להיות לפחות מנה אחת")
    return courses


# ---------- הכלים ----------

class MenuTools:
    """
    הכלים שהאג'נט יכול להפעיל. המחלקה מחזיקה את הנתונים ואת הבקשה, כך שהמודל
    לא צריך (ולא יכול) לשלוח מחדש את רשימת האורחים או את התקציב ולשנות אותם בטעות.
    """

    def __init__(self, request: MenuRequest, recipes: list[dict] | None = None,
                 prices: dict | None = None):
        self.request = request
        recipes = recipes if recipes is not None else load_json(DATA_DIR / "recipes.json")
        self.prices = prices if prices is not None else load_json(DATA_DIR / "prices.json")
        self.recipes = {r["name"]: r for r in recipes}
        self.approved_menu: dict | None = None  # מתמלא רק כש-finalize_menu מאשר
        # המקומות בתפריט: (סוג, מספר המנה מהסוג הזה)
        self.slots = [(c, i) for c, n in request.courses.items() for i in range(n)]
        self._check_data()

    def _check_data(self):
        """שגיאות בקבצי הנתונים עוצרות את התוכנית מיד, ולא באמצע ריצה."""
        for r in self.recipes.values():
            if r["category"] not in CATEGORIES:
                raise DataError(f"קטגוריה לא מוכרת במתכון '{r['name']}': {r['category']}")
            for ing in r["ingredients"]:
                item = self.prices.get(ing["name"])
                if item and item["unit"] != ing["unit"]:
                    raise DataError(f"יחידות לא תואמות ל'{ing['name']}' במתכון '{r['name']}': "
                                    f"{ing['unit']} במתכון, {item['unit']} בקטלוג")

    # --- כלי 1 ---
    def search_recipes(self, category: str, exclude_allergens: list[str] | None = None,
                       diet: str | None = None,
                       exclude_ingredients: list[str] | None = None,
                       include_any: list[str] | None = None) -> dict:
        """
        מחפש מנות בקטגוריה, עם סינון לפי אלרגנים, תזונה ומרכיבים.
        include_any: רק מנות שיש בהן לפחות אחד מאלה (בשם המנה או במרכיב) - למשל מה שאורח אוהב.
        """
        if category not in CATEGORIES:
            return {"error": f"קטגוריה לא מוכרת: '{category}'. אפשרויות: {list(CATEGORIES)}"}
        exclude_allergens = exclude_allergens or []
        exclude_ingredients = exclude_ingredients or []
        bad = [a for a in exclude_allergens if a not in ALLERGENS]
        if bad:
            return {"error": f"אלרגנים לא מוכרים: {bad}. אפשרויות: {list(ALLERGENS)}"}
        if diet is not None and diet not in DIETS:
            return {"error": f"תזונה לא מוכרת: '{diet}'. אפשרויות: {list(DIETS)}"}

        results = []
        for r in self.recipes.values():
            if r["category"] != category:
                continue
            if set(exclude_allergens) & set(r["allergens"]):
                continue
            if diet and diet not in r["diet"]:
                continue
            if r["name"] in self.request.exclude_dishes:
                continue
            names = [ing["name"] for ing in r["ingredients"]]
            if any(mentions(x, n) for x in exclude_ingredients for n in names):
                continue
            if include_any and not any(mentions(x, n) for x in include_any for n in names + [r["name"]]):
                continue
            results.append({"name": r["name"], "allergens": r["allergens"], "kashrut": kashrut(r),
                            "diet": r["diet"], "ingredients": names})

        result = {"category": category, "count": len(results), "recipes": results}
        if not results:
            result["note"] = "לא נמצאו מנות שעומדות בכל הסינונים. אפשר לנסות לוותר על חלק מהם."
        return result

    # --- כלי 2 ---
    def calculate_cost(self, menu: dict) -> dict:
        """מחשב את עלות התפריט לפי מחיר כל מרכיב בקטלוג ומספר האורחים."""
        dishes, errors = self._parse_menu(menu)
        if errors:
            return {"error": "התפריט לא תקין", "details": errors}

        breakdown, missing = [], []
        for r in dishes:
            cost, no_price = self.dish_cost(r["name"])
            missing += [{"dish": r["name"], "ingredient": name} for name in no_price]
            breakdown.append({"name": r["name"], "category": r["category"], "cost": cost})

        total = round(sum(d["cost"] for d in breakdown), 2)
        result = {
            "total_cost": total,
            "budget": self.request.budget,
            "within_budget": total <= self.request.budget,
            "difference": round(self.request.budget - total, 2),  # שלילי = חריגה
            "cost_per_guest": round(total / self.request.num_guests, 2),
            # מהיקרה לזולה, כדי שיהיה קל לראות מה כדאי להחליף
            "dishes": sorted(breakdown, key=lambda d: d["cost"], reverse=True),
            "complete": not missing,
        }
        if self.request.target_budget is not None:
            result["target_budget"] = self.request.target_budget
            result["within_target"] = total <= self.request.target_budget
        if missing:
            result["missing_prices"] = missing
            result["warning"] = ("לחלק מהמרכיבים אין מחיר בקטלוג, ולכן העלות חלקית. "
                                 "אי אפשר לאשר תפריט עם המנות האלה - צריך להחליף אותן.")
        return result

    # --- כלי 3 ---
    def check_guests(self, menu: dict) -> dict:
        """בודק לכל אורח: אילוצי חובה (אלרגיה, תזונה) והעדפות (מה לא אוהב)."""
        dishes, errors = self._parse_menu(menu)
        if errors:
            return {"error": "התפריט לא תקין", "details": errors}

        courses = {c: [r for r in dishes if r["category"] == c] for c in self.request.courses}
        violations = []

        def add(guest, kind, problem):
            violations.append({"guest": guest.name, "type": kind, "problem": problem})

        for g in self.request.guests:
            # בכל סוג מנה מספיק שאחת המנות מתאימה. כשיש רק מנה אחת מסוג מסוים - היא חייבת להתאים לכולם
            disliked = []  # מנות מסוגים (חוץ מעיקריות) שבהם כל מה שמותר לאורח מכיל משהו שהוא לא אוהב
            for category, kind in courses.items():
                edible = [r for r in kind if not g.hard_conflicts(r)]
                if not edible:
                    if len(kind) == 1:
                        add(g, "hard", f"אסור לאכול את '{kind[0]['name']}': {', '.join(g.hard_conflicts(kind[0]))}")
                    else:
                        why = "; ".join(f"{r['name']}: {', '.join(g.hard_conflicts(r))}" for r in kind)
                        add(g, "hard", f"אין אף מנה מסוג {CATEGORIES[category]} שמותר לאכול ({why})")
                elif all(g.disliked_ingredients(r) for r in edible):
                    if category == "main":
                        add(g, "soft", "כל המנות העיקריות שמותר לאכול מכילות מרכיב לא אהוב")
                    else:
                        disliked += [r["name"] for r in edible]

            if len({self.recipes[d]["category"] for d in disliked}) > MAX_DISLIKED_NON_MAIN:
                add(g, "soft", f"יותר מדי מנות עם מרכיבים לא אהובים: {disliked} "
                               f"(מותר עד סוג מנה אחד, חוץ מהעיקריות)")

        for r in dishes:
            if r["name"] in self.request.exclude_dishes:
                violations.append({"guest": "בקשת המשתמש", "type": "hard",
                                   "problem": f"המשתמש ביקש להחליף את '{r['name']}'"})

        # כשרות: אילוץ על כל התפריט, לא על אורח מסוים
        if self.request.kosher:
            meat = [r["name"] for r in dishes if kashrut(r) == "meat"]
            dairy = [r["name"] for r in dishes if kashrut(r) == "dairy"]
            if meat and dairy:
                violations.append({"guest": "כשרות", "type": "hard",
                                   "problem": f"בשר וחלב באותה ארוחה - בשרי: {', '.join(meat)}; "
                                              f"חלבי: {', '.join(dairy)}"})

        result = {"all_satisfied": not violations, "violations": violations,
                  "guests_checked": len(self.request.guests)}
        likes = self.likes_report(dishes)
        if likes:  # מידע בלבד - לא משפיע על all_satisfied
            result["likes"] = likes
        return result

    def likes_report(self, dishes: list[dict]) -> list[dict]:
        """לכל דבר שאורח אוהב: האם הוא בתפריט, ובאיזו מנה. העדפה רכה - לא הפרה."""
        report = []
        for g in self.request.guests:
            for like in g.likes:
                found = [r["name"] for r in dishes if like in g.liked_in(r)]
                report.append({"guest": g.name, "likes": like, "in_menu": bool(found), "dishes": found})
        return report

    # ---------- למה העדפה לא מומשה (למשתמש, אחרי שהתפריט אושר) ----------

    def preferences_report(self, menu: dict) -> dict:
        """
        likes: כל "אוהב/ת" - האם בתפריט, ואם לא - למה.
        compromises: כל מנה בתפריט שמישהו לא אוהב, ומה יש לו במקומה מאותו סוג (alternative) - ולמה לא הוחלפה.
        הסיבה נבדקת בקוד: מנסים לשים בתפריט מנה מתאימה, ובודקים מה היה נשבר.
        """
        dishes, _ = self._parse_menu(menu)
        likes = self.likes_report(dishes)
        # likes_report עובר על האורחים ועל ה"אוהב" שלהם באותו סדר - כך כל שורה מתאימה לאורח שלה,
        # גם כששני אורחים נקראים באותו שם ("אורח")
        guests = [g for g in self.request.guests for _ in g.likes]
        for item, guest in zip(likes, guests):
            if not item["in_menu"]:
                options = [r for r in self.recipes.values() if item["likes"] in guest.liked_in(r)]
                item["reason"] = self._why_not(menu, options, f"אין במאגר מנה עם {item['likes']}")

        compromises = []
        for g in self.request.guests:
            for r in dishes:
                if not g.disliked_ingredients(r) or g.hard_conflicts(r):
                    continue
                # גם כשיש לו מנה אחרת מאותו סוג - מראים: בצ'אט, "מרק עוף" לצד מרק עדשים עבר בשקט,
                # למרות שהמארחת לא אוהבת עוף והיה מרק זול יותר שכולם אוכלים
                alternative = [x["name"] for x in dishes if x["category"] == r["category"]
                               and not g.hard_conflicts(x) and not g.disliked_ingredients(x)]
                disliked = ", ".join(g.disliked_ingredients(r))
                options = [x for x in self.recipes.values()
                           if x["category"] == r["category"] and x["name"] != r["name"] and not g.disliked_ingredients(x)]
                compromises.append({"guest": g.name, "dislikes": disliked, "dish": r["name"], "alternative": alternative,
                                    "reason": self._why_not(menu, options, f"אין חלופה בלי {disliked}")})
        return {"likes": likes, "compromises": compromises}

    def _why_not(self, menu: dict, options: list[dict], no_options: str) -> str:
        """הסיבה הכי רלוונטית לכך שאף אחת מהמנות לא בתפריט. אם אחת מהן הייתה עובדת - אומרים את זה בכנות."""
        if not options:
            return no_options
        outcomes = sorted((self._try_dish(menu, r) for r in options), key=lambda o: o[0])
        rank, text = outcomes[0]
        if rank == 0:
            return f"אפשר היה - {text} - אבל האג'נט בחר אחרת"
        reasons = list(dict.fromkeys(t for _, t in outcomes))
        return "; ".join(reasons[:2]) + (" ועוד" if len(reasons) > 2 else "")

    def _try_dish(self, menu: dict, recipe: dict) -> tuple[int, str]:
        """
        מה קורה אם שמים את המנה בתפריט (במקום אחת המנות מאותו סוג).
        מחזיר (דירוג, הסבר): 0 עובד, 1 מעל היעד, 2 מעל התקציב, 3 "לא אוהב", 4 אלרגיה/תזונה, 5 חסר מחיר.
        """
        results = []
        for slot in self.slots:
            if slot[0] != recipe["category"] or recipe["name"] in menu[slot[0]]:
                continue
            candidate = self._replace(menu, slot, recipe["name"])
            cost = self.calculate_cost(candidate)
            violations = self.check_guests(candidate)["violations"]
            hard = [v for v in violations if v["type"] == "hard"]
            soft = [v for v in violations if v["type"] == "soft"]
            name = recipe["name"]
            # קצר וברור: "סלט חסה עם אגוזי מלך מכילה אגוזים (דנה)" - ולא הודעת ההפרה המלאה
            blocked = [(g.name, g.hard_conflicts(recipe)) for g in self.request.guests if g.hard_conflicts(recipe)]
            disliked = [(g.name, g.disliked_ingredients(recipe)) for g in self.request.guests
                        if g.disliked_ingredients(recipe)]
            if not cost["complete"]:
                results.append((5, f"{name}: אין מחיר ל{cost['missing_prices'][0]['ingredient']}"))
            elif hard and blocked:
                results.append((4, f"{name} {blocked[0][1][0]} ({blocked[0][0]})"))
            elif hard and hard[0]["guest"] == "כשרות":
                other = "dairy" if kashrut(recipe) == "meat" else "meat"
                results.append((4, f"{name} היא מנה {KASHRUT[kashrut(recipe)]}ת, ובתפריט יש מנה {KASHRUT[other]}ת "
                                   f"(ארוחה כשרה)"))
            elif hard:
                results.append((4, f"{name}: {hard[0]['problem']} ({hard[0]['guest']})"))
            elif soft and disliked:
                results.append((3, f"ב{name} יש {', '.join(disliked[0][1])}, ו{disliked[0][0]} לא אוהב/ת"))
            elif soft:
                results.append((3, f"{name}: {soft[0]['problem']} ({soft[0]['guest']})"))
            elif not cost["within_budget"]:
                results.append((2, f"{name}: התפריט היה עולה {cost['total_cost']:.0f} ש\"ח - מעל התקציב"))
            elif cost.get("within_target") is False:
                results.append((1, f"{name}: התפריט היה עולה {cost['total_cost']:.0f} ש\"ח - מעל היעד"))
            else:
                results.append((0, f"עם {name} התפריט היה עולה {cost['total_cost']:.0f} ש\"ח"))
        if not results and recipe["category"] not in self.request.courses:
            return 6, f"{recipe['name']} היא {CATEGORIES[recipe['category']]}, ואין {CATEGORIES[recipe['category']]} בתפריט שביקשו"
        return min(results) if results else (6, f"{recipe['name']}: אין מקום בתפריט")

    # --- כלי 4 ---
    def finalize_menu(self, menu: dict) -> dict:
        """
        בודק את התפריט מחדש מההתחלה, ורק אם הכל תקין מאשר אותו ומפיק רשימת קניות.
        זה הכלי היחיד שיכול לסיים את המשימה בהצלחה - לא סומכים על המודל שיגיד "סיימתי".
        """
        cost = self.calculate_cost(menu)
        if "error" in cost:
            return {"approved": False, "reasons": cost["details"]}
        guests = self.check_guests(menu)

        reasons = []
        if not cost["complete"]:
            items = ", ".join(f"{m['ingredient']} ({m['dish']})" for m in cost["missing_prices"])
            reasons.append(f"חסרים מחירים: {items}")
        if not cost["within_budget"]:
            reasons.append(f"חריגה מהתקציב: {cost['total_cost']} ש\"ח מתוך {cost['budget']}")
        reasons += [f"{v['guest']}: {v['problem']}" for v in guests["violations"]]
        if reasons:
            return {"approved": False, "reasons": reasons}

        dishes, _ = self._parse_menu(menu)
        self.approved_menu = {
            "menu": menu,
            "total_cost": cost["total_cost"],
            "cost_per_guest": cost["cost_per_guest"],
            "shopping_list": self._shopping_list(dishes),
            **self.preferences_report(menu),
        }
        return {"approved": True, **self.approved_menu}

    # --- כלי 5 ---
    def suggest_swaps(self, menu: dict) -> dict:
        """
        לכל מנה בתפריט: אילו מנות מאותה קטגוריה אפשר לשים במקומה כך שהתפריט כולו
        יהיה תקין לכל האורחים, וכמה יעלה התפריט אחרי ההחלפה - מהזול ליקר.
        בלי הכלי הזה המודל מנחש מה להחליף, ומבזבז צעדים על ניסיונות שמייקרים או שוברים אילוץ.

        אם אין החלפה בודדת שמוזילה, נבדקת גם החלפה של שתי מנות יחד: חלק מהכללים מקשרים
        בין מנות (למשל "לכל היותר מנה אחת לא אהובה"), ואז רק שתי החלפות ביחד מוזילות בלי לשבור כלל.
        """
        dishes, errors = self._parse_menu(menu)
        if errors:
            return {"error": "התפריט לא תקין", "details": errors}

        current = self.calculate_cost(menu)
        alternatives = {slot: self._alternatives(menu, slot) for slot in self.slots}
        suggestions = []
        for slot in self.slots:
            options = []
            for dish in alternatives[slot]:
                total = self._valid_total(self._replace(menu, slot, dish))
                if total is not None:
                    options.append({"dish": dish, "new_total": total,
                                    "saving": round(current["total_cost"] - total, 2)})
            options.sort(key=lambda o: o["new_total"])
            replaced = self._dish_at(menu, slot)
            suggestions.append({"replace": replaced, "category": slot[0],
                                "options": options[:3]})

        cheaper = [(o, s["replace"]) for s in suggestions for o in s["options"] if o["saving"] > 0]
        result = {"current_total": current["total_cost"], "current_complete": current["complete"],
                  "budget": self.request.budget, "suggestions": suggestions}
        if cheaper:
            best, replaced = max(cheaper, key=lambda pair: pair[0]["saving"])
            result["best_swap"] = {"replace": replaced, "with": best["dish"],
                                   "saving": best["saving"], "new_total": best["new_total"]}
            return result

        double = self._best_double_swap(menu, current["total_cost"], alternatives)
        if double:
            result["best_double_swap"] = double
            result["note"] = "אין החלפה של מנה אחת שמוזילה, אבל החלפה של שתי מנות יחד כן."
        else:
            result["note"] = ("אין החלפה של מנה אחת או של שתי מנות שמוזילה את התפריט ושומרת על כל "
                              "האורחים. כנראה שזה התפריט הזול ביותר שאפשר להגיע אליו מכאן.")
        return result

    # ---------- מתכונים (למשתמש - לא כלי של המודל) ----------

    def recipe_card(self, dish: str) -> dict:
        """מתכון מוכן לבישול. הכמויות מחושבות בקוד לפי מספר הסועדים, ולא נוחשות."""
        recipe = self.recipes.get(dish)
        if recipe is None:
            return {"error": f"אין מתכון בשם '{dish}'"}
        servings = self._servings(recipe)
        return {
            "name": dish,
            "category": recipe["category"],
            "time_minutes": recipe.get("time_minutes"),
            "num_guests": self.request.num_guests,
            "portion": self._portion(recipe),  # פחות ממנה שלמה כשיש כמה מנות מאותו סוג
            "same_kind": self.request.courses.get(recipe["category"], 1),
            "ingredients": [{"name": ing["name"], "amount": ing["amount"] * servings, "unit": ing["unit"]}
                            for ing in recipe["ingredients"]],
            "steps": recipe.get("steps", []),
        }

    # ---------- פונקציות עזר פנימיות (לא חשופות למודל) ----------

    def _best_double_swap(self, menu: dict, current_total: float, alternatives: dict) -> dict | None:
        best = None
        for slot_a, slot_b in combinations(self.slots, 2):
            for dish_a, dish_b in product(alternatives[slot_a], alternatives[slot_b]):
                total = self._valid_total(self._replace(self._replace(menu, slot_a, dish_a), slot_b, dish_b))
                saving = total is not None and round(current_total - total, 2)
                if saving and saving > 0 and (best is None or total < best["new_total"]):
                    best = {"replace": [self._dish_at(menu, slot_a), self._dish_at(menu, slot_b)],
                            "with": [dish_a, dish_b], "saving": saving, "new_total": total}
        return best

    def _alternatives(self, menu: dict, slot: tuple) -> list[str]:
        """מנות מאותו סוג שאינן כבר בתפריט."""
        in_menu = set(menu_dishes(menu))
        return [n for n, r in self.recipes.items() if r["category"] == slot[0] and n not in in_menu]

    def _valid_total(self, menu: dict) -> float | None:
        """העלות של תפריט אם הוא תקין לגמרי (מבנה, מחירים, אורחים), אחרת None."""
        cost = self.calculate_cost(menu)
        if "error" in cost or not cost["complete"] or not self.check_guests(menu)["all_satisfied"]:
            return None
        return cost["total_cost"]

    @staticmethod
    def _dish_at(menu: dict, slot: tuple) -> str:
        category, index = slot
        return menu[category][index]

    @staticmethod
    def _replace(menu: dict, slot: tuple, dish: str) -> dict:
        """עותק של התפריט עם מנה אחת מוחלפת. לא משנה את המקור."""
        category, index = slot
        dishes = list(menu[category])
        dishes[index] = dish
        return dict(menu, **{category: dishes})

    def dish_cost(self, dish: str) -> tuple[float, list[str]]:
        """עלות מנה אחת לכל הסועדים (מעוגלת לאגורה), ומרכיבים בלי מחיר. כל חישוב עלות עובר דרך כאן."""
        recipe = self.recipes[dish]
        servings = self._servings(recipe)
        cost, missing = 0.0, []
        for ing in recipe["ingredients"]:
            item = self.prices.get(ing["name"])
            if item is None:
                missing.append(ing["name"])
                continue
            cost += ing["amount"] * servings / item["package_size"] * item["price"]
        return round(cost, 2), missing

    def _servings(self, recipe: dict) -> float:
        return self.request.num_guests * self._portion(recipe)

    def _portion(self, recipe: dict) -> float:
        return portion(self.request.courses.get(recipe["category"], 1))

    def _parse_menu(self, menu) -> tuple[list[dict], list[str]]:
        """הופך תפריט (שמות מנות) לרשימת מתכונים, ומחזיר הסבר על כל בעיה במבנה."""
        courses = self.request.courses
        if not isinstance(menu, dict):
            return [], [f"התפריט צריך להיות אובייקט עם רשימת מנות לכל סוג: {', '.join(courses)}"]

        errors = []
        for key, value in menu.items():
            if key not in courses and value:  # רשימה ריקה לסוג שלא ביקשו - בסדר
                errors.append(f"'{key}' לא בתפריט שביקשו ({describe_courses(courses)}). "
                              f"השדות: {', '.join(courses)}")

        dishes = []
        for category, count in courses.items():
            names = menu.get(category)
            if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
                errors.append(f"חסרה רשימת מנות מסוג {CATEGORIES[category]} "
                              f"(השדה {category}: רשימה של {count} מנות)")
                continue
            if len(names) != count:
                errors.append(f"צריך בדיוק {count} מנות מסוג {CATEGORIES[category]} ברשימה {category}, "
                              f"ויש {len(names)}")
            if len(set(names)) < len(names):
                errors.append(f"מנה כפולה ברשימה {category} - צריך מנות שונות")
            for name in names:
                recipe = self.recipes.get(name)
                if recipe is None:
                    close = difflib.get_close_matches(name, self.recipes, n=1, cutoff=0.6)
                    hint = f" אולי התכוונת ל-'{close[0]}'?" if close else ""
                    errors.append(f"המנה '{name}' לא קיימת במאגר.{hint}")
                elif recipe["category"] != category:
                    errors.append(f"'{name}' היא {CATEGORIES[recipe['category']]}, "
                                  f"לא {CATEGORIES[category]}")
                else:
                    dishes.append(recipe)
        return dishes, errors

    def _shopping_list(self, dishes: list[dict]) -> list[dict]:
        """מאחד מרכיבים משותפים (בצל מכמה מנות = שורה אחת) ומעגל למעלה לאריזות שלמות."""
        totals: dict[str, float] = {}
        for r in dishes:
            servings = self._servings(r)
            for ing in r["ingredients"]:
                totals[ing["name"]] = totals.get(ing["name"], 0) + ing["amount"] * servings

        shopping = []
        for name, amount in sorted(totals.items()):
            item = self.prices[name]
            packages = math.ceil(round(amount / item["package_size"], 6))
            shopping.append({
                "item": name,
                "needed": round(amount, 1),
                "unit": UNITS[item["unit"]],
                "packages": packages,
                "package_size": item["package_size"],
                "packages_cost": round(packages * item["price"], 2),
            })
        return shopping
