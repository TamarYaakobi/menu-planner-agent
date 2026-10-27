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


def guest(name="אורח", allergies=(), diet=None, dislikes=(), likes=()):
    return {"name": name, "allergies": list(allergies), "diet": diet, "dislikes": list(dislikes),
            "likes": list(likes)}


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

    def test_new_allergens(self):
        # סויה, שומשום ובוטנים - פעם היו "לא נתמכים", עכשיו אלרגיות רגילות
        for text, allergen in (("רון רגיש לסויה", "soy"), ("דנה אלרגית לשומשום", "sesame"),
                               ("אבא אלרגי לבוטנים", "peanuts"), ("בלי טחינה בבקשה, אלרגיה", "sesame")):
            with self.subTest(text):
                self.assertProblem(text, parsed())
                self.assertProblem(text, parsed([guest(allergies=[allergen])]), False)
        # בוטנים הם לא אגוזים: מי שאלרגי לבוטנים ומסומן nuts - עדיין חסר
        self.assertProblem("אבא אלרגי לבוטנים", parsed([guest(allergies=["nuts"])]))

    def test_approximate_budget(self):
        from goal_parser import is_approximate
        for text, expected in (("תקציב של בערך 400", True), ("בערך 400 שקל", True), ("כ-400 ש\"ח", True),
                               ("around 400 shekels", True), ("עד 400 שקל", False), ("400 שקל", False),
                               ("כ-10 אנשים, 300 שקל", False)):
            with self.subTest(text):
                self.assertEqual(is_approximate(text), expected)
        request, _ = build_request({"num_guests": 4, "budget_total": 400}, approximate=True)
        self.assertEqual((request["target_budget"], request["budget"]), (400, 450))
        MenuRequest.from_dict(request)  # תקין

    def test_model_flag_or_text_marks_approximate(self):
        # הקוד מזהה "בערך" גם אם המודל לא סימן, והמודל יכול לסמן גם אם הקוד לא זיהה
        result = parse_goal(FakeClient(submit(parsed(budget_total=400))), "fake", "4 איש, תקציב של בערך 400")
        self.assertEqual(result.request["target_budget"], 400)
        self.assertIn("בקירוב", " ".join(result.assumptions))
        result = parse_goal(FakeClient(submit(parsed(budget_total=400, budget_approximate=True))), "fake",
                            "4 איש, יש לי בערך 400")
        self.assertEqual(result.request["budget"], 450)

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
        self.assertEqual(len(detect_unsupported("סבתא סוכרתית, וארוחה כשרה", parsed())), 2)
        self.assertEqual(detect_unsupported("ארוחה כשרה", parsed(unsupported=["כשרות"])), [])
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

    AUNT = "סבתא שלי לא אוהבת חריף, ודודה שלי שאוהבת חסה"

    def test_like_inverted_into_dislike_is_caught(self):
        # באג אמיתי: "שאוהבת חסה" פוענח כ"לא אוהבת חסה"
        self.assertProblem(self.AUNT, parsed([guest("דודה", dislikes=["חסה"])]))
        self.assertProblem(self.AUNT, parsed([guest("דודה", likes=["חסה"])]), False)

    def test_same_food_liked_by_one_disliked_by_another(self):
        text = "נועה אוהבת שקשוקה ורון לא אוהב שקשוקה"
        self.assertProblem(text, parsed([guest("נועה", likes=["שקשוקה"]), guest("רון", dislikes=["שקשוקה"])]), False)

    def test_dropped_like_is_caught(self):
        self.assertProblem(self.AUNT, parsed([guest("דודה")]))

    def test_like_detection_edge_cases(self):
        from goal_parser import liked_words
        self.assertEqual(liked_words("דני אוהב מאוד את השקשוקה"), ["שקשוקה"])
        self.assertEqual(liked_words("רון לא אוהב עוף"), [])          # שלילה
        self.assertEqual(liked_words("סבתא שלא אוהבת עוף"), [])       # שלילה עם "שלא"
        self.assertEqual(liked_words("מיכל אוהבת לבשל"), [])          # לא מאכל
        self.assertEqual(liked_words("סבא אוהב חריף"), [])            # לא במאגר - אין מה לעשות איתו

    def test_like_mention_is_not_an_allergy(self):
        self.assertProblem("דנה אוהבת ביצים", parsed([guest(likes=["ביצים"])]), False)


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


class TestUpdates(unittest.TestCase):
    """עדכון לבקשה קיימת: המודל מקבל את הבקשה המובנית + ההודעה החדשה, והקוד בודק מה השתנה."""

    def setUp(self):
        from goal_parser import ParsedGoal
        self.previous = ParsedGoal("ok", request={"num_guests": 4, "budget": 300, "guests": [
            guest("דני", allergies=["dairy"]), guest("סבתא", likes=["חסה"])]})

    def update(self, text, *replies):
        client = FakeClient(*replies)
        return parse_goal(client, "fake", text, previous=self.previous), client

    def test_model_gets_current_request_and_only_the_new_message(self):
        same_guests = [guest("דני", allergies=["dairy"]), guest("סבתא", likes=["חסה"])]
        result, client = self.update("בעצם התקציב 200", submit(parsed(same_guests, num_guests=4, budget_total=200)))
        sent = client.requests[0][1]["content"]
        self.assertIn("\"budget_total\": 300", sent)     # הבקשה הנוכחית, מובנית
        self.assertIn("בעצם התקציב 200", sent)
        self.assertEqual(result.changes, ["תקציב: מ-300 ל-200"])
        self.assertEqual(result.warnings, [])

    def test_allergy_lost_in_update_is_sent_back(self):
        # הבאג מהשיחה הארוכה: עדכון של תקציב "איבד" את האלרגיה של דני
        lost = parsed([guest("דני"), guest("סבתא", likes=["חסה"])], num_guests=4, budget_total=200)
        kept = parsed([guest("דני", allergies=["dairy"]), guest("סבתא", likes=["חסה"])], num_guests=4, budget_total=200)
        result, client = self.update("בעצם התקציב 200", submit(lost), submit(kept))
        self.assertEqual((result.status, result.attempts), ("ok", 2))
        self.assertIn("חלב", client.requests[1][-1]["content"])

    def test_removing_on_purpose_is_allowed(self):
        without_dani = parsed([guest("סבתא", likes=["חסה"])], num_guests=3, budget_total=300)
        result, _ = self.update("דני לא מגיע", submit(without_dani))
        self.assertEqual(result.warnings, [])
        self.assertIn("הוסר/ה: דני", result.changes)

    def test_changes_are_described(self):
        from goal_parser import describe_changes
        old = {"num_guests": 4, "budget": 300, "guests": [guest("דני", allergies=["dairy"])]}
        new = {"num_guests": 5, "budget": 450, "target_budget": 400,
               "guests": [guest("דני", allergies=["dairy", "nuts"]), guest("נועה", diet="vegan")]}
        changes = describe_changes(old, new)
        self.assertIn("סועדים: מ-4 ל-5", changes)
        self.assertIn("תקציב: מ-300 ל-בערך 400 (עד 450)", changes)
        self.assertTrue(any(c.startswith("נוסף/ה: נועה") for c in changes))
        self.assertTrue(any(c.startswith("דני:") and "אגוזים" in c for c in changes))


class TestUnderstandGoalConversation(unittest.TestCase):
    """השיחה בטרמינל: שאלות על מידע חסר, ואישור לפני ריצה."""

    def run_conversation(self, replies, answers=(), interactive=True, auto_yes=False):
        from unittest import mock
        from agent import understand_goal
        client = FakeClient(*replies)
        answers = list(answers)
        with mock.patch("builtins.input", side_effect=lambda prompt="": answers.pop(0)) as fake_input, \
                mock.patch("builtins.print"):
            result, _ = understand_goal(client, "fake", "אני רוצה לארח", interactive, auto_yes)
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
