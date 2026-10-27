"""
תרחישי בדיקה לאג'נט - רגילים ומקרי קצה.
expect: מה התוצאה הנכונה ("success", "failure", או "invalid" לקלט שצריך להיפסל לפני הריצה).
"""

_FRIDAY_GUESTS = [
    {"name": "דנה", "allergies": ["nuts"]},
    {"name": "יוסי", "diet": "vegetarian"},
    {"name": "מירב", "dislikes": ["פטריות", "עגבניות"]},
    {"name": "אבי", "allergies": ["gluten"]},
]

SCENARIOS = [
    {
        "id": "friday",
        "title": "ארוחת שישי (רגיל)",
        "description": "4 אורחים עם אילוצים שונים, תקציב צפוף. התרחיש של ההדגמה.",
        "expect": "success",
        "request": {"num_guests": 10, "budget": 160, "guests": _FRIDAY_GUESTS},
    },
    {
        "id": "generous",
        "title": "תקציב נדיב",
        "description": "אותם אורחים, תקציב גבוה. האג'נט אמור לסיים מהר, בלי סיבובי תיקון מיותרים.",
        "expect": "success",
        "request": {"num_guests": 10, "budget": 400, "guests": _FRIDAY_GUESTS},
    },
    {
        "id": "edge_budget",
        "title": "על הגבול",
        "description": "התקציב גבוה בשקל אחד בלבד מהתפריט התקין הזול ביותר. רק פתרון אחד או שניים עובדים.",
        "expect": "success",
        "request": {"num_guests": 10, "budget": 149, "guests": _FRIDAY_GUESTS},
    },
    {
        "id": "impossible_budget",
        "title": "תקציב בלתי אפשרי",
        "description": "גם התפריט הזול ביותר יקר מהתקציב. האג'נט צריך לזהות ולדווח, לא להמשיך לנסות.",
        "expect": "failure",
        "request": {"num_guests": 10, "budget": 90,
                    "guests": [{"name": "יוסי", "diet": "vegan"}, {"name": "אבי", "allergies": ["gluten"]}]},
    },
    {
        "id": "conflicting",
        "title": "אילוצים סותרים",
        "description": "אורח טבעוני שלא אוהב עדשים - והעיקרית הטבעונית היחידה היא קארי עדשים. "
                       "אין פתרון בגלל ההעדפות, לא בגלל הכסף.",
        "expect": "failure",
        "request": {"num_guests": 8, "budget": 500,
                    "guests": [{"name": "נועה", "diet": "vegan", "dislikes": ["עדשים"]}]},
    },
    {
        "id": "many_constraints",
        "title": "הרבה אילוצים",
        "description": "5 אורחים, כל אחד עם אלרגיה אחרת, ואחת טבעונית. מעט מאוד מנות מתאימות לכולם.",
        "expect": "success",
        "request": {"num_guests": 12, "budget": 250, "guests": [
            {"name": "שירה", "diet": "vegan"},
            {"name": "עומר", "allergies": ["gluten"]},
            {"name": "ליאת", "allergies": ["nuts"]},
            {"name": "רון", "allergies": ["dairy", "eggs"]},
            {"name": "מאיה", "allergies": ["fish"], "dislikes": ["בצל"]},
        ]},
    },
    {
        "id": "single_guest",
        "title": "סועד יחיד",
        "description": "ארוחה לאדם אחד. בודק שהחישובים עובדים גם בכמויות קטנות.",
        "expect": "success",
        "request": {"num_guests": 1, "budget": 20, "guests": []},
    },
    {
        "id": "big_party",
        "title": "50 סועדים",
        "description": "אירוע גדול עם תקציב צפוף. בודק שהעלות גדלה נכון עם מספר הסועדים.",
        "expect": "success",
        "request": {"num_guests": 50, "budget": 650,
                    "guests": [{"name": "יוסי", "diet": "vegetarian"}]},
    },
    {
        "id": "unknown_dislike",
        "title": "העדפה למרכיב שלא קיים",
        "description": "אורח שלא אוהב כוסברה - שאין באף מנה. ההעדפה לא אמורה להשפיע על כלום.",
        "expect": "success",
        "request": {"num_guests": 6, "budget": 200, "guests": [{"name": "גיל", "dislikes": ["כוסברה"]}]},
    },
    {
        "id": "invalid_input",
        "title": "קלט לא תקין",
        "description": "אלרגיה שלא קיימת ותקציב שלילי. צריך להיפסל לפני שהאג'נט רץ, בלי לבזבז קריאות למודל.",
        "expect": "invalid",
        "request": {"num_guests": 5, "budget": -10, "guests": [{"name": "דני", "allergies": ["פיצה"]}]},
    },
]
