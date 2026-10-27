"""
בדיקות מקיפות לכלים: כל אלרגיה, כל תזונה, ואלפי מקרים אקראיים.

הרעיון: "אורקל" - מימוש נפרד ופשוט של הכללים, שנכתב רק לבדיקה ולא משתף קוד עם tools.py.
על כל מקרה אקראי, הכלים חייבים להסכים עם האורקל. אם יש באג בכלים, הם לא יסכימו.
"""
import random
import sys
import unittest
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from evaluate import find_best_menu  # noqa: E402
from tools import ALLERGENS, DATA_DIR, DIETS, MENU_SLOTS, MenuRequest, MenuTools, load_json  # noqa: E402

RECIPES = load_json(DATA_DIR / "recipes.json")
PRICES = load_json(DATA_DIR / "prices.json")
BY_NAME = {r["name"]: r for r in RECIPES}
BY_CATEGORY = {c: [r["name"] for r in RECIPES if r["category"] == c] for c in ("main", "side", "vegetable", "dessert")}

# סיווג של כל מרכיב במאגר. מרכיב חדש שלא מופיע כאן יכשיל את הבדיקה - חייבים לסווג אותו במפורש
INGREDIENT_ALLERGENS = {
    "קמח": {"gluten"}, "פסטה": {"gluten"}, "פתיתים": {"gluten"}, "קוסקוס": {"gluten"},
    # מוצרים קנויים - מסומנים גם לפי "עלול להכיל" (שמרני: עדיף להחמיר באלרגיות)
    "פירורי לחם": {"gluten", "sesame", "soy"}, "שוקולד מריר": {"soy"},
    "ביצים": {"eggs"},
    "חמאה": {"dairy"}, "חלב": {"dairy"}, "שמנת לבישול": {"dairy"}, "פרמזן": {"dairy"}, "גבינה בולגרית": {"dairy"},
    "גבינה לבנה": {"dairy"}, "גבינה צהובה": {"dairy"}, "מוצרלה": {"dairy"},
    "שקדים": {"nuts"}, "אגוזי מלך": {"nuts"},
    "טחינה גולמית": {"sesame"},
    "פילה סלמון": {"fish"}, "פילה אמנון": {"fish"},
}
MEAT = {"שוקי עוף", "חזה עוף", "בשר בקר טחון", "פרגיות", "בשר בקר לבישול"}
FISH = {"פילה סלמון", "פילה אמנון"}
NOT_VEGETARIAN = MEAT | FISH
SAFE = {
    "תפוחי אדמה", "בטטה", "בצל", "שום", "עגבניות", "מלפפונים", "פלפל אדום", "קישואים", "פטריות שמפיניון",
    "חסה", "לימון", "פטרוזיליה", "שמיר", "רוזמרין", "תפוחים", "בננות", "תפוזים", "אבטיח", "אורז",
    "עדשים כתומות", "עדשים ירוקות", "סוכר", "חלב קוקוס", "רסק עגבניות", "זיתים",
    "שמן זית", "שמן קנולה", "פפריקה", "אבקת קארי", "כמהין",
    "חצילים", "תרד", "קינואה", "כרוב", "גזר", "עמילן תירס", "בזיליקום",
}
LIKE_POOL = ["חסה", "שקשוקה", "סלמון", "עוף", "שוקולד", "אבטיח", "פסטה", "עדשים", "פטריות"]
DISLIKE_POOL = ["עגבניות", "פטריות", "עדשים", "שום", "בצל", "תפוחי אדמה", "אורז", "עוף", "בקר", "סלמון",
                "ביצים", "שוקולד", "אבטיח", "מלפפונים", "פלפל", "קישואים", "תפוזים", "חלב", "שקדים", "כוסברה"]


# ---------- האורקל: הכללים, כתובים מחדש בצורה הכי פשוטה ----------

def oracle_can_eat(guest: dict, dish: str) -> bool:
    recipe = BY_NAME[dish]
    if set(guest.get("allergies", [])) & set(recipe["allergens"]):
        return False
    return guest.get("diet") is None or guest["diet"] in recipe["diet"]


def oracle_dislikes(guest: dict, dish: str) -> bool:
    return any(d in ing["name"] for d in guest.get("dislikes", []) for ing in BY_NAME[dish]["ingredients"])


def oracle_kosher_ok(menu: dict) -> bool:
    """כשרות, ישירות מהמרכיבים: אסור שיהיו גם בשר וגם חלב (דגים ופרווה - מותר)."""
    ingredients = {i["name"] for d in menu["mains"] + [menu["side"], menu["vegetable"], menu["dessert"]]
                   for i in BY_NAME[d]["ingredients"]}
    has_dairy = any("dairy" in INGREDIENT_ALLERGENS.get(i, set()) for i in ingredients)
    return not (ingredients & MEAT and has_dairy)


def oracle_guests_ok(guests: list, menu: dict) -> bool:
    others = [menu["side"], menu["vegetable"], menu["dessert"]]
    for g in guests:
        edible_mains = [m for m in menu["mains"] if oracle_can_eat(g, m)]
        if not edible_mains:
            return False
        if all(oracle_dislikes(g, m) for m in edible_mains):
            return False
        if not all(oracle_can_eat(g, d) for d in others):
            return False
        if sum(oracle_dislikes(g, d) for d in others) > 1:
            return False
    return True


def oracle_cost(num_guests: int, menu: dict) -> float | None:
    """None = יש מרכיב בלי מחיר."""
    total = 0.0
    for dish in menu["mains"] + [menu["side"], menu["vegetable"], menu["dessert"]]:
        servings = num_guests * (0.6 if dish in menu["mains"] else 1)
        for ing in BY_NAME[dish]["ingredients"]:
            if ing["name"] not in PRICES:
                return None
            item = PRICES[ing["name"]]
            total += ing["amount"] * servings / item["package_size"] * item["price"]
    return total


def oracle_valid_total(request: dict, menu: dict) -> float | None:
    if menu["mains"][0] == menu["mains"][1]:
        return None
    cost = oracle_cost(request["num_guests"], menu)
    if cost is None or not oracle_guests_ok(request["guests"], menu):
        return None
    if request.get("kosher") and not oracle_kosher_ok(menu):
        return None
    return cost


# ---------- מחוללים אקראיים ----------

def random_guest(rng: random.Random, i: int) -> dict:
    # likes נוצרים כאן, אבל האורקל מתעלם מהם - כך הבדיקה מוכיחה שהעדפה רכה לא משנה אף החלטה
    guest = {"name": f"אורח{i}",
             "allergies": rng.sample(list(ALLERGENS), rng.choice([0, 0, 1, 1, 2, 3])),
             "dislikes": rng.sample(DISLIKE_POOL, rng.choice([0, 1, 2, 3])),
             "likes": rng.sample(LIKE_POOL, rng.choice([0, 0, 1, 2]))}
    if rng.random() < 0.3:
        guest["diet"] = rng.choice(list(DIETS))
    return guest


def random_request(rng: random.Random) -> dict:
    return {"num_guests": rng.randint(5, 30), "budget": rng.uniform(80, 400), "kosher": rng.random() < 0.3,
            "guests": [random_guest(rng, i) for i in range(rng.randint(0, 5))]}


def random_menu(rng: random.Random) -> dict:
    return {"mains": rng.sample(BY_CATEGORY["main"], 2), "side": rng.choice(BY_CATEGORY["side"]),
            "vegetable": rng.choice(BY_CATEGORY["vegetable"]), "dessert": rng.choice(BY_CATEGORY["dessert"])}


def tools_for(request: dict) -> MenuTools:
    return MenuTools(MenuRequest.from_dict(request))


# ---------- הבדיקות ----------

class TestAllergenData(unittest.TestCase):
    """התגיות במאגר חייבות להתאים למרכיבים בפועל - זו בטיחות, לא רק נוחות."""

    def test_every_ingredient_is_classified(self):
        ingredients = {i["name"] for r in RECIPES for i in r["ingredients"]} | set(PRICES)
        unclassified = ingredients - set(INGREDIENT_ALLERGENS) - NOT_VEGETARIAN - SAFE
        self.assertFalse(unclassified, f"מרכיבים חדשים - צריך לסווג אותם: {unclassified}")

    def test_allergen_tags_match_ingredients(self):
        for r in RECIPES:
            expected = set().union(*(INGREDIENT_ALLERGENS.get(i["name"], set()) for i in r["ingredients"]))
            self.assertEqual(set(r["allergens"]), expected, r["name"])

    def test_kashrut_is_derived_correctly(self):
        from tools import kashrut
        for r in RECIPES:
            names = {i["name"] for i in r["ingredients"]}
            has_dairy = any("dairy" in INGREDIENT_ALLERGENS.get(n, set()) for n in names)
            with self.subTest(r["name"]):
                self.assertFalse(names & MEAT and has_dairy, "מנה עם בשר וחלב יחד")
                expected = "dairy" if has_dairy else "meat" if names & MEAT else "parve"
                self.assertEqual(kashrut(r), expected)

    def test_diet_tags_match_ingredients(self):
        for r in RECIPES:
            names = {i["name"] for i in r["ingredients"]}
            vegetarian = not (names & NOT_VEGETARIAN)
            vegan = vegetarian and not ({"eggs", "dairy"} & set(r["allergens"]))
            self.assertEqual("vegetarian" in r["diet"], vegetarian, r["name"])
            self.assertEqual("vegan" in r["diet"], vegan, r["name"])


class TestEveryAllergyAndDiet(unittest.TestCase):
    """כל אלרגיה וכל תזונה, מול כל מנה במאגר."""

    def test_each_restriction_against_each_dish(self):
        base = {"mains": ["קארי עדשים בחלב קוקוס", "עוף בתנור עם תפוחי אדמה"], "side": "מג'דרה",
                "vegetable": "סלט ירקות קצוץ", "dessert": "אבטיח קר"}  # מנות בלי אף אלרגן
        restrictions = [{"allergies": [a]} for a in ALLERGENS] + [{"diet": d} for d in DIETS]
        for restriction in restrictions:
            guest = {"name": "בודק", **restriction}
            tools = tools_for({"num_guests": 10, "budget": 1000, "guests": [guest]})
            for recipe in RECIPES:
                if recipe["category"] == "main":
                    continue  # עיקרית אחת מספיקה - נבדק בנפרד
                menu = dict(base, **{recipe["category"]: recipe["name"]})
                with self.subTest(restriction=restriction, dish=recipe["name"]):
                    hard = [v for v in tools.check_guests(menu)["violations"] if v["type"] == "hard"]
                    self.assertEqual(bool(hard), not oracle_can_eat(guest, recipe["name"]))

    def test_search_filters_each_allergen_and_diet(self):
        tools = tools_for({"num_guests": 10, "budget": 1000})
        for category, names in BY_CATEGORY.items():
            for allergen in ALLERGENS:
                found = {r["name"] for r in tools.search_recipes(category, exclude_allergens=[allergen])["recipes"]}
                self.assertEqual(found, {n for n in names if allergen not in BY_NAME[n]["allergens"]})
            for diet in DIETS:
                found = {r["name"] for r in tools.search_recipes(category, diet=diet)["recipes"]}
                self.assertEqual(found, {n for n in names if diet in BY_NAME[n]["diet"]})

    def test_one_guest_allergic_to_everything(self):
        guest = {"name": "רגיש", "allergies": list(ALLERGENS), "diet": "vegan"}
        tools = tools_for({"num_guests": 10, "budget": 1000, "guests": [guest]})
        truth = find_best_menu(tools)
        self.assertTrue(truth["solvable"])
        self.assertTrue(tools.finalize_menu(truth["best"]["menu"])["approved"])


class TestToolsAgreeWithOracle(unittest.TestCase):
    """אלפי בקשות ותפריטים אקראיים - הכלים חייבים להסכים עם האורקל בכל אחד."""

    def test_finalize_and_check_guests(self):
        rng = random.Random(42)
        approved_count = 0
        for _ in range(3000):
            request, menu = random_request(rng), random_menu(rng)
            tools = tools_for(request)
            with self.subTest(request=request, menu=menu):
                guests_ok = oracle_guests_ok(request["guests"], menu) and (
                    not request["kosher"] or oracle_kosher_ok(menu))
                self.assertEqual(tools.check_guests(menu)["all_satisfied"], guests_ok)

                cost = oracle_cost(request["num_guests"], menu)
                result = tools.calculate_cost(menu)
                self.assertEqual(result["complete"], cost is not None)
                if cost is not None:
                    self.assertAlmostEqual(result["total_cost"], cost, places=1)

                should_approve = cost is not None and guests_ok and round(cost, 2) <= request["budget"]
                approved = tools.finalize_menu(menu)["approved"]
                self.assertEqual(approved, should_approve)
                approved_count += approved

                # דיווח ה"אוהב/ת" - מול מימוש פשוט: השם מופיע בשם מנה או במרכיב
                dishes = menu["mains"] + [menu["side"], menu["vegetable"], menu["dessert"]]
                expected_likes = [any(like in n for d in dishes for n in [d] + [i["name"] for i in BY_NAME[d]["ingredients"]])
                                  for g in request["guests"] for like in g["likes"]]
                reported = [x["in_menu"] for x in tools.check_guests(menu).get("likes", [])]
                self.assertEqual(reported, expected_likes)
        self.assertGreater(approved_count, 100, "המחולל צריך לייצר מספיק תפריטים תקינים כדי שהבדיקה תהיה משמעותית")

    def test_suggest_swaps(self):
        rng = random.Random(7)
        checked = 0
        while checked < 150:
            request = random_request(rng)
            menu = random_menu(rng)
            current = oracle_cost(request["num_guests"], menu)
            if current is None:
                continue
            checked += 1
            result = tools_for(request).suggest_swaps(menu)
            with self.subTest(request=request, menu=menu):
                # כל הצעה תקינה, והעלות שלה נכונה
                for suggestion in result["suggestions"]:
                    slot = next(s for s in MENU_SLOTS if _dish_at(menu, s) == suggestion["replace"])
                    for option in suggestion["options"]:
                        total = oracle_valid_total(request, _replace(menu, slot, option["dish"]))
                        self.assertIsNotNone(total, option)
                        self.assertAlmostEqual(option["new_total"], total, places=1)

                best_single = _best_oracle_saving(request, menu, current, 1)
                best_double = _best_oracle_saving(request, menu, current, 2)
                if best_single > 0.005:
                    self.assertAlmostEqual(result["best_swap"]["saving"], best_single, places=1)
                else:
                    self.assertNotIn("best_swap", result)
                    if best_double > 0.005:
                        self.assertAlmostEqual(result["best_double_swap"]["saving"], best_double, places=1)
                        double_menu = menu
                        for old, new in zip(result["best_double_swap"]["replace"], result["best_double_swap"]["with"]):
                            double_menu = _replace(double_menu, next(s for s in MENU_SLOTS if _dish_at(menu, s) == old), new)
                        self.assertIsNotNone(oracle_valid_total(request, double_menu))
                    else:
                        self.assertNotIn("best_double_swap", result)


class TestSolverAgreesWithOracle(unittest.TestCase):

    def test_cheapest_menu(self):
        rng = random.Random(3)
        for _ in range(8):  # כ-59,000 תפריטים לכל בקשה - האורקל איטי, אז פחות בקשות
            request = random_request(rng)
            totals = [t for t in (oracle_valid_total(request, m) for m in _all_menus()) if t is not None]
            truth = find_best_menu(tools_for(request))
            with self.subTest(request=request):
                self.assertEqual(truth["valid_menus"], len(totals))
                if totals:
                    self.assertAlmostEqual(truth["best"]["total_cost"], min(totals), places=1)
                    self.assertEqual(truth["solvable"], round(min(totals), 2) <= request["budget"])
                else:
                    self.assertIsNone(truth["best"])


class TestInputEdgeCases(unittest.TestCase):

    def test_bad_requests_are_rejected(self):
        bad = [
            {"num_guests": 5, "budget": float("inf")},
            {"num_guests": 5, "budget": float("nan")},
            {"num_guests": 5, "budget": True},
            {"num_guests": 2.5, "budget": 100},
            {"num_guests": 5, "budget": 100, "guests": "דנה"},
            {"num_guests": 5, "budget": 100, "guests": [{"name": "   "}]},
            {"num_guests": 5, "budget": 100, "guests": [{"name": 7}]},
            {"num_guests": 5, "budget": 100, "guests": [{"name": "דנה", "allergies": "nuts"}]},
            {"num_guests": 5, "budget": 100, "guests": [{"name": "דנה", "dislikes": "בצל"}]},
            {"num_guests": 5, "budget": 100, "guests": [{"name": "דנה", "dislikes": [None]}]},
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                MenuRequest.from_dict(data)

    def test_empty_dislike_does_not_match_everything(self):
        request = MenuRequest.from_dict({"num_guests": 5, "budget": 100,
                                         "guests": [{"name": "דנה", "dislikes": ["", "  "]}]})
        self.assertEqual(request.guests[0].dislikes, [])

    def test_huge_party(self):
        tools = tools_for({"num_guests": 1000, "budget": 20000, "guests": []})
        truth = find_best_menu(tools)
        self.assertTrue(tools.finalize_menu(truth["best"]["menu"])["approved"])


# ---------- עזרים ----------

def _dish_at(menu, slot):
    return menu[slot[0]] if slot[1] is None else menu[slot[0]][slot[1]]


def _replace(menu, slot, dish):
    new = dict(menu, mains=list(menu["mains"]))
    if slot[1] is None:
        new[slot[0]] = dish
    else:
        new["mains"][slot[1]] = dish
    return new


def _best_oracle_saving(request, menu, current, k):
    """החיסכון הגדול ביותר שאפשר להשיג בהחלפה של בדיוק k מנות (0 אם אין)."""
    in_menu = set(menu["mains"]) | {menu["side"], menu["vegetable"], menu["dessert"]}
    best = 0.0
    for slots in combinations(MENU_SLOTS, k):
        def expand(i, m):
            nonlocal best
            if i == len(slots):
                total = oracle_valid_total(request, m)
                if total is not None:
                    best = max(best, round(current - total, 2))
                return
            category = BY_NAME[_dish_at(menu, slots[i])]["category"]
            for dish in BY_CATEGORY[category]:
                if dish not in in_menu:
                    expand(i + 1, _replace(m, slots[i], dish))
        expand(0, menu)
    return best


def _all_menus():
    for mains in combinations(BY_CATEGORY["main"], 2):
        for side in BY_CATEGORY["side"]:
            for vegetable in BY_CATEGORY["vegetable"]:
                for dessert in BY_CATEGORY["dessert"]:
                    yield {"mains": list(mains), "side": side, "vegetable": vegetable, "dessert": dessert}


if __name__ == "__main__":
    unittest.main()
