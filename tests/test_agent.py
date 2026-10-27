"""
בדיקות ללולאת האג'נט, עם "מודל מזויף" שמחזיר תשובות מתוסרטות מראש.
כך בודקים כל תנאי סיום ומקרה קצה - בלי אינטרנט, בלי מכסה ובלי אקראיות.
"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx2  # ספריית ה-HTTP שגרסת openai הנוכחית משתמשת בה
from openai import RateLimitError
from openai.types.chat import ChatCompletionMessage

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent import MenuAgent, _retry_delay  # noqa: E402
from evaluate import find_best_menu, judge  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402
from tools import MenuRequest, MenuTools  # noqa: E402

VALID_MENU = {
    "mains": ["עוף בתנור עם תפוחי אדמה", "קארי עדשים בחלב קוקוס"],
    "side": "מג'דרה", "vegetable": "סלט ירקות קצוץ", "dessert": "סלט פירות",
}


def call(name, args, call_id="c1"):
    """בקשה של המודל להפעיל כלי. args יכול להיות dict או מחרוזת (לבדיקת JSON שבור)."""
    arguments = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def reply(*tool_calls, content=None):
    return ChatCompletionMessage.model_validate(
        {"role": "assistant", "content": content, "tool_calls": list(tool_calls) or None})


def rate_limit_error(message):
    response = httpx2.Response(429, request=httpx2.Request("POST", "https://example.com"))
    return RateLimitError(message, response=response, body=None)


class FakeClient:
    """מחקה את client.chat.completions.create ומחזיר את התשובות לפי הסדר."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []  # מה נשלח למודל בכל קריאה
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append([dict(m) for m in kwargs["messages"]])
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(choices=[SimpleNamespace(message=item)])


def make_agent(*replies, budget=1000, guests=None, max_steps=15):
    tools = MenuTools(MenuRequest.from_dict({"num_guests": 10, "budget": budget, "guests": guests or []}))
    client = FakeClient(*replies)
    events = []
    agent = MenuAgent(client, "fake-model", tools, max_steps=max_steps,
                      on_event=lambda kind, data: events.append((kind, data)))
    return agent, client, events


def tool_results(client, call_index):
    """תוצאות הכלים שהמודל ראה בקריאה מספר call_index."""
    return [json.loads(m["content"]) for m in client.requests[call_index] if m["role"] == "tool"]


class TestStopConditions(unittest.TestCase):

    def test_success_only_after_finalize_approves(self):
        agent, client, _ = make_agent(reply(call("finalize_menu", {"menu": VALID_MENU})))
        self.assertTrue(agent.run())
        self.assertEqual(agent.steps_used, 1)

    def test_rejected_finalize_does_not_end_the_run(self):
        agent, client, _ = make_agent(
            reply(call("finalize_menu", {"menu": VALID_MENU})),   # חורג מהתקציב
            reply(call("report_failure", {"reason": "יקר מדי"})),
            budget=50)
        self.assertFalse(agent.run())
        self.assertFalse(tool_results(client, 1)[0]["approved"])  # המודל ראה את הדחייה
        self.assertEqual(agent.failure_reason, "יקר מדי")

    def test_model_saying_done_in_text_is_not_success(self):
        agent, _, events = make_agent(reply(content="סיימתי! התפריט מצוין."),
                                      reply(content="באמת סיימתי."))
        self.assertFalse(agent.run())
        self.assertIn("nudge", [kind for kind, _ in events])
        self.assertIn("הפסיק", agent.failure_reason)

    def test_nudge_gives_another_chance(self):
        agent, _, _ = make_agent(reply(content="הנה התפריט"),
                                 reply(call("finalize_menu", {"menu": VALID_MENU})))
        self.assertTrue(agent.run())

    def test_max_steps_with_low_steps_warning(self):
        search = reply(call("search_recipes", {"category": "main"}))
        agent, client, events = make_agent(*[search] * 5, max_steps=5)
        self.assertFalse(agent.run())
        self.assertIn("5 צעדים", agent.failure_reason)
        self.assertIn(("low_steps", {"remaining": 3}), events)
        # האזהרה נוספת אחרי צעד 2 (נשארו 3), ולכן המודל רואה אותה בקריאה השלישית
        self.assertIn("נשארו לך רק 3 צעדים", client.requests[2][-1]["content"])


class TestBadToolCalls(unittest.TestCase):
    """טעויות של המודל חוזרות אליו כשגיאה - והריצה ממשיכה."""

    def _first_result(self, bad_call):
        agent, client, _ = make_agent(reply(bad_call), reply(call("finalize_menu", {"menu": VALID_MENU})))
        self.assertTrue(agent.run(), "האג'נט צריך להמשיך אחרי שגיאה")
        return tool_results(client, 1)[0]

    def test_broken_json(self):
        self.assertIn("error", self._first_result(call("search_recipes", "{not json")))

    def test_args_not_an_object(self):
        self.assertIn("error", self._first_result(call("report_failure", "[1, 2]")))

    def test_unknown_tool(self):
        self.assertIn("אין כלי", self._first_result(call("delete_everything", {}))["error"])

    def test_private_method_is_blocked(self):
        self.assertIn("error", self._first_result(call("_shopping_list", {"dishes": []})))

    def test_missing_parameter(self):
        self.assertIn("פרמטרים שגויים", self._first_result(call("calculate_cost", {}))["error"])


class TestBookkeeping(unittest.TestCase):

    def test_best_cost_keeps_cheapest_complete_menu(self):
        cheap = dict(VALID_MENU, vegetable="סלט ירקות קצוץ")
        expensive = dict(VALID_MENU, mains=["סלמון בתנור עם לימון ושמיר", "קארי עדשים בחלב קוקוס"])
        missing_price = dict(VALID_MENU, mains=["ריזוטו כמהין", "שקשוקה"])  # "זול" אבל חלקי
        agent, _, _ = make_agent(
            reply(call("calculate_cost", {"menu": cheap}, "a"), call("calculate_cost", {"menu": expensive}, "b"),
                  call("calculate_cost", {"menu": missing_price}, "c")),
            reply(call("report_failure", {"reason": "x"})), budget=10)
        agent.run()
        self.assertEqual(agent.best_cost["menu"], cheap)

    def test_parallel_tool_calls_all_get_results(self):
        agent, client, _ = make_agent(
            reply(call("search_recipes", {"category": "main"}, "a"), call("search_recipes", {"category": "side"}, "b")),
            reply(call("finalize_menu", {"menu": VALID_MENU})))
        agent.run()
        ids = [m["tool_call_id"] for m in client.requests[1] if m["role"] == "tool"]
        self.assertEqual(ids, ["a", "b"])


class TestRateLimit(unittest.TestCase):

    def test_parses_retry_delay(self):
        self.assertEqual(_retry_delay(rate_limit_error("Please retry in 5.7s.")), 5.7)
        self.assertIsNone(_retry_delay(rate_limit_error("Please retry in 20h34m26.3s.")))

    def test_daily_quota_is_raised_not_waited(self):
        agent, _, _ = make_agent(rate_limit_error("Please retry in 20h34m26.3s."))
        with self.assertRaises(RateLimitError):
            agent.run()

    def test_per_minute_quota_waits_and_continues(self):
        agent, _, events = make_agent(rate_limit_error("Please retry in 0.0s."),
                                      reply(call("finalize_menu", {"menu": VALID_MENU})))
        self.assertTrue(agent.run())
        self.assertIn("rate_limit", [kind for kind, _ in events])


class TestJudge(unittest.TestCase):

    def test_scenarios_match_solver(self):
        """הציפייה שכתובה בכל תרחיש חייבת להתאים למה שהשופט מחשב."""
        for s in SCENARIOS:
            with self.subTest(s["id"]):
                if s["expect"] == "invalid":
                    with self.assertRaises(ValueError):
                        MenuRequest.from_dict(s["request"])
                    continue
                truth = find_best_menu(MenuTools(MenuRequest.from_dict(s["request"])))
                self.assertEqual(truth["solvable"], s["expect"] == "success")

    def test_verdicts(self):
        solvable = {"best": {"total_cost": 100}, "solvable": True, "valid_within_budget": 3}
        unsolvable = {"best": {"total_cost": 100}, "solvable": False, "valid_within_budget": 0}
        self.assertEqual(judge(True, 100, solvable)[0], "pass")
        self.assertEqual(judge(False, None, unsolvable)[0], "pass")
        self.assertEqual(judge(False, 100, unsolvable)[0], "pass")      # מצא את הזול ביותר וויתר בצדק
        self.assertEqual(judge(False, 121, unsolvable)[0], "partial")   # צדק, אבל ויתר מוקדם מדי
        self.assertEqual(judge(False, None, solvable)[0], "fail")
        self.assertEqual(judge(True, 100, unsolvable)[0], "bug")


class TestUI(unittest.TestCase):

    def test_ui_loads_without_errors(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_file(str(Path(__file__).parent.parent / "ui.py"), default_timeout=30).run()
        self.assertFalse(app.exception, app.exception)
        for scenario in SCENARIOS:  # כל תרחיש נטען בלי שגיאה, כולל הלא-תקין
            app.sidebar.selectbox[0].set_value(scenario["id"]).run()
            self.assertFalse(app.exception, f"{scenario['id']}: {app.exception}")

    def test_sandbox_buttons(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_file(str(Path(__file__).parent.parent / "ui.py"), default_timeout=30).run()
        app.multiselect(key="sb_mains").set_value(VALID_MENU["mains"]).run()
        for button in app.button:
            if button.label in ("calculate_cost", "check_guests", "finalize_menu"):
                button.click().run()
                self.assertFalse(app.exception, app.exception)


if __name__ == "__main__":
    unittest.main()
