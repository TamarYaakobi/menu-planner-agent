"""
בדיקות לפענוח מטרה בשפה חופשית - בלי מודל אמיתי.
רשת הביטחון נבדקת ישירות, והלולאה נבדקת עם "מודל מזויף" שטועה בכוונה.
(הבדיקה מול מודל אמיתי: python evaluate.py --goals)
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from goal_parser import (MAX_ATTEMPTS, build_request, cross_check, detect_unsupported,  # noqa: E402
                         parse_goal, unknown_dislikes)
from test_agent import FakeClient, call, reply  # noqa: E402
from tools import MenuRequest  # noqa: E402


def guest(name="אורח", allergies=(), diet=None, dislikes=()):
    return {"name": name, "allergies": list(allergies), "diet": diet, "dislikes": list(dislikes)}


def parsed(guests=(), num_guests=10, budget_total=200, **extra):
    return {"num_guests": num_guests, "budget_total": budget_total, "guests": list(guests), **extra}


class TestCrossCheck(unittest.TestCase):
    """רשת הביטחון: הטקסט המקורי מזכיר משהו שאין לו זכר בבקשה."""

    def assertProblem(self, text, data, expected=True):
        problems = cross_check(text, data)
        self.assertEqual(bool(problems), expected, f"{text!r} -> {problems}")

    def test_dropped_allergy_is_caught(self):
        self.assertProblem("דני צליאק", parsed())
        self.assertProblem("דני צליאק", parsed([guest(allergies=["gluten"])]), False)

    def test_hebrew_prefixes(self):
        for text in ("אלרגי לגלוטן", "ולגלוטן", "אלרגית לביצים", "רגיש ללקטוז", "אלרגי לאגוזים", "ומהדגים"):
            with self.subTest(text):
                self.assertProblem(text, parsed())

    def test_words_that_only_look_like_allergens(self):
        for text in ("חלב קוקוס זה בסדר", "צריך הרבה חלבון", "הוא מדגיש שזה חשוב", "אגס וענבים"):
            with self.subTest(text):
                self.assertProblem(text, parsed(), False)

    def test_assumption_alone_does_not_cover_an_allergy(self):
        # באג אמיתי מהבדיקות: המודל כתב רק הנחה, ולא סימן אף אורח
        data = parsed(assumptions=["הנחתי שאף אחד לא אלרגי לגלוטן"])
        self.assertProblem("דני צליאק", data)

    def test_dislike_covers_a_mention(self):
        self.assertProblem("עדי לא אוהבת ביצה", parsed([guest(dislikes=["ביצים"])]), False)

    def test_vegan_covers_eggs_and_dairy(self):
        self.assertProblem("נועה טבעונית, בלי ביצים ובלי חלב", parsed([guest(diet="vegan")]), False)

    def test_dropped_diet_is_caught(self):
        self.assertProblem("חצי מהם צמחונים", parsed([guest()], assumptions=["חצי צמחונים"]))
        self.assertProblem("חצי מהם צמחונים", parsed([guest(diet="vegetarian")]), False)
        self.assertProblem("יוסי לא אוכל בשר", parsed())
        self.assertProblem("יוסי צמחוני", parsed([guest(diet="vegan")]), False)  # מחמיר יותר - בסדר

    def test_unsupported_restriction_is_added_by_code(self):
        # מודל שהתעקש לא לרשום "סוכרתית" - הקוד מוסיף בעצמו
        self.assertEqual(len(detect_unsupported("סבתא סוכרתית, אבא אלרגי לבוטנים", parsed())), 2)
        self.assertEqual(detect_unsupported("אבא אלרגי לבוטנים", parsed(unsupported=["בוטנים"])), [])
        self.assertTrue(detect_unsupported("ארוחה כשרה", parsed()))
        # הנחה מוצגת כמידע, לא כאזהרה - לא מספיקה
        self.assertTrue(detect_unsupported("סבתא סוכרתית", parsed(assumptions=["סבתא סוכרתית, לא נלקח בחשבון"])))

    def test_stubborn_model_cannot_hide_unsupported(self):
        client = FakeClient(submit(parsed([guest("סבתא")])))
        result = parse_goal(client, "fake", "ארוחה ל-7, 180 שקל. סבתא סוכרתית")
        self.assertEqual((result.status, result.attempts), ("ok", 1))
        self.assertIn("סוכרתית", " ".join(result.unsupported))

    def test_budget_per_guest_needs_a_marker(self):
        # באג אמיתי מהבדיקות: "150₪" פוענח כ-150 לאדם
        self.assertProblem("6 סועדים, 150₪", parsed(budget_total=None, budget_per_guest=150))
        self.assertProblem("6 סועדים, 25₪ לאדם", parsed(budget_total=None, budget_per_guest=25), False)

    def test_explicit_per_guest_amount_must_not_be_total(self):
        self.assertProblem("8 אנשים, 40 ש\"ח לאדם", parsed(budget_total=40))
        self.assertProblem("ארוחה ל-6, לכל אחד יש העדפות", parsed(), False)


class TestBuildRequest(unittest.TestCase):

    def test_per_guest_budget_is_multiplied_in_code(self):
        request, missing = build_request({"num_guests": 8, "budget_per_guest": 40, "guests": []})
        self.assertEqual((request["budget"], missing), (320, []))

    def test_total_wins_over_per_guest(self):
        request, _ = build_request({"num_guests": 8, "budget_total": 300, "budget_per_guest": 40})
        self.assertEqual(request["budget"], 300)

    def test_missing_fields(self):
        self.assertEqual(build_request({"guests": []})[1], ["num_guests", "budget"])
        self.assertEqual(build_request({"budget_per_guest": 40})[1], ["num_guests"])

    def test_zero_means_unknown(self):
        # מודל כתב num_guests: 0 במקום null עבור "התקציב שלי הוא 500 שקל"
        self.assertEqual(build_request({"num_guests": 0, "budget_total": 500})[1], ["num_guests"])
        self.assertEqual(build_request({"num_guests": 5, "budget_total": 0})[1], ["budget"])

    def test_float_guest_count(self):
        request, _ = build_request({"num_guests": 12.0, "budget_total": 100})
        self.assertIs(type(request["num_guests"]), int)

    def test_garbage_is_left_for_validation(self):
        request, _ = build_request({"num_guests": "הרבה", "budget_per_guest": 40, "guests": ["x"]})
        with self.assertRaises(ValueError):
            MenuRequest.from_dict(request)


def submit(data, call_id="c1"):
    return reply(call("submit_request", data, call_id))


class TestParseGoalLoop(unittest.TestCase):

    def test_success(self):
        client = FakeClient(submit(parsed([guest("דני", allergies=["gluten"])])))
        result = parse_goal(client, "fake", "10 איש, 200 שקל, דני צליאק")
        self.assertEqual((result.status, result.attempts, result.warnings), ("ok", 1, []))

    def test_cross_check_sends_it_back_and_model_fixes(self):
        client = FakeClient(submit(parsed()), submit(parsed([guest("דני", allergies=["gluten"])])))
        result = parse_goal(client, "fake", "10 איש, 200 שקל, דני צליאק")
        self.assertEqual((result.status, result.attempts), ("ok", 2))
        feedback = json.loads(client.requests[1][-1]["content"])
        self.assertFalse(feedback["accepted"])
        self.assertIn("גלוטן", feedback["problems"][0])

    def test_stubborn_model_ends_with_visible_warning(self):
        client = FakeClient(*[submit(parsed())] * MAX_ATTEMPTS)
        result = parse_goal(client, "fake", "10 איש, 200 שקל, דני צליאק")
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.warnings, "בעיה שלא תוקנה חייבת להגיע למשתמש")
        self.assertEqual(result.attempts, MAX_ATTEMPTS)

    def test_invalid_request_is_retried_then_error(self):
        bad = parsed([guest(allergies=["פיצה"])])
        client = FakeClient(*[submit(bad)] * MAX_ATTEMPTS)
        result = parse_goal(client, "fake", "10 איש, 200 שקל")
        self.assertEqual(result.status, "error")
        self.assertIsNone(result.request)

    def test_no_tool_call_and_broken_json_are_retried(self):
        client = FakeClient(reply(content="הנה: 10 איש"), reply(call("submit_request", "{oops")),
                            submit(parsed()))
        result = parse_goal(client, "fake", "10 איש, 200 שקל")
        self.assertEqual((result.status, result.attempts), ("ok", 3))

    def test_missing_info_asks_without_retrying(self):
        client = FakeClient(submit(parsed(num_guests=None, budget_total=None)))
        result = parse_goal(client, "fake", "אני רוצה לארח את המשפחה")
        self.assertEqual(result.status, "needs_info")
        self.assertEqual(len(result.questions), 2)
        self.assertEqual(len(client.requests), 1)

    def test_off_topic(self):
        client = FakeClient(submit(parsed(off_topic=True)))
        self.assertEqual(parse_goal(client, "fake", "מה מזג האוויר?").status, "off_topic")

    def test_empty_and_huge_text_never_reach_the_model(self):
        client = FakeClient()  # בלי תשובות - קריאה למודל תיכשל
        self.assertEqual(parse_goal(client, "fake", "   ").status, "error")
        self.assertEqual(parse_goal(client, "fake", "א" * 5000).status, "error")
        self.assertEqual(client.requests, [])

    def test_every_tool_call_gets_an_answer(self):
        # Gemini דורש תשובה לכל קריאה לכלי, גם אם יש כמה באותה הודעה
        two_calls = reply(call("submit_request", parsed(), "a"), call("submit_request", parsed(), "b"))
        client = FakeClient(two_calls, submit(parsed([guest(allergies=["gluten"])])))
        parse_goal(client, "fake", "10 איש, 200 שקל, צליאק")
        answered = {m["tool_call_id"] for m in client.requests[1] if m["role"] == "tool"}
        self.assertEqual(answered, {"a", "b"})


class TestUnderstandGoalConversation(unittest.TestCase):
    """השיחה בטרמינל: שאלות על מידע חסר, ואישור לפני ריצה."""

    def run_conversation(self, replies, answers=(), interactive=True, auto_yes=False):
        from unittest import mock
        from agent import understand_goal
        client = FakeClient(*replies)
        answers = list(answers)
        with mock.patch("builtins.input", side_effect=lambda prompt="": answers.pop(0)) as fake_input, \
                mock.patch("builtins.print"):
            result = understand_goal(client, "fake", "אני רוצה לארח", interactive, auto_yes)
        return result, client, fake_input

    def test_asks_for_missing_info_then_confirms(self):
        result, client, _ = self.run_conversation(
            [submit(parsed(num_guests=None, budget_total=None)), submit(parsed(num_guests=12, budget_total=300))],
            answers=["12", "300", "כן"])
        self.assertEqual((result["num_guests"], result["budget"]), (12, 300))
        self.assertIn("12", client.requests[1][1]["content"])  # התשובות נוספו לטקסט

    def test_user_can_cancel(self):
        result, _, _ = self.run_conversation([submit(parsed())], answers=["לא"])
        self.assertIsNone(result)

    def test_yes_skips_confirmation_only_without_warnings(self):
        result, _, fake_input = self.run_conversation([submit(parsed())], interactive=False, auto_yes=True)
        self.assertIsNotNone(result)
        fake_input.assert_not_called()

        flagged = parsed(unsupported=["אלרגיה לבוטנים"])
        result, _, _ = self.run_conversation([submit(flagged)], interactive=False, auto_yes=True)
        self.assertIsNone(result, "--yes לא עוקף הגבלה שהמערכת לא בודקת")

    def test_missing_info_without_a_terminal_stops(self):
        result, _, fake_input = self.run_conversation(
            [submit(parsed(budget_total=None))], interactive=False)
        self.assertIsNone(result)
        fake_input.assert_not_called()


class TestUnknownDislikes(unittest.TestCase):

    def test_flags_only_unknown(self):
        request = {"guests": [guest(dislikes=["כוסברה", "פטריות"])]}
        self.assertEqual(unknown_dislikes(request), ["כוסברה"])


if __name__ == "__main__":
    unittest.main()
