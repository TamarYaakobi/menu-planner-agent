"""
בדיקות לממשק הצ'אט - בלי מודל אמיתי: המפענח והאג'נט מוחלפים בגרסאות מזויפות,
וכל מסלול בשיחה נבדק (שאלה על מידע חסר, אישור, תיקון, תפריט, כישלון, תקלה במודל).
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

import httpx2
from openai import APITimeoutError
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).parent.parent))

from goal_parser import ParsedGoal  # noqa: E402

CHAT = str(Path(__file__).parent.parent / "chat.py")
VALID_MENU = {"mains": ["עוף בתנור עם תפוחי אדמה", "קארי עדשים בחלב קוקוס"],
              "side": "מג'דרה", "vegetable": "סלט חסה עם אגוזי מלך", "dessert": "סלט פירות"}
REQUEST = {"num_guests": 4, "budget": 300, "guests": [
    {"name": "דוד", "allergies": ["dairy"], "diet": None, "dislikes": [], "likes": []},
    {"name": "דודה", "allergies": [], "diet": None, "dislikes": [], "likes": ["חסה"]}]}


class FakeAgent:
    succeed = True

    def __init__(self, client, model, tools, on_event=None, fallbacks=None):
        self.tools, self.on_event = tools, on_event
        self.steps_used, self.failure_reason, self.best_cost = 2, None, None

    def run(self):
        self.on_event("thought", {"text": "מחפש מנות"})
        if not FakeAgent.succeed:
            self.failure_reason = "יקר מדי"
            self.best_cost = {"menu": VALID_MENU, "total_cost": 349.2, "budget": 300}
            return False
        result = self.tools.finalize_menu(VALID_MENU)
        self.on_event("tool", {"name": "finalize_menu", "args": {"menu": VALID_MENU}, "result": result})
        return result["approved"]


class TestChat(unittest.TestCase):

    def setUp(self):
        FakeAgent.succeed = True
        self.parse = mock.Mock(return_value=ParsedGoal("ok", request=REQUEST, model="fake"))
        self.patches = [mock.patch("goal_parser.parse_goal", self.parse),
                        mock.patch("agent.MenuAgent", FakeAgent),
                        mock.patch("llm.create_client", return_value=(None, "fake")),
                        mock.patch("llm.fallback_models", return_value=[])]
        for p in self.patches:
            p.start()
        self.app = AppTest.from_file(CHAT, default_timeout=30).run()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def send(self, text):
        self.app.chat_input[0].set_value(text).run()
        self.assertFalse(self.app.exception, self.app.exception)
        return self.app.session_state["messages"][-1]["content"]

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
        last = self.app.session_state["messages"][-1]
        self.assertTrue(last["shopping_list"])
        self.assertIn("💭 מחפש מנות", last["events"])
        self.assertEqual(self.app.session_state["stage"], "done")

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

    def test_correction_instead_of_yes(self):
        self.send("4 איש, 300 שקל")
        self.send("בעצם התקציב 200")
        self.assertIn("תיקון: בעצם התקציב 200", self.parse.call_args.args[2])
        self.assertEqual(self.app.session_state["stage"], "confirm")

    def test_warnings_are_shown(self):
        self.parse.return_value = ParsedGoal("ok", request=REQUEST, unsupported=["בוטנים"], model="fake")
        reply = self.send("אבא אלרגי לבוטנים")
        self.assertIn("בוטנים", reply)
        self.assertIn("שימי לב לאזהרות", reply)

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

    def test_new_conversation_button(self):
        self.send("4 איש, 300 שקל")
        self.app.sidebar.button[0].click().run()
        self.assertEqual(len(self.app.session_state["messages"]), 1)
        self.assertEqual(self.app.session_state["stage"], "goal")


if __name__ == "__main__":
    unittest.main()
