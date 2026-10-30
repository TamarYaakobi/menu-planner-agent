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
    "main": ["עוף בתנור עם תפוחי אדמה", "קארי עדשים בחלב קוקוס"],
    "side": ["מג'דרה"],
    "vegetable": ["סלט ירקות קצוץ"],
    "dessert": ["סלט פירות"],
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
        result = tools.calculate_cost({"main": ["m1", "m2"], "side": ["s"], "vegetable": ["v"], "dessert": ["d"]})
        # עיקריות: 60% מנה לכל אחת -> 12 ש"ח כל אחת. שאר המנות: 20 ש"ח כל אחת
        self.assertAlmostEqual(result["total_cost"], 12 + 12 + 20 * 3)
        self.assertTrue(result["within_budget"])

    def test_missing_price_is_reported(self):
        menu = dict(VALID_MENU, main=["ריזוטו כמהין", "שקשוקה"])
        result = make_tools().calculate_cost(menu)
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing_prices"][0]["ingredient"], "כמהין")

    def test_over_budget(self):
        result = make_tools(budget=50).calculate_cost(VALID_MENU)
        self.assertFalse(result["within_budget"])
        self.assertLess(result["difference"], 0)

    def test_invalid_menu_structure(self):
        tools = make_tools()
        self.assertIn("error", tools.calculate_cost({"main": ["שקשוקה"]}))
        self.assertIn("error", tools.calculate_cost("not a dict"))
        self.assertIn("error", tools.calculate_cost(dict(VALID_MENU, side=["סלט פירות"])))  # קטגוריה שגויה
        self.assertIn("error", tools.calculate_cost(dict(VALID_MENU, main=["שקשוקה", "שקשוקה"])))
        self.assertIn("error", tools.calculate_cost(dict(VALID_MENU, side="מג'דרה")))  # מחרוזת במקום רשימה
        self.assertIn("error", tools.calculate_cost(dict(VALID_MENU, starter=["מרק עוף"])))  # לא ביקשו ראשונה
        self.assertNotIn("error", tools.calculate_cost(dict(VALID_MENU, starter=[])))

    def test_typo_gets_suggestion(self):
        result = make_tools().calculate_cost(dict(VALID_MENU, dessert=["סלט פרות"]))
        self.assertIn("סלט פירות", result["details"][0])


class TestCheckGuests(unittest.TestCase):

    def test_valid_menu_satisfies_everyone(self):
        tools = make_tools(guests=[{"name": "יוסי", "diet": "vegetarian"},
                                   {"name": "דנה", "allergies": ["nuts"]}])
        self.assertTrue(tools.check_guests(VALID_MENU)["all_satisfied"])

    def test_vegetarian_needs_one_main(self):
        tools = make_tools(guests=[{"name": "יוסי", "diet": "vegetarian"}])
        menu = dict(VALID_MENU, main=["עוף בתנור עם תפוחי אדמה", "שניצל עוף"])
        violations = tools.check_guests(menu)["violations"]
        self.assertEqual(violations[0]["type"], "hard")

    def test_allergy_in_side_dish_is_hard(self):
        tools = make_tools(guests=[{"name": "דנה", "allergies": ["nuts"]}])
        menu = dict(VALID_MENU, side=["אורז עם שקדים קלויים"])
        result = tools.check_guests(menu)
        self.assertEqual([v["type"] for v in result["violations"]], ["hard"])

    def test_one_disliked_side_is_ok_two_is_not(self):
        tools = make_tools(guests=[{"name": "מירב", "dislikes": ["פטריות", "עגבניות"]}])
        one = dict(VALID_MENU, vegetable=["פטריות מוקפצות עם שום"])
        self.assertTrue(tools.check_guests(one)["all_satisfied"])
        two = dict(one, side=["פתיתים ברוטב עגבניות"])
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
                self.assertNotIn(option["dish"], VALID_MENU["main"])  # בלי עיקרית כפולה
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
        menu = dict(VALID_MENU, main=["עוף בתנור עם תפוחי אדמה", "שניצל עוף"])
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
    MEAT_AND_DAIRY = dict(VALID_MENU, dessert=["עוגת גבינה"])  # עוף בתנור (בשרי) + עוגת גבינה (חלבי)

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
        menu = dict(VALID_MENU, main=["פילה אמנון ברוטב עגבניות", "קארי עדשים בחלב קוקוס"], dessert=["עוגת גבינה"])
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
    return {category: [new if d == old else d for d in dishes] for category, dishes in menu.items()}


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

    def test_guests_with_the_same_name_keep_their_own_reason(self):
        # השני היה דורס את הראשון, והראשון קיבל "אין במאגר" על מנה שיש במאגר
        likes = self.report([{"name": "אורח", "likes": ["שקשוקה"]}, {"name": "אורח", "likes": ["ארטישוק"]}])["likes"]
        self.assertIn("אפשר היה", likes[0]["reason"])
        self.assertIn("אין במאגר", likes[1]["reason"])

    def test_compromise_on_a_dislike_is_explained(self):
        report = self.report([{"name": "מירב", "dislikes": ["עגבניות"]}])  # בקארי ובסלט הקצוץ יש עגבניות
        by_dish = {c["dish"]: c for c in report["compromises"]}
        self.assertEqual(by_dish["קארי עדשים בחלב קוקוס"]["alternative"], ["עוף בתנור עם תפוחי אדמה"])
        self.assertEqual(by_dish["סלט ירקות קצוץ"]["alternative"], [])  # המנה האחת שמותר
        self.assertTrue(all(c["reason"] for c in report["compromises"]))

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
        menu = dict(VALID_MENU, vegetable=["סלט חסה עם אגוזי מלך"])
        report = {x["likes"]: x for x in tools.check_guests(menu)["likes"]}
        self.assertEqual(report["חסה"]["dishes"], ["סלט חסה עם אגוזי מלך"])  # מרכיב
        self.assertFalse(report["שקשוקה"]["in_menu"])
        self.assertTrue(report["עוף"]["in_menu"])                              # "עוף" בשם המנה ובמרכיב

    def test_like_never_breaks_validity(self):
        without = make_tools(guests=[{"name": "דנה", "allergies": ["nuts"]}])
        with_like = make_tools(guests=[{"name": "דנה", "allergies": ["nuts"], "likes": ["אגוזי מלך"]}])
        menu = dict(VALID_MENU, vegetable=["סלט חסה עם אגוזי מלך"])  # היא אוהבת - אבל אלרגית
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
        result = tools.finalize_menu(dict(VALID_MENU, vegetable=["סלט חסה עם אגוזי מלך"]))
        self.assertTrue(result["likes"][0]["in_menu"])

    def test_empty_like_is_dropped(self):
        request = MenuRequest.from_dict({"num_guests": 5, "budget": 100,
                                         "guests": [{"name": "דודה", "likes": ["", " "]}]})
        self.assertEqual(request.guests[0].likes, [])


class TestCourses(unittest.TestCase):
    """המשתמש בוחר כמה מנות יהיו מכל סוג - והכללים חלים על כל סוג לפי מה שנבחר."""
    MENU = {"starter": ["מרק עדשים כתומות"], "main": ["שניצל עוף"], "dessert": ["סלט פירות", "עוגת שוקולד"]}

    def tools(self, guests=(), courses=None, **request):
        courses = courses or {"starter": 1, "main": 1, "dessert": 2}
        return MenuTools(MenuRequest.from_dict({"num_guests": 10, "budget": 1000, "guests": list(guests),
                                                "courses": courses, **request}))

    def test_default_is_the_classic_menu(self):
        self.assertEqual(make_tools().request.courses, {"main": 2, "side": 1, "vegetable": 1, "dessert": 1})

    def test_bad_courses_are_rejected(self):
        for courses in ({"soup": 1}, {"main": 4}, {"main": -1}, {"main": 1.5}, {"main": True}, {"main": 0}, {},
                        "main", ["main"]):
            with self.subTest(courses=courses), self.assertRaises(ValueError):
                MenuRequest.from_dict({"num_guests": 5, "budget": 100, "courses": courses})

    def test_courses_are_kept_in_meal_order_without_zeros(self):
        request = MenuRequest.from_dict({"num_guests": 5, "budget": 100,
                                         "courses": {"dessert": 1, "side": 0, "starter": 1, "main": 2}})
        self.assertEqual(list(request.courses.items()), [("starter", 1), ("main", 2), ("dessert", 1)])

    def test_menu_must_match_the_chosen_courses(self):
        tools = self.tools()
        self.assertNotIn("error", tools.calculate_cost(self.MENU))
        self.assertIn("error", tools.calculate_cost(dict(self.MENU, main=["שניצל עוף", "שקשוקה"])))  # ביקשו אחת
        self.assertIn("error", tools.calculate_cost(dict(self.MENU, side=["מג'דרה"])))             # לא ביקשו תוספת
        self.assertIn("error", tools.calculate_cost({k: v for k, v in self.MENU.items() if k != "starter"}))

    def test_single_dish_of_a_kind_must_suit_everyone(self):
        # עיקרית אחת בלבד - הצמחוני לא יכול לאכול שניצל, וזו הפרה קשה (עם שתי עיקריות זה היה מותר)
        result = self.tools([{"name": "יוסי", "diet": "vegetarian"}]).check_guests(self.MENU)
        self.assertEqual([v["type"] for v in result["violations"]], ["hard"])
        self.assertIn("שניצל עוף", result["violations"][0]["problem"])

    def test_two_desserts_one_is_enough(self):
        # עוגת השוקולד עם ביצים - אבל יש סלט פירות, אז אלרגי לביצים מסודר בקינוחים
        guests = [{"name": "דנה", "allergies": ["eggs"]}]
        menu = dict(self.MENU, main=["עוף בתנור עם תפוחי אדמה"])
        self.assertTrue(self.tools(guests).check_guests(menu)["all_satisfied"])
        both_eggs = dict(menu, dessert=["מוס שוקולד", "עוגת שוקולד"])
        problem = self.tools(guests).check_guests(both_eggs)["violations"][0]["problem"]
        self.assertIn("אין אף מנה מסוג קינוח", problem)

    def test_dislikes_count_kinds_not_dishes(self):
        # לא אוהבת שוקולד - אבל יש סלט פירות, אז הקינוחים לא "נתקעים". עם מרק שיש בו בצל - עדיין סוג אחד בלבד
        guests = [{"name": "מירב", "dislikes": ["שוקולד", "בצל"]}]
        menu = dict(self.MENU, main=["סלמון בתנור עם לימון ושמיר"])
        self.assertTrue(self.tools(guests).check_guests(menu)["all_satisfied"])
        stuck = dict(menu, dessert=["מוס שוקולד", "עוגת שוקולד"])  # עכשיו גם הקינוחים וגם המרק
        self.assertEqual(self.tools(guests).check_guests(stuck)["violations"][0]["type"], "soft")
        report = self.tools(guests).preferences_report(menu)
        self.assertEqual([(c["dish"], c["alternative"]) for c in report["compromises"]],
                         [("מרק עדשים כתומות", []), ("עוגת שוקולד", ["סלט פירות"])])

    def test_disliked_dish_with_an_alternative_is_still_reported(self):
        # מהצ'אט: מרק עוף לצד מרק עדשים, והמארחת לא אוהבת עוף. מותר - אבל צריך להופיע בכרטיס, עם חלופה זולה
        tools = self.tools([{"name": "אני", "dislikes": ["עוף"]}], courses={"starter": 2, "main": 1, "dessert": 1})
        menu = {"starter": ["מרק עדשים כתומות", "מרק עוף"], "main": ["קארי עדשים בחלב קוקוס"], "dessert": ["סלט פירות"]}
        self.assertTrue(tools.check_guests(menu)["all_satisfied"])
        [item] = tools.preferences_report(menu)["compromises"]
        self.assertEqual((item["dish"], item["alternative"]), ("מרק עוף", ["מרק עדשים כתומות"]))
        self.assertIn("אפשר היה", item["reason"])

    def test_portions_follow_the_number_of_dishes(self):
        # 10 סועדים: מנה ראשונה ועיקרית - מנה שלמה; שני קינוחים - 60% מכל אחד
        tools = self.tools()
        cost = tools.calculate_cost(self.MENU)
        whole = MenuTools(MenuRequest.from_dict({"num_guests": 10, "budget": 1000,
                                                 "courses": {"dessert": 1}}))
        fruit_whole = whole.calculate_cost({"dessert": ["סלט פירות"]})["total_cost"]
        fruit = next(d["cost"] for d in cost["dishes"] if d["name"] == "סלט פירות")
        self.assertAlmostEqual(fruit, round(fruit_whole * 0.6, 2), places=1)

    def test_swaps_stay_inside_the_chosen_courses(self):
        tools = self.tools([{"name": "דנה", "allergies": ["eggs"]}])
        menu = dict(self.MENU, main=["עוף בתנור עם תפוחי אדמה"])
        result = tools.suggest_swaps(menu)
        self.assertEqual([s["category"] for s in result["suggestions"]], ["starter", "main", "dessert", "dessert"])
        for suggestion in result["suggestions"]:
            for option in suggestion["options"]:
                swapped = _swap(menu, suggestion["replace"], option["dish"])
                self.assertTrue(tools.finalize_menu(swapped)["approved"], swapped)

    def test_like_for_a_course_that_is_not_in_the_menu(self):
        reason = TestPreferenceReasons().like_reason([{"name": "שירה", "likes": ["מרק עוף"]}])
        self.assertIn("אין מנה ראשונה בתפריט", reason)

    def test_judge_finds_the_cheapest(self):
        from evaluate import find_best_menu
        tools = self.tools([{"name": "דנה", "allergies": ["eggs"], "dislikes": ["בצל"]}], kosher=True)
        truth = find_best_menu(tools)
        self.assertTrue(tools.finalize_menu(truth["best"]["menu"])["approved"])
        self.assertNotIn("best_swap", tools.suggest_swaps(truth["best"]["menu"]))


class TestFoodGroups(unittest.TestCase):
    """"לא אוהב עוף" = כל מה שיש בו עוף, גם פרגיות. מילה ספציפית נשארת ספציפית."""

    def test_general_word_covers_the_group(self):
        g = MenuRequest.from_dict({"num_guests": 2, "budget": 100,
                                   "guests": [{"name": "אני", "dislikes": ["עוף", "דגים", "גבינות"]}]}).guests[0]
        tools = make_tools()
        for dish in ("פרגיות בתנור עם ירקות", "מרק עוף", "שניצל עוף", "סלמון בתנור עם לימון ושמיר",
                     "קציצות דגים", "סלט קפרזה", "פסטה ברוטב שמנת ופטריות"):
            with self.subTest(dish):
                self.assertTrue(g.disliked_ingredients(tools.recipes[dish]))
        self.assertFalse(g.disliked_ingredients(tools.recipes["קארי עדשים בחלב קוקוס"]))

    def test_specific_word_stays_specific(self):
        g = MenuRequest.from_dict({"num_guests": 2, "budget": 100,
                                   "guests": [{"name": "אני", "dislikes": ["חזה עוף"]}]}).guests[0]
        tools = make_tools()
        self.assertTrue(g.disliked_ingredients(tools.recipes["שניצל עוף"]))
        self.assertFalse(g.disliked_ingredients(tools.recipes["פרגיות בתנור עם ירקות"]))

    def test_search_uses_the_group(self):
        names = [r["name"] for r in make_tools().search_recipes("main", exclude_ingredients=["עוף"])["recipes"]]
        self.assertNotIn("פרגיות בתנור עם ירקות", names)
        liked = [r["name"] for r in make_tools().search_recipes("main", include_any=["דגים"])["recipes"]]
        self.assertEqual(sorted(liked), ["סלמון בתנור עם לימון ושמיר", "פילה אמנון ברוטב עגבניות"])


class TestExcludeDishes(unittest.TestCase):
    """"אבל יש עוף במרק" - המשתמש ביקש להחליף מנה. אילוץ קשיח, כמו אלרגיה."""

    def tools(self):
        return MenuTools(MenuRequest.from_dict({"num_guests": 10, "budget": 1000, "exclude_dishes": ["מג'דרה"]}))

    def test_excluded_dish_is_a_hard_violation(self):
        tools = self.tools()
        violations = tools.check_guests(VALID_MENU)["violations"]
        self.assertEqual([(v["type"], v["guest"]) for v in violations], [("hard", "בקשת המשתמש")])
        self.assertFalse(tools.finalize_menu(VALID_MENU)["approved"])

    def test_search_swaps_and_judge_skip_it(self):
        from evaluate import find_best_menu
        tools = self.tools()
        self.assertNotIn("מג'דרה", [r["name"] for r in tools.search_recipes("side")["recipes"]])
        side = next(s for s in tools.suggest_swaps(VALID_MENU)["suggestions"] if s["category"] == "side")
        self.assertTrue(side["options"])  # יש במה להחליף
        self.assertNotIn("מג'דרה", find_best_menu(tools)["best"]["menu"]["side"])

    def test_invalid_value(self):
        for value in ("מג'דרה", [1]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MenuRequest.from_dict({"num_guests": 5, "budget": 100, "exclude_dishes": value})


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
        result = tools.finalize_menu(dict(VALID_MENU, side=["אורז עם שקדים קלויים"]))
        self.assertFalse(result["approved"])
        self.assertEqual(len(result["reasons"]), 2)  # תקציב + אלרגיה
        self.assertIsNone(tools.approved_menu)


if __name__ == "__main__":
    unittest.main()
