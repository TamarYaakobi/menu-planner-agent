"""
בדיקות לכלים - בלי AI ובלי אינטרנט.
הרצה: python -m unittest discover tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import (ALLERGENS, CATEGORIES, DATA_DIR, DIETS, DataError,  # noqa: E402
                   MenuRequest, MenuTools, load_json)

VALID_MENU = {
    "mains": ["עוף בתנור עם תפוחי אדמה", "קארי עדשים בחלב קוקוס"],
    "side": "מג'דרה",
    "vegetable": "סלט ירקות קצוץ",
    "dessert": "סלט פירות",
}


def make_tools(num_guests=10, budget=1000, guests=None):
    request = MenuRequest.from_dict({"num_guests": num_guests, "budget": budget, "guests": guests or []})
    return MenuTools(request)


class TestData(unittest.TestCase):
    """בודק שקבצי הנתונים עקביים - טעות בהם תפגע בכל ריצה."""

    def setUp(self):
        self.recipes = load_json(DATA_DIR / "recipes.json")
        self.prices = load_json(DATA_DIR / "prices.json")

    def test_every_category_has_options(self):
        for category in CATEGORIES:
            count = sum(r["category"] == category for r in self.recipes)
            self.assertGreaterEqual(count, 3, category)

    def test_tags_are_known(self):
        for r in self.recipes:
            self.assertTrue(set(r["allergens"]) <= set(ALLERGENS), r["name"])
            self.assertTrue(set(r["diet"]) <= set(DIETS), r["name"])
            if "vegan" in r["diet"]:
                self.assertIn("vegetarian", r["diet"], f"{r['name']}: טבעוני חייב להיות גם צמחוני")

    def test_only_truffle_is_missing_from_catalog(self):
        # כמהין חסר בכוונה, כדי לבדוק שהאג'נט יודע להתמודד עם מחיר שלא נמצא
        missing = {i["name"] for r in self.recipes for i in r["ingredients"]} - set(self.prices)
        self.assertEqual(missing, {"כמהין"})


class TestRequest(unittest.TestCase):

    def test_rejects_bad_values(self):
        bad_requests = [
            {"num_guests": 0, "budget": 100},
            {"num_guests": 5, "budget": -1},
            {"num_guests": 5, "budget": 100, "guests": [{"name": "דנה", "allergies": ["פיצה"]}]},
            {"num_guests": 5, "budget": 100, "guests": [{"name": "דנה", "diet": "קטו"}]},
            {"num_guests": 1, "budget": 100, "guests": [{"name": "א"}, {"name": "ב"}]},
        ]
        for data in bad_requests:
            with self.assertRaises(ValueError, msg=data):
                MenuRequest.from_dict(data)

    def test_unit_mismatch_is_a_data_error(self):
        request = MenuRequest.from_dict({"num_guests": 2, "budget": 100})
        recipes = [{"name": "x", "category": "main", "allergens": [], "diet": [],
                    "ingredients": [{"name": "חלב", "amount": 1, "unit": "g"}]}]
        with self.assertRaises(DataError):
            MenuTools(request, recipes=recipes, prices={"חלב": {"price": 7, "package_size": 1000, "unit": "ml"}})


class TestSearchRecipes(unittest.TestCase):

    def setUp(self):
        self.tools = make_tools()

    def test_filters(self):
        result = self.tools.search_recipes("dessert", exclude_allergens=["nuts"], diet="vegan")
        self.assertGreater(result["count"], 0)
        for r in result["recipes"]:
            self.assertNotIn("nuts", r["allergens"])
            self.assertIn("vegan", r["diet"])

    def test_exclude_ingredient_matches_partial_name(self):
        result = self.tools.search_recipes("vegetable", exclude_ingredients=["פטריות"])
        names = [r["name"] for r in result["recipes"]]
        self.assertNotIn("פטריות מוקפצות עם שום", names)

    def test_bad_input_returns_error_instead_of_crashing(self):
        self.assertIn("error", self.tools.search_recipes("soup"))
        self.assertIn("error", self.tools.search_recipes("main", exclude_allergens=["פיצה"]))

    def test_no_results_has_note(self):
        result = self.tools.search_recipes("main", diet="vegan", exclude_ingredients=["עדשים", "עגבניות"])
        self.assertEqual(result["count"], 0)
        self.assertIn("note", result)


class TestCalculateCost(unittest.TestCase):

    def test_cost_from_ingredients(self):
        # מאגר זעיר שאפשר לחשב בראש: 100 גרם x 10 אורחים = 1 ק"ג = 20 ש"ח
        request = MenuRequest.from_dict({"num_guests": 10, "budget": 100})
        prices = {"קמח": {"price": 20, "package_size": 1000, "unit": "g"}}
        recipes = [
            {"name": n, "category": c, "allergens": [], "diet": [],
             "ingredients": [{"name": "קמח", "amount": 100, "unit": "g"}]}
            for n, c in [("m1", "main"), ("m2", "main"), ("s", "side"), ("v", "vegetable"), ("d", "dessert")]
        ]
        tools = MenuTools(request, recipes=recipes, prices=prices)
        result = tools.calculate_cost({"mains": ["m1", "m2"], "side": "s", "vegetable": "v", "dessert": "d"})
        # עיקריות: 60% מנה לכל אחת -> 12 ש"ח כל אחת. שאר המנות: 20 ש"ח כל אחת
        self.assertAlmostEqual(result["total_cost"], 12 + 12 + 20 * 3)
        self.assertTrue(result["within_budget"])

    def test_missing_price_is_reported(self):
        menu = dict(VALID_MENU, mains=["ריזוטו כמהין", "שקשוקה"])
        result = make_tools().calculate_cost(menu)
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing_prices"][0]["ingredient"], "כמהין")

    def test_over_budget(self):
        result = make_tools(budget=50).calculate_cost(VALID_MENU)
        self.assertFalse(result["within_budget"])
        self.assertLess(result["difference"], 0)

    def test_invalid_menu_structure(self):
        tools = make_tools()
        self.assertIn("error", tools.calculate_cost({"mains": ["שקשוקה"]}))
        self.assertIn("error", tools.calculate_cost("not a dict"))
        self.assertIn("error", tools.calculate_cost(dict(VALID_MENU, side="סלט פירות")))  # קטגוריה שגויה
        self.assertIn("error", tools.calculate_cost(dict(VALID_MENU, mains=["שקשוקה", "שקשוקה"])))

    def test_typo_gets_suggestion(self):
        result = make_tools().calculate_cost(dict(VALID_MENU, dessert="סלט פרות"))
        self.assertIn("סלט פירות", result["details"][0])


class TestCheckGuests(unittest.TestCase):

    def test_valid_menu_satisfies_everyone(self):
        tools = make_tools(guests=[{"name": "יוסי", "diet": "vegetarian"},
                                   {"name": "דנה", "allergies": ["nuts"]}])
        self.assertTrue(tools.check_guests(VALID_MENU)["all_satisfied"])

    def test_vegetarian_needs_one_main(self):
        tools = make_tools(guests=[{"name": "יוסי", "diet": "vegetarian"}])
        menu = dict(VALID_MENU, mains=["עוף בתנור עם תפוחי אדמה", "שניצל עוף"])
        violations = tools.check_guests(menu)["violations"]
        self.assertEqual(violations[0]["type"], "hard")

    def test_allergy_in_side_dish_is_hard(self):
        tools = make_tools(guests=[{"name": "דנה", "allergies": ["nuts"]}])
        menu = dict(VALID_MENU, side="אורז עם שקדים קלויים")
        result = tools.check_guests(menu)
        self.assertEqual([v["type"] for v in result["violations"]], ["hard"])

    def test_one_disliked_side_is_ok_two_is_not(self):
        tools = make_tools(guests=[{"name": "מירב", "dislikes": ["פטריות", "עגבניות"]}])
        one = dict(VALID_MENU, vegetable="פטריות מוקפצות עם שום")
        self.assertTrue(tools.check_guests(one)["all_satisfied"])
        two = dict(one, side="פתיתים ברוטב עגבניות")
        self.assertEqual(tools.check_guests(two)["violations"][0]["type"], "soft")


class TestSuggestSwaps(unittest.TestCase):

    def test_every_option_is_valid_and_sorted(self):
        guests = [{"name": "יוסי", "diet": "vegetarian"}, {"name": "דנה", "allergies": ["nuts"]}]
        tools = make_tools(guests=guests)
        result = tools.suggest_swaps(VALID_MENU)
        self.assertEqual(len(result["suggestions"]), 5)
        for slot in result["suggestions"]:
            totals = [o["new_total"] for o in slot["options"]]
            self.assertEqual(totals, sorted(totals))
            for option in slot["options"]:
                self.assertNotEqual(option["dish"], slot["replace"])
                self.assertNotIn(option["dish"], VALID_MENU["mains"])  # בלי עיקרית כפולה
                self.assertNotIn("אגוז", option["dish"])                # דנה אלרגית
                self.assertNotEqual(option["dish"], "ריזוטו כמהין")     # אין לו מחיר מלא

    def test_best_swap_really_saves(self):
        tools = make_tools()
        result = tools.suggest_swaps(VALID_MENU)
        best = result["best_swap"]
        current = tools.calculate_cost(VALID_MENU)["total_cost"]
        self.assertAlmostEqual(current - best["saving"], best["new_total"])
        self.assertGreater(best["saving"], 0)

    def test_offers_fix_for_guest_violation(self):
        # שתי עיקריות בשריות - ליוסי אין מה לאכול. ההצעות צריכות לכלול עיקרית צמחונית
        tools = make_tools(guests=[{"name": "יוסי", "diet": "vegetarian"}])
        menu = dict(VALID_MENU, mains=["עוף בתנור עם תפוחי אדמה", "שניצל עוף"])
        main_options = [o["dish"] for s in tools.suggest_swaps(menu)["suggestions"]
                        if s["category"] == "main" for o in s["options"]]
        self.assertTrue(main_options)
        for dish in main_options:
            self.assertIn("vegetarian", tools.recipes[dish]["diet"])

    def test_cheapest_menu_has_no_cheaper_swap(self):
        # התפריט התקין הזול ביותר (לפי השופט) - אין החלפה שמוזילה אותו
        from evaluate import find_best_menu
        tools = make_tools(budget=50, guests=[{"name": "יוסי", "diet": "vegan"}])
        best_menu = find_best_menu(tools)["best"]["menu"]
        result = tools.suggest_swaps(best_menu)
        self.assertNotIn("best_swap", result)
        self.assertIn("note", result)

    def test_does_not_modify_the_menu(self):
        menu = {k: (list(v) if isinstance(v, list) else v) for k, v in VALID_MENU.items()}
        make_tools().suggest_swaps(menu)
        self.assertEqual(menu, VALID_MENU)


class TestKosher(unittest.TestCase):
    MEAT_AND_DAIRY = dict(VALID_MENU, dessert="עוגת גבינה")  # עוף בתנור (בשרי) + עוגת גבינה (חלבי)

    def kosher_tools(self, **request):
        return MenuTools(MenuRequest.from_dict({"num_guests": 10, "budget": 1000, "kosher": True, **request}))

    def test_meat_and_dairy_together_is_a_hard_violation(self):
        result = self.kosher_tools().check_guests(self.MEAT_AND_DAIRY)
        self.assertFalse(result["all_satisfied"])
        self.assertEqual(result["violations"][0]["guest"], "כשרות")
        self.assertIn("עוגת גבינה", result["violations"][0]["problem"])

    def test_only_when_requested(self):
        self.assertTrue(make_tools().check_guests(self.MEAT_AND_DAIRY)["all_satisfied"])

    def test_fish_and_parve_go_with_everything(self):
        menu = dict(VALID_MENU, mains=["פילה אמנון ברוטב עגבניות", "קארי עדשים בחלב קוקוס"], dessert="עוגת גבינה")
        self.assertTrue(self.kosher_tools().check_guests(menu)["all_satisfied"])

    def test_search_shows_kashrut(self):
        found = {r["name"]: r["kashrut"] for r in make_tools().search_recipes("dessert")["recipes"]}
        self.assertEqual((found["עוגת גבינה"], found["סלט פירות"]), ("dairy", "parve"))

    def test_swaps_and_finalize_respect_kosher(self):
        tools = self.kosher_tools()
        self.assertFalse(tools.finalize_menu(self.MEAT_AND_DAIRY)["approved"])
        swaps = tools.suggest_swaps(self.MEAT_AND_DAIRY)
        for slot in swaps["suggestions"]:
            for option in slot["options"]:
                self.assertTrue(tools.check_guests(_swap(self.MEAT_AND_DAIRY, slot["replace"], option["dish"]))
                                ["all_satisfied"])

    def test_invalid_flag(self):
        with self.assertRaises(ValueError):
            MenuRequest.from_dict({"num_guests": 5, "budget": 100, "kosher": "כן"})


def _swap(menu, old, new):
    swapped = dict(menu, mains=[new if m == old else m for m in menu["mains"]])
    for key in ("side", "vegetable", "dessert"):
        if menu[key] == old:
            swapped[key] = new
    return swapped


class TestPreferenceReasons(unittest.TestCase):
    """למה העדפה לא מומשה - נבדק בקוד, לא מנוחש."""

    def report(self, guests, menu=VALID_MENU, budget=1000, **request):
        tools = MenuTools(MenuRequest.from_dict({"num_guests": 8, "budget": budget, "guests": guests, **request}))
        return tools.preferences_report(menu)

    def like_reason(self, guests, **kwargs):
        return next(x for x in self.report(guests, **kwargs)["likes"] if not x["in_menu"])["reason"]

    def test_blocked_by_someone_elses_allergy(self):
        reason = self.like_reason([{"name": "דנה", "allergies": ["nuts"]}, {"name": "שירה", "likes": ["אגוזי מלך"]}])
        self.assertIn("מכילה אגוזים (דנה)", reason)

    def test_not_in_database(self):
        self.assertIn("אין במאגר", self.like_reason([{"name": "שירה", "likes": ["ארטישוק"]}]))

    def test_over_budget(self):
        cost = make_tools(num_guests=8).calculate_cost(VALID_MENU)["total_cost"]
        reason = self.like_reason([{"name": "גיל", "likes": ["סלמון"]}], budget=cost + 1)
        self.assertIn("מעל התקציב", reason)

    def test_kosher_conflict(self):
        reason = self.like_reason([{"name": "שירה", "likes": ["עוגת גבינה"]}], kosher=True)
        self.assertIn("מנה חלבית, ובתפריט יש מנה בשרית", reason)

    def test_honest_when_it_was_possible(self):
        self.assertIn("אפשר היה", self.like_reason([{"name": "שירה", "likes": ["שקשוקה"]}]))

    def test_compromise_on_a_dislike_is_explained(self):
        report = self.report([{"name": "מירב", "dislikes": ["עגבניות"]}])  # בסלט הקצוץ יש עגבניות
        self.assertEqual(report["compromises"][0]["dish"], "סלט ירקות קצוץ")
        self.assertTrue(report["compromises"][0]["reason"])

    def test_finalize_includes_reasons(self):
        tools = MenuTools(MenuRequest.from_dict({"num_guests": 8, "budget": 1000,
                                                 "guests": [{"name": "שירה", "likes": ["ארטישוק"]}]}))
        result = tools.finalize_menu(VALID_MENU)
        self.assertIn("reason", result["likes"][0])
        self.assertIn("compromises", result)


class TestTargetBudget(unittest.TestCase):
    """"בערך 400": היעד מדווח, אבל רק התקרה קובעת אם התפריט מאושר."""

    def test_target_is_reported(self):
        tools = MenuTools(MenuRequest.from_dict({"num_guests": 10, "budget": 450, "target_budget": 400}))
        result = tools.calculate_cost(VALID_MENU)
        self.assertEqual(result["target_budget"], 400)
        self.assertEqual(result["within_target"], result["total_cost"] <= 400)

    def test_over_target_under_ceiling_is_approved(self):
        cost = make_tools().calculate_cost(VALID_MENU)["total_cost"]
        tools = MenuTools(MenuRequest.from_dict({"num_guests": 10, "budget": cost + 10, "target_budget": cost - 10}))
        self.assertFalse(tools.calculate_cost(VALID_MENU)["within_target"])
        self.assertTrue(tools.finalize_menu(VALID_MENU)["approved"])

    def test_no_target_no_field(self):
        self.assertNotIn("within_target", make_tools().calculate_cost(VALID_MENU))

    def test_invalid_target(self):
        for target in (0, -5, 500, "בערך", float("nan")):
            with self.subTest(target=target), self.assertRaises(ValueError):
                MenuRequest.from_dict({"num_guests": 5, "budget": 450, "target_budget": target})


class TestLikes(unittest.TestCase):
    """'אוהב/ת' - העדפה רכה: מדווחת, אבל לעולם לא משנה אם התפריט תקין."""

    def test_like_matches_ingredient_or_dish_name(self):
        tools = make_tools(guests=[{"name": "דודה", "likes": ["חסה", "שקשוקה", "עוף"]}])
        menu = dict(VALID_MENU, vegetable="סלט חסה עם אגוזי מלך")
        report = {x["likes"]: x for x in tools.check_guests(menu)["likes"]}
        self.assertEqual(report["חסה"]["dishes"], ["סלט חסה עם אגוזי מלך"])  # מרכיב
        self.assertFalse(report["שקשוקה"]["in_menu"])
        self.assertTrue(report["עוף"]["in_menu"])                              # "עוף" בשם המנה ובמרכיב

    def test_like_never_breaks_validity(self):
        without = make_tools(guests=[{"name": "דנה", "allergies": ["nuts"]}])
        with_like = make_tools(guests=[{"name": "דנה", "allergies": ["nuts"], "likes": ["אגוזי מלך"]}])
        menu = dict(VALID_MENU, vegetable="סלט חסה עם אגוזי מלך")  # היא אוהבת - אבל אלרגית
        self.assertFalse(with_like.check_guests(menu)["all_satisfied"])
        self.assertEqual(with_like.finalize_menu(menu)["approved"], without.finalize_menu(menu)["approved"])

    def test_no_likes_no_report(self):
        self.assertNotIn("likes", make_tools().check_guests(VALID_MENU))

    def test_search_include_any(self):
        tools = make_tools()
        names = [r["name"] for r in tools.search_recipes("vegetable", include_any=["חסה"])["recipes"]]
        self.assertEqual(names, ["סלט חסה עם אגוזי מלך"])
        mains = [r["name"] for r in tools.search_recipes("main", include_any=["שקשוקה"])["recipes"]]
        self.assertEqual(mains, ["שקשוקה"])  # לפי שם המנה

    def test_finalize_reports_likes(self):
        tools = make_tools(guests=[{"name": "דודה", "likes": ["חסה"]}])
        result = tools.finalize_menu(dict(VALID_MENU, vegetable="סלט חסה עם אגוזי מלך"))
        self.assertTrue(result["likes"][0]["in_menu"])

    def test_empty_like_is_dropped(self):
        request = MenuRequest.from_dict({"num_guests": 5, "budget": 100,
                                         "guests": [{"name": "דודה", "likes": ["", " "]}]})
        self.assertEqual(request.guests[0].likes, [])


class TestFinalizeMenu(unittest.TestCase):

    def test_approves_valid_menu_with_shopping_list(self):
        tools = make_tools()
        result = tools.finalize_menu(VALID_MENU)
        self.assertTrue(result["approved"])
        self.assertIsNotNone(tools.approved_menu)
        onions = [i for i in result["shopping_list"] if i["item"] == "בצל"]
        self.assertEqual(len(onions), 1, "בצל מכמה מנות צריך להתאחד לשורה אחת")

    def test_rejects_and_explains(self):
        tools = make_tools(budget=50, guests=[{"name": "דנה", "allergies": ["nuts"]}])
        result = tools.finalize_menu(dict(VALID_MENU, side="אורז עם שקדים קלויים"))
        self.assertFalse(result["approved"])
        self.assertEqual(len(result["reasons"]), 2)  # תקציב + אלרגיה
        self.assertIsNone(tools.approved_menu)


if __name__ == "__main__":
    unittest.main()
