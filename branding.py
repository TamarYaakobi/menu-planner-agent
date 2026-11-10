"""
החתימה שלי בממשקים: הלוגו בתחתית סרגל הצד, עם קישור ל-GitHub, והאייקון בלשונית של הדפדפן.

הלוגו מוטמע בדף (base64) ולא נטען מקובץ, כי Streamlit לא מגיש קבצים מהתיקייה בלי הגדרה נוספת.
"""
import base64
from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).parent / "assets"
ICON = str(ASSETS / "tamar-build-icon.png")
GITHUB_URL = "https://github.com/TamarYaakobi"

# החתימה נדבקת לתחתית הסרגל, כדי שתיראה גם כשרשימת השיחות ארוכה
_CSS = """<style>
[data-testid="stElementContainer"]:has(.made-by) { position: sticky; bottom: 0; z-index: 2;
  padding-bottom: 1rem; background: var(--secondary-background-color, #FFF1E6); }
.made-by { margin-top: 0.6rem; padding: 0.7rem 0 0.8rem; border-top: 1px solid #F3D9C4; text-align: center;
  background: #FFF1E6; }
.made-by span { display: block; color: #8A6A55; font-size: 0.8rem; margin-bottom: 0.35rem; }
.made-by img { width: 170px; max-width: 80%; opacity: 0.9; transition: opacity 0.15s; }
.made-by a:hover img { opacity: 1; }
</style>"""


def sidebar_signature():
    """לקרוא בתוך with st.sidebar, אחרי כל שאר התוכן."""
    logo = base64.b64encode((ASSETS / "tamar-build-wordmark-light.svg").read_bytes()).decode()
    st.markdown(f'{_CSS}<div class="made-by"><span>פותח על ידי</span>'
                f'<a href="{GITHUB_URL}" target="_blank" rel="noopener" title="GitHub">'
                f'<img src="data:image/svg+xml;base64,{logo}" alt="tamar.build()"></a></div>',
                unsafe_allow_html=True)
