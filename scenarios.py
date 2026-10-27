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
        "request": {"num_guests": 10, "budget": 146, "guests": _FRIDAY_GUESTS},
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
        "request": {"num_guests": 10, "budget": 137.8, "guests": _FRIDAY_GUESTS},
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
        "id": "every_allergy",
        "title": "כל סוגי האלרגיות",
        "description": "5 אורחים, לכל אחד אלרגיה אחרת (גלוטן, ביצים, חלב, אגוזים, דגים). "
                       "התקציב משאיר רק 2 תפריטים תקינים מתוך 192.",
        "expect": "success",
        "request": {"num_guests": 12, "budget": 156, "guests": [
            {"name": "עומר", "allergies": ["gluten"]},
            {"name": "רון", "allergies": ["eggs"]},
            {"name": "ליאת", "allergies": ["dairy"]},
            {"name": "דנה", "allergies": ["nuts"]},
            {"name": "מאיה", "allergies": ["fish"]},
        ]},
    },
    {
        "id": "allergic_to_all",
        "title": "אלרגית לכל החמישה",
        "description": "אורחת אחת אלרגית לכל האלרגנים, ואורח צמחוני. רק 2 תפריטים תקינים בתקציב.",
        "expect": "success",
        "request": {"num_guests": 10, "budget": 130, "guests": [
            {"name": "נגה", "allergies": ["gluten", "eggs", "dairy", "nuts", "fish"]},
            {"name": "יוסי", "diet": "vegetarian"},
        ]},
    },
    {
        "id": "picky_crowd",
        "title": "קהל בררן",
        "description": "5 אורחים עם 9 מרכיבים לא אהובים - כל החלפה עלולה לשבור העדפה של מישהו אחר. "
                       "רק 2 תפריטים תקינים בתקציב, בהפרש של שקל אחד.",
        "expect": "success",
        "request": {"num_guests": 12, "budget": 171.9, "guests": [
            {"name": "אבי", "dislikes": ["עגבניות", "פטריות"]},
            {"name": "בתיה", "dislikes": ["עדשים", "שום"]},
            {"name": "גיא", "dislikes": ["תפוחי אדמה", "אורז"]},
            {"name": "דבורה", "dislikes": ["בצל"]},
            {"name": "הילה", "diet": "vegetarian", "dislikes": ["ביצים"]},
        ]},
    },
    {
        "id": "picky_allergic",
        "title": "בררנים ואלרגיים",
        "description": "שילוב של העדפות, אלרגיות (אגוזים וחלב) וצמחונות. רק 2 תפריטים תקינים בתקציב.",
        "expect": "success",
        "request": {"num_guests": 12, "budget": 172.1, "guests": [
            {"name": "אבי", "dislikes": ["עגבניות", "מלפפונים", "קישואים"]},
            {"name": "בתיה", "dislikes": ["שוקולד", "תפוזים", "אבטיח"]},
            {"name": "גיא", "allergies": ["nuts", "dairy"]},
            {"name": "דבורה", "diet": "vegetarian", "dislikes": ["פלפל"]},
        ]},
    },
    {
        "id": "almost_possible",
        "title": "כמעט אפשרי",
        "description": "אותם בררנים ואלרגיים, והתקציב נמוך בשקל אחד מהתפריט הזול ביותר. "
                       "כדי לדווח נכון, האג'נט חייב להגיע בדיוק למינימום.",
        "expect": "failure",
        "request": {"num_guests": 12, "budget": 163, "guests": [
            {"name": "אבי", "dislikes": ["עגבניות", "מלפפונים", "קישואים"]},
            {"name": "בתיה", "dislikes": ["שוקולד", "תפוזים", "אבטיח"]},
            {"name": "גיא", "allergies": ["nuts", "dairy"]},
            {"name": "דבורה", "diet": "vegetarian", "dislikes": ["פלפל"]},
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
