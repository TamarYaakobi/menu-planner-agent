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
from openai import OpenAI, RateLimitError

DEFAULT_MODEL = "gpt-4o-mini"
RATE_LIMIT_MAX_WAIT = 60  # שניות. המתנה ארוכה יותר = מכסה יומית, ואין טעם לחכות
RATE_LIMIT_RETRIES = 5


class ConfigError(Exception):
    """הגדרות חסרות - למשל אין מפתח API."""


def create_client() -> tuple[OpenAI, str]:
    """מחזיר לקוח מוכן ואת שם המודל. זורק ConfigError אם חסר מפתח."""
    load_dotenv()
    api_key = os.getenv("LLM_API_KEY")
    if not api_key or api_key == "your-key-here":
        raise ConfigError("לא נמצא מפתח API. צרי קובץ .env לפי .env.example והכניסי את המפתח.")

    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL") or None,  # None = השרת הרגיל של OpenAI
        timeout=30,
        max_retries=3,  # הספרייה מנסה שוב לבד על שגיאות רשת ועומס (429, 503)
    )
    return client, os.getenv("LLM_MODEL") or DEFAULT_MODEL


def ask_model(client, model: str, messages: list, tools: list,
              on_wait: Callable[[float], None] | None = None):
    """
    שולח שיחה למודל ומחזיר את ההודעה שלו.
    בחריגה ממכסה לדקה - מחכה וממשיך. במכסה יומית - זורק RateLimitError הלאה.
    """
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


if __name__ == "__main__":
    # בדיקת חיבור: python llm.py
    client, model = create_client()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "ענה במילה אחת: מה בירת צרפת?"}],
    )
    print(f"מודל: {model}")
    print(f"תשובה: {response.choices[0].message.content}")
