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
        result = self.tools.search_recipes("main", diet="vegan", exclude_ingredients=["עדשים"])
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
