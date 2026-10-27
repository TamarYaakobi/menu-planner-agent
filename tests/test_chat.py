"""
בדיקות לממשק הצ'אט - בלי מודל אמיתי: המפענח והאג'נט מוחלפים בגרסאות מזויפות,
וכל מסלול בשיחה נבדק (שאלה, אישור, תיקון, תפריט, מתכונים, כישלון, תקלה במודל, היסטוריה).
השיחות נשמרות לתיקייה זמנית - לא לתיקייה האמיתית של המשתמשת.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx2
from openai import APITimeoutError
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).parent.parent))

from chat_logic import list_conversations  # noqa: E402
from goal_parser import ParsedGoal  # noqa: E402

CHAT = str(Path(__file__).parent.parent / "chat.py")
VALID_MENU = {"mains": ["עוף בתנור עם תפוחי אדמה", "קארי עדשים בחלב קוקוס"],
              "side": "מג'דרה", "vegetable": "סלט חסה עם אגוזי מלך", "dessert": "סלט פירות"}
REQUEST = {"num_guests": 4, "budget": 300, "guests": [
    {"name": "דוד", "allergies": ["dairy"], "diet": None, "dislikes": [], "likes": []},
    {"name": "דודה", "allergies": [], "diet": None, "dislikes": [], "likes": ["חסה"]}]}


class FakeAgent:
    succeed = True
    last_previous_menu = None

    def __init__(self, client, model, tools, on_event=None, fallbacks=None, previous_menu=None):
        self.tools, self.on_event = tools, on_event
        self.steps_used, self.failure_reason, self.best_cost = 2, None, None
        FakeAgent.last_previous_menu = previous_menu
        FakeAgent.last_request = tools.request

    def run(self):
        self.on_event("thought", {"text": "מחפש מנות"})
        if not FakeAgent.succeed:
            self.failure_reason = "יקר מדי"
            self.best_cost = {"menu": VALID_MENU, "total_cost": 349.2, "budget": 300}
            return False
        result = self.tools.finalize_menu(VALID_MENU)
        self.on_event("tool", {"name": "finalize_menu", "args": {"menu": VALID_MENU}, "result": result})
        return result["approved"]


class ChatTestCase(unittest.TestCase):
    """הכנה משותפת: מודל ואג'נט מזויפים, ותיקיית היסטוריה זמנית."""

    def setUp(self):
        FakeAgent.succeed, FakeAgent.last_previous_menu = True, None
        self.history = tempfile.TemporaryDirectory()
        self.parse = mock.Mock(return_value=ParsedGoal("ok", request=REQUEST, model="fake"))
        self.patches = [mock.patch("goal_parser.parse_goal", self.parse),
                        mock.patch("agent.MenuAgent", FakeAgent),
                        mock.patch("llm.create_client", return_value=(None, "fake")),
                        mock.patch("llm.fallback_models", return_value=[]),
                        mock.patch.dict(os.environ, {"CHAT_HISTORY_DIR": self.history.name})]
        for p in self.patches:
            p.start()
        self.app = AppTest.from_file(CHAT, default_timeout=30).run()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.history.cleanup()

    def send(self, text):
        self.app.chat_input[0].set_value(text).run()
        self.assertFalse(self.app.exception, self.app.exception)
        return self.app.session_state["messages"][-1]["content"]

    def get_menu(self):
        self.send("4 איש, 300 שקל, דוד רגיש לחלב, דודה אוהבת חסה")
        return self.send("כן")


class TestChat(ChatTestCase):

    def test_welcome(self):
        self.assertIn("היי", self.app.session_state["messages"][0]["content"])

    def test_full_conversation(self):
        reply = self.send("4 איש, 300 שקל, דוד רגיש לחלב, דודה אוהבת חסה")
        self.assertIn("להתחיל לתכנן", reply)
        self.assertIn("אוהב/ת חסה", reply)
        self.assertEqual(self.app.session_state["stage"], "confirm")

        reply = self.send("כן")
        self.assertIn("הנה התפריט", reply)
        self.assertIn("❤️ דודה אוהב/ת חסה", reply)
        self.assertIn("רוצה מתכונים", reply)
        last = self.app.session_state["messages"][-1]
        self.assertTrue(last["shopping_list"])
        self.assertIn("💭 מחפש מנות", last["events"])
        self.assertEqual(self.app.session_state["stage"], "done")

    def test_other_ways_to_say_yes(self):
        for yes in ("אחלה", "סבבה, יאללה תתחיל", "👍"):
            with self.subTest(yes):
                self.send("4 איש, 300 שקל")
                self.assertIn("הנה התפריט", self.send(yes))

    def test_yes_with_a_correction_is_a_correction(self):
        self.send("4 איש, 300 שקל")
        self.send("כן אבל התקציב 200")
        self.assertEqual(self.parse.call_args.args[2], "כן אבל התקציב 200")
        self.assertEqual(self.app.session_state["stage"], "confirm")  # לא תכנן - חזר לאישור

    def test_missing_info_question(self):
        self.parse.return_value = ParsedGoal("needs_info", questions=["כמה סועדים?"])
        reply = self.send("אני רוצה לארח")
        self.assertIn("כמה סועדים?", reply)
        self.assertEqual(self.app.session_state["stage"], "goal")
        self.parse.return_value = ParsedGoal("ok", request=REQUEST, model="fake")
        self.send("4")
        sent_text = self.parse.call_args.args[2]
        self.assertIn("אני רוצה לארח", sent_text)  # התשובה מצטרפת לבקשה המקורית
        self.assertIn("4", sent_text)

    def test_correction_sends_only_the_new_message_with_the_known_request(self):
        # בשיחות ארוכות המודל ערבב מי רגיש למה - עכשיו הוא מקבל את הבקשה המובנית + ההודעה החדשה בלבד
        self.send("4 איש, 300 שקל, דוד רגיש לחלב")
        updated = ParsedGoal("ok", request=dict(REQUEST, budget=200), model="fake", changes=["תקציב: מ-300 ל-200"])
        self.parse.return_value = updated
        reply = self.send("בעצם התקציב 200")
        self.assertEqual(self.parse.call_args.args[2], "בעצם התקציב 200")
        self.assertEqual(self.parse.call_args.kwargs["previous"].request, REQUEST)
        self.assertIn("עדכנתי", reply)
        self.assertIn("תקציב: מ-300 ל-200", reply)

    def test_replanning_starts_from_the_previous_menu(self):
        self.get_menu()
        self.parse.return_value = ParsedGoal("ok", request=dict(REQUEST, budget=250), model="fake")
        self.send("התקציב 250")
        self.send("כן")
        self.assertEqual(FakeAgent.last_previous_menu["menu"], VALID_MENU)

    def test_recipes_after_menu(self):
        self.get_menu()
        reply = self.send("מתכון לקינוח")
        self.assertIn("### 🍳 סלט פירות", reply)
        self.assertIn("**הכנה:**", reply)
        self.assertNotIn("עוף בתנור", reply)
        reply = self.send("תביא את כל המתכונים")
        self.assertEqual(reply.count("### 🍳"), 5)
        self.parse.assert_called_once()  # בקשת מתכונים לא נשלחת למפענח

    def test_cost_question_after_menu(self):
        # השאלה שהפילה את הצ'אט - עכשיו נענית מהנתונים, בלי לשלוח למפענח
        self.get_menu()
        reply = self.send("כמה יעלה התפריט לסועד?")
        self.assertIn("ש\"ח לסועד", reply)
        self.assertIn("באריזות שלמות", reply)
        self.parse.assert_called_once()

    def test_unexpected_error_is_a_friendly_message(self):
        self.parse.side_effect = TypeError("unhashable type: 'list'")
        reply = self.send("4 איש, 300 שקל")
        self.assertIn("משהו השתבש", reply)

    def test_kosher_setting_applies_to_planning(self):
        self.app.sidebar.toggle[0].set_value(True).run()
        self.assertIn("ארוחה כשרה", self.send("4 איש, 300 שקל"))
        self.assertIn("כשר", self.send("כן"))
        self.assertTrue(FakeAgent.last_request.kosher)
        self.assertTrue(self.app.session_state["menu"]["request"]["kosher"])  # גם המתכונים והעלות לפי אותה בקשה

    def test_thanks_after_menu(self):
        self.get_menu()
        self.assertIn("בשמחה", self.send("תודה רבה!"))

    def test_warnings_are_shown(self):
        self.parse.return_value = ParsedGoal("ok", request=REQUEST, unsupported=["כשרות"], model="fake")
        reply = self.send("ארוחה כשרה")
        self.assertIn("כשרות", reply)
        self.assertIn("שימי לב לאזהרות", reply)

    def test_approximate_budget_is_shown(self):
        self.parse.return_value = ParsedGoal("ok", request=dict(REQUEST, budget=350, target_budget=300), model="fake")
        reply = self.send("4 איש, בערך 300 שקל")
        self.assertIn("בערך 300", reply)
        self.assertIn("עד 350", reply)

    def test_failure_suggests_budget(self):
        FakeAgent.succeed = False
        self.send("4 איש, 300 שקל")
        reply = self.send("כן")
        self.assertIn("לא מצאתי", reply)
        self.assertIn("עם תקציב של 350", reply)

    def test_model_unavailable(self):
        self.parse.side_effect = APITimeoutError(request=httpx2.Request("POST", "https://example.com"))
        reply = self.send("4 איש, 300 שקל")
        self.assertIn("המודל לא זמין", reply)

    def test_off_topic_resets(self):
        self.parse.return_value = ParsedGoal("off_topic", error="לא ארוחה")
        reply = self.send("מה מזג האוויר?")
        self.assertIn("רק לתכנן ארוחות", reply)
        self.assertEqual(self.app.session_state["goal_text"], "")


class TestHistory(ChatTestCase):
    """שיחות נשמרות, מופיעות בסרגל הצד, ואפשר לחזור אליהן ולהמשיך."""

    def test_conversation_is_saved_and_can_be_resumed(self):
        self.get_menu()
        saved = list_conversations()
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0]["title"].startswith("4 איש"))

        self.app.sidebar.button[0].click().run()  # שיחה חדשה
        self.assertEqual(len(self.app.session_state["messages"]), 1)

        open_button = next(b for b in self.app.sidebar.button if b.key == f"open_{saved[0]['id']}")
        open_button.click().run()
        self.assertEqual(self.app.session_state["stage"], "done")
        self.assertIn("הנה התפריט", self.app.session_state["messages"][-1]["content"])
        self.assertIn("### 🍳", self.send("מתכונים"))  # ממשיכים בדיוק מאיפה שהפסקנו

    def test_empty_conversation_is_not_saved(self):
        self.assertEqual(list_conversations(), [])

    def test_delete(self):
        self.get_menu()
        conversation_id = list_conversations()[0]["id"]
        next(b for b in self.app.sidebar.button if b.key == f"delete_{conversation_id}").click().run()
        self.assertEqual(list_conversations(), [])
        self.assertEqual(len(self.app.session_state["messages"]), 1)  # השיחה הפתוחה נמחקה - מתחילים חדשה


if __name__ == "__main__":
    unittest.main()
