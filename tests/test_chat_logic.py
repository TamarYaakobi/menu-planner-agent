"""בדיקות ללוגיקה של הצ'אט: זיהוי "כן", בקשות מתכונים, עיצוב כמויות, ושמירת שיחות."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from chat_logic import (cost_answer, delete_conversation, diners, format_amount, format_recipe,  # noqa: E402
                        is_cost_question, is_thanks, is_yes, list_conversations, load_conversation,
                        menu_card_html, requested_dishes, save_conversation, shopping_line, wants_recipes)
from goal_parser import ParsedGoal  # noqa: E402
from tools import DATA_DIR, MenuRequest, MenuTools, load_json  # noqa: E402

MENU = {"main": ["שניצל עוף", "קארי עדשים בחלב קוקוס"], "side": ["מג'דרה"],
        "vegetable": ["סלט ירקות קצוץ"], "dessert": ["סלט פירות"]}


class TestHtml(unittest.TestCase):
    """הכרטיסים ב-HTML: מה שהמשתמשת כתבה עובר escape, ורשימת הקניות כתובה בעברית טבעית."""

    def test_user_text_cannot_inject_html(self):
        from chat_logic import user_html
        self.assertNotIn("<script>", user_html("<script>alert(1)</script>"))
        approved = {"menu": MENU, "total_cost": 100, "cost_per_guest": 10, "shopping_list": [],
                    "likes": [{"guest": "<b>דנה</b>", "likes": "חסה", "in_menu": False, "dishes": [], "reason": "<i>x</i>"}],
                    "compromises": []}
        card = menu_card_html(approved, {"budget": 200}, [])
        self.assertNotIn("<b>דנה</b>", card)
        self.assertIn("&lt;b&gt;דנה&lt;/b&gt;", card)

    def test_card_shows_reasons_and_kosher(self):
        approved = {"menu": MENU, "total_cost": 143.2, "cost_per_guest": 17.9, "shopping_list": [],
                    "likes": [{"guest": "שירה", "likes": "אגוזי מלך", "in_menu": False, "dishes": [],
                               "reason": "סלט חסה עם אגוזי מלך מכילה אגוזים (דנה)"},
                              {"guest": "אמא", "likes": "ארטישוק", "in_menu": False, "dishes": [], "reason": "x"}],
                    "compromises": [{"guest": "מירב", "dislikes": "עגבניות", "dish": "סלט ירקות קצוץ", "reason": "y"}]}
        card = menu_card_html(approved, {"budget": 200, "kosher": True}, ["ארטישוק"])
        self.assertIn("מכילה אגוזים (דנה)", card)
        self.assertIn("אין לי מתכון עם זה", card)
        self.assertIn("מירב לא אוהב/ת עגבניות", card)
        self.assertIn("🕍 כשר", card)

    def test_shopping_lines_read_naturally(self):
        cases = [({"item": "אבקת קארי", "needed": 14.4, "unit": "גרם", "packages": 1, "package_size": 100},
                  ("14 גרם", "אריזה אחת של 100 גרם")),
                 ({"item": "חלב קוקוס", "needed": 480, "unit": 'מ"ל', "packages": 2, "package_size": 400},
                  ('480 מ"ל', '2 אריזות של 400 מ"ל')),
                 ({"item": "לימון", "needed": 1.6, "unit": "יח'", "packages": 2, "package_size": 1},
                  ("2 יח'", "2 יח'")),
                 ({"item": "שוקי עוף", "needed": 1440, "unit": "גרם", "packages": 2, "package_size": 1000},
                  ('1.4 ק"ג', '2 אריזות של 1 ק"ג'))]
        for item, expected in cases:
            with self.subTest(item["item"]):
                self.assertEqual(shopping_line(item), expected)


class TestYesAndThanks(unittest.TestCase):

    def test_yes(self):
        for text in ("כן", "אחלה", "כן, תודה!", "אחלה יאללה תתחיל", "👍", "טוב אז תתחיל", "סבבה לגמרי", "OK"):
            with self.subTest(text):
                self.assertTrue(is_yes(text))

    def test_not_yes(self):
        # "כן אבל..." הוא תיקון - אסור להתעלם ממנו. "כנראה" מתחיל ב"כנ" ולא ב"כן"
        for text in ("כן אבל התקציב 200", "בעצם 6 סועדים", "לא", "כנראה 5", "", "תודה"):
            with self.subTest(text):
                self.assertFalse(is_yes(text))

    def test_thanks(self):
        self.assertTrue(is_thanks("תודה רבה!"))
        self.assertTrue(is_thanks("מושלם, תודה 🙏"))
        self.assertFalse(is_thanks("תודה, ובלי סלמון"))


class TestCostQuestions(unittest.TestCase):

    def test_detects_questions_not_requests(self):
        for text in ("כמה יעלה התפריט לסועד?", "כמה זה עולה?", "מה המחיר לאדם?", "מה העלות?", "how much per person?"):
            self.assertTrue(is_cost_question(text), text)
        # בקשות לשנות - לא שאלות
        for text in ("תוריד את העלות", "התקציב 200", "כמה סועדים יש?", "בלי סלמון"):
            self.assertFalse(is_cost_question(text), text)

    def test_answer_matches_the_tools(self):
        menu = {"main": ["עוף בתנור עם תפוחי אדמה", "קארי עדשים בחלב קוקוס"], "side": ["תפוחי אדמה אפויים ברוזמרין"],
                "vegetable": ["סלט חסה עם אגוזי מלך"], "dessert": ["סלט פירות"]}
        request = {"num_guests": 10, "budget": 1000, "guests": []}
        tools = MenuTools(MenuRequest.from_dict(request))
        approved = tools.finalize_menu(menu)
        answer = cost_answer({"approved": approved, "request": request})
        self.assertIn(f"{approved['cost_per_guest']:.2f} ש\"ח לסועד", answer)
        self.assertIn("עוף בתנור עם תפוחי אדמה: 7.28", answer)  # 72.8 ש"ח לעשרה סועדים
        self.assertIn("באריזות שלמות", answer)


class TestRecipeRequests(unittest.TestCase):

    def test_wants_recipes(self):
        for text in ("מתכונים", "תביא לי את המתכונים", "מתכון לשניצל", "איך מכינים את הקארי?"):
            self.assertTrue(wants_recipes(text), text)
        self.assertFalse(wants_recipes("בלי סלמון"))

    def test_which_dishes(self):
        all_dishes = MENU["main"] + MENU["side"] + MENU["vegetable"] + MENU["dessert"]
        cases = {
            "תביא לי את המתכונים": all_dishes,
            "מתכון לשניצל": ["שניצל עוף"],                      # "לשניצל" - קידומת של אות אחת
            "מתכון לקינוח בבקשה": ["סלט פירות"],                # לפי סוג המנה
            "איך מכינים את הקארי?": ["קארי עדשים בחלב קוקוס"],
            "את המתכון של הסלט": ["סלט ירקות קצוץ"],            # "סלט" = מנת הירקות, לא גם סלט הפירות
            "מתכון לסלט הפירות": ["סלט פירות"],                 # אבל "סלט הפירות" = הקינוח בלבד
            "מתכונים לעיקריות": MENU["main"],
            "מתכון למג'דרה ולשניצל": ["מג'דרה", "שניצל עוף"],   # גרש בתוך המילה
        }
        for text, expected in cases.items():
            with self.subTest(text):
                self.assertEqual(sorted(requested_dishes(text, MENU)), sorted(expected))


class TestRecipeFormatting(unittest.TestCase):

    def test_amounts(self):
        cases = [(1800, "g", '1.8 ק"ג'), (240, "g", "240 גרם"), (12.4, "g", "12 גרם"), (0.3, "g", "קורט"),
                 (1500, "ml", "1.5 ליטר"), (2.5, "unit", "2.5 יח'"), (1.2, "unit", "1.5 יח'"), (3, "unit", "3 יח'")]
        for amount, unit, expected in cases:
            with self.subTest(amount=amount, unit=unit):
                self.assertEqual(format_amount(amount, unit), expected)

    def test_one_diner_is_not_plural(self):
        # בצ'אט הופיע "1 סועדים"
        self.assertEqual((diners(1), diners(8)), ("סועד אחד", "8 סועדים"))
        self.assertEqual((diners(1, "ל"), diners(8, "ל")), ("לסועד אחד", "ל-8 סועדים"))
        card = MenuTools(MenuRequest.from_dict({"num_guests": 1, "budget": 100})).recipe_card("שניצל עוף")
        self.assertIn("לסועד אחד", format_recipe(card))
        self.assertNotIn("1 סועדים", format_recipe(card))

    def test_every_recipe_has_steps_and_time(self):
        for recipe in load_json(DATA_DIR / "recipes.json"):
            with self.subTest(recipe["name"]):
                self.assertGreaterEqual(len(recipe.get("steps", [])), 2)
                self.assertIsInstance(recipe.get("time_minutes"), int)
                self.assertGreater(recipe["time_minutes"], 0)

    def test_amounts_scale_with_guests(self):
        small = MenuTools(MenuRequest.from_dict({"num_guests": 5, "budget": 500})).recipe_card("סלט פירות")
        big = MenuTools(MenuRequest.from_dict({"num_guests": 10, "budget": 500})).recipe_card("סלט פירות")
        self.assertEqual(big["ingredients"][0]["amount"], 2 * small["ingredients"][0]["amount"])

    def test_mains_use_partial_portion(self):
        card = MenuTools(MenuRequest.from_dict({"num_guests": 10, "budget": 500})).recipe_card("שניצל עוף")
        self.assertAlmostEqual(card["ingredients"][0]["amount"], 180 * 10 * 0.6)  # חזה עוף: 180 גרם למנה
        self.assertIn("60% מנה", format_recipe(card))

    def test_portion_follows_the_chosen_courses(self):
        # עיקרית אחת - מנה שלמה. שלושה קינוחים - 40% מכל אחד
        request = MenuRequest.from_dict({"num_guests": 10, "budget": 500, "courses": {"main": 1, "dessert": 3}})
        tools = MenuTools(request)
        main, dessert = tools.recipe_card("שניצל עוף"), tools.recipe_card("סלט פירות")
        self.assertAlmostEqual(main["ingredients"][0]["amount"], 180 * 10)
        self.assertNotIn("% מנה", format_recipe(main))
        self.assertAlmostEqual(dessert["ingredients"][0]["amount"], 80 * 10 * 0.4)
        self.assertIn("40% מנה לסועד, כי יש 3 קינוחים", format_recipe(dessert))

    def test_starter_recipe(self):
        cases = {"מתכון למרק": ["מרק עוף"], "מתכון למנה הראשונה": ["מרק עוף"]}
        menu = dict(MENU, starter=["מרק עוף"])
        for text, expected in cases.items():
            with self.subTest(text):
                self.assertEqual(requested_dishes(text, menu), expected)

    def test_unknown_dish(self):
        tools = MenuTools(MenuRequest.from_dict({"num_guests": 5, "budget": 500}))
        self.assertIn("error", tools.recipe_card("פיצה"))


class TestHistoryStorage(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"CHAT_HISTORY_DIR": self.directory.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.directory.cleanup()

    def state(self, *messages, goal=None):
        return {"messages": [{"role": "assistant", "content": "היי"}, *messages], "stage": "confirm",
                "goal_text": "x", "goal": goal, "model": "m", "menu": None, "previous_menu": None}

    def test_round_trip(self):
        goal = ParsedGoal("ok", request={"num_guests": 4, "budget": 300, "guests": []}, changes=["תקציב"])
        save_conversation("a1", self.state({"role": "user", "content": "4 איש, 300 שקל"}, goal=goal))
        loaded = load_conversation("a1")
        self.assertEqual(loaded["goal"], goal)  # חוזר כ-ParsedGoal, לא כ-dict
        self.assertEqual(loaded["stage"], "confirm")
        self.assertEqual(list_conversations()[0]["title"], "4 איש, 300 שקל")

    def test_old_menu_format_is_upgraded(self):
        # שיחות מלפני שאפשר היה לבחור סוגי מנות: "mains" ומחרוזת לכל סוג אחר
        old = {"mains": MENU["main"], "side": "מג'דרה", "vegetable": "סלט ירקות קצוץ", "dessert": "סלט פירות"}
        request = {"num_guests": 4, "budget": 300, "guests": []}
        state = self.state({"role": "user", "content": "4 איש"})
        state.update(menu={"approved": {"menu": old, "total_cost": 50, "shopping_list": []}, "request": request},
                     previous_menu={"menu": dict(old), "total_cost": 50})
        save_conversation("old", state)
        loaded = load_conversation("old")
        self.assertEqual(loaded["menu"]["approved"]["menu"], MENU)
        self.assertEqual(loaded["previous_menu"]["menu"], MENU)
        self.assertIn("מג'דרה", cost_answer(loaded["menu"]))  # והוא עובד עם הכלים

    def test_no_user_message_not_saved(self):
        save_conversation("a1", self.state())
        self.assertEqual(list_conversations(), [])

    def test_newest_first(self):
        save_conversation("old", self.state({"role": "user", "content": "ישנה"}))
        path = Path(self.directory.name) / "old.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["updated"] = "2000-01-01T00:00:00"
        path.write_text(json.dumps(data), encoding="utf-8")
        save_conversation("new", self.state({"role": "user", "content": "חדשה"}))
        self.assertEqual([c["id"] for c in list_conversations()], ["new", "old"])

    def test_corrupt_file_is_skipped(self):
        save_conversation("good", self.state({"role": "user", "content": "תקינה"}))
        (Path(self.directory.name) / "bad.json").write_text("{not json", encoding="utf-8")
        self.assertEqual([c["id"] for c in list_conversations()], ["good"])
        self.assertIsNone(load_conversation("bad"))

    def test_delete(self):
        save_conversation("a1", self.state({"role": "user", "content": "x"}))
        delete_conversation("a1")
        self.assertEqual(list_conversations(), [])
        delete_conversation("a1")  # מחיקה של מה שכבר לא קיים - לא שגיאה

    def test_id_cannot_escape_the_folder(self):
        for bad in ("../secret", "a/b", "..", "a.json"):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    load_conversation(bad)
                with self.assertRaises(ValueError):
                    delete_conversation(bad)


if __name__ == "__main__":
    unittest.main()
