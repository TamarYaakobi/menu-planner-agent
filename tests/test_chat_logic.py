"""בדיקות ללוגיקה של הצ'אט: זיהוי "כן", בקשות מתכונים, עיצוב כמויות, ושמירת שיחות."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from chat_logic import (delete_conversation, format_amount, format_recipe, is_thanks, is_yes,  # noqa: E402
                        list_conversations, load_conversation, requested_dishes, save_conversation, wants_recipes)
from goal_parser import ParsedGoal  # noqa: E402
from tools import DATA_DIR, MenuRequest, MenuTools, load_json  # noqa: E402

MENU = {"mains": ["שניצל עוף", "קארי עדשים בחלב קוקוס"], "side": "מג'דרה",
        "vegetable": "סלט ירקות קצוץ", "dessert": "סלט פירות"}


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


class TestRecipeRequests(unittest.TestCase):

    def test_wants_recipes(self):
        for text in ("מתכונים", "תביא לי את המתכונים", "מתכון לשניצל", "איך מכינים את הקארי?"):
            self.assertTrue(wants_recipes(text), text)
        self.assertFalse(wants_recipes("בלי סלמון"))

    def test_which_dishes(self):
        all_dishes = MENU["mains"] + [MENU["side"], MENU["vegetable"], MENU["dessert"]]
        cases = {
            "תביא לי את המתכונים": all_dishes,
            "מתכון לשניצל": ["שניצל עוף"],                      # "לשניצל" - קידומת של אות אחת
            "מתכון לקינוח בבקשה": ["סלט פירות"],                # לפי סוג המנה
            "איך מכינים את הקארי?": ["קארי עדשים בחלב קוקוס"],
            "את המתכון של הסלט": ["סלט ירקות קצוץ"],            # "סלט" = מנת הירקות, לא גם סלט הפירות
            "מתכון לסלט הפירות": ["סלט פירות"],                 # אבל "סלט הפירות" = הקינוח בלבד
            "מתכונים לעיקריות": MENU["mains"],
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
