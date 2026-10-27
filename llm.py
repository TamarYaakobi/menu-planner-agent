"""
חיבור למודל השפה.

משתמשים בספריית openai, אבל הכתובת והמודל מגיעים מקובץ .env - כך אותו קוד
עובד גם מול Gemini (שיש לו ממשק תואם OpenAI) וגם מול OpenAI עצמו.
"""
import os
import re
import time
from typing import Callable

from dotenv import load_dotenv
from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI, RateLimitError

DEFAULT_MODEL = "gpt-4o-mini"
RATE_LIMIT_MAX_WAIT = 60  # שניות. המתנה ארוכה יותר = מכסה יומית, ואין טעם לחכות
RATE_LIMIT_RETRIES = 5


class ConfigError(Exception):
    """הגדרות חסרות - למשל אין מפתח API."""


def create_client() -> tuple[OpenAI, str]:
    """מחזיר לקוח מוכן ואת שם המודל. זורק ConfigError אם חסר מפתח."""
    # override: הצ'אט רץ שעות, ומפתח שהוחלף ב-.env (למשל כשנגמרה מכסה) צריך להיכנס לתוקף בלי הפעלה מחדש.
    # בלי זה, load_dotenv לא דורס את המפתח הישן שכבר נטען
    load_dotenv(override=True)
    api_key = os.getenv("LLM_API_KEY")
    if not api_key or api_key == "your-key-here":
        raise ConfigError("לא נמצא מפתח API. צרי קובץ .env לפי .env.example והכניסי את המפתח.")

    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL") or None,  # None = השרת הרגיל של OpenAI
        timeout=30,
        # הספרייה מנסה שוב לבד על שגיאות רשת ועומס. מעט ניסיונות - כי יש מודלי גיבוי,
        # ועדיף לעבור אליהם מהר מאשר לחכות דקות למודל עמוס
        max_retries=1,
    )
    return client, os.getenv("LLM_MODEL") or DEFAULT_MODEL


def fallback_models() -> list[str]:
    """מודלי גיבוי מ-LLM_FALLBACK_MODELS (מופרדים בפסיק), לפי סדר העדיפות."""
    load_dotenv(override=True)
    return [m.strip() for m in (os.getenv("LLM_FALLBACK_MODELS") or "").split(",") if m.strip()]


def ask_model(client, model: str, messages: list, tools: list,
              on_wait: Callable[[float], None] | None = None,
              fallbacks: list[str] | None = None,
              on_fallback: Callable[[str, str, Exception], None] | None = None):
    """
    שולח שיחה למודל ומחזיר את ההודעה שלו.
    אם המודל לא זמין (עומס, השרת לא עונה, מכסה יומית) - עובר למודל הגיבוי הבא ברשימה.
    רק כשכל המודלים נכשלו, השגיאה נזרקת הלאה.
    """
    models = [model] + [m for m in fallbacks or [] if m != model]
    for i, current in enumerate(models):
        try:
            return _ask_one_model(client, current, messages, tools, on_wait)
        except UNAVAILABLE as e:
            if i == len(models) - 1:
                raise
            if on_fallback:
                on_fallback(current, models[i + 1], e)


# המודל לא זמין כרגע - אין טעם להמשיך לנסות אותו, כדאי לעבור לגיבוי.
# (שגיאות אחרות, כמו מפתח לא תקין או בקשה שגויה, יחזרו גם במודל אחר - ולכן לא מחליפים עליהן)
UNAVAILABLE = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)


def _ask_one_model(client, model: str, messages: list, tools: list, on_wait):
    """בחריגה ממכסה לדקה - מחכה וממשיך. במכסה יומית - זורק RateLimitError הלאה."""
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            response = client.chat.completions.create(model=model, messages=messages, tools=tools)
            return response.choices[0].message
        except RateLimitError as e:
            wait = retry_delay(e)
            if wait is None or wait > RATE_LIMIT_MAX_WAIT or attempt == RATE_LIMIT_RETRIES:
                raise
            if on_wait:
                on_wait(wait)
            time.sleep(wait + 1)


def retry_delay(error: RateLimitError) -> float | None:
    """Gemini כותב בגוף השגיאה כמה לחכות: 'retry in 5.7s'. מכסה יומית ('20h34m') לא תתאים."""
    match = re.search(r"retry in ([\d.]+)s", str(error))
    return float(match.group(1)) if match else None


def daily_quota_message(error: Exception) -> str | None:
    """
    "נגמרה המכסה היומית... תתחדש בעוד כ-11 שעות" - כשהשגיאה היא מכסה יומית (Gemini: '...PerDay...',
    'retry in 11h34m57s'). None לכל שגיאה אחרת - אז ייתכן שבאמת שווה לנסות שוב בעוד דקה.
    """
    text = str(error)
    if not isinstance(error, RateLimitError) or "PerDay" not in text:
        return None
    message = "נגמרה המכסה היומית של המודל"
    match = re.search(r"retry in (?:(\d+)h)?(?:(\d+)m)?[\d.]+s", text)
    if match:
        hours = int(match.group(1) or 0) + int(match.group(2) or 0) / 60
        message += (" - היא תתחדש בעוד פחות משעה" if hours < 1 else
                    " - היא תתחדש בעוד כשעה" if hours < 1.5 else f" - היא תתחדש בעוד כ-{round(hours)} שעות")
    return message + ". אפשר גם להוסיף מודל גיבוי ב-LLM_FALLBACK_MODELS, או להחליף מפתח ב-.env."


if __name__ == "__main__":
    # בדיקת חיבור: python llm.py
    client, model = create_client()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "ענה במילה אחת: מה בירת צרפת?"}],
    )
    print(f"מודל: {model}")
    print(f"תשובה: {response.choices[0].message.content}")
