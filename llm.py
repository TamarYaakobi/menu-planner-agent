"""
חיבור למודל השפה.

משתמשים בספריית openai, אבל הכתובת והמודל מגיעים מקובץ .env - כך אותו קוד
עובד גם מול Gemini (שיש לו ממשק תואם OpenAI) וגם מול OpenAI עצמו.
"""
import os

from dotenv import load_dotenv
from openai import OpenAI

DEFAULT_MODEL = "gpt-4o-mini"


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


if __name__ == "__main__":
    # בדיקת חיבור: python llm.py
    client, model = create_client()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "ענה במילה אחת: מה בירת צרפת?"}],
    )
    print(f"מודל: {model}")
    print(f"תשובה: {response.choices[0].message.content}")
