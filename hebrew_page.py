"""
מסמן דף Streamlit כעברית, מימין לשמאל, ו"לא לתרגום".

Streamlit מסמן כל דף כאנגלית (<html lang="en">). דפדפנים כמו Edge ו-Chrome רואים דף "באנגלית"
מלא עברית ומציעים - או מפעילים לבד - תרגום אוטומטי לעברית, שמשבש מילים שכבר בעברית:
"בננות" הפך ל"בנות", "פפריקה" ל"אפריקה", ו"גרם" ל"גראם" עם ניקוד.
"""
import streamlit as st

_SCRIPT = """<script>
const root = window.parent.document.documentElement;
root.lang = "he";
root.dir = "rtl";
root.setAttribute("translate", "no");
root.classList.add("notranslate");
const head = window.parent.document.head;
if (!head.querySelector('meta[name="google"]')) {
  const meta = window.parent.document.createElement("meta");
  meta.name = "google";
  meta.content = "notranslate";
  head.appendChild(meta);
}
</script>"""


def mark_page_hebrew():
    # st.iframe מחליף את components.html (שהוסר בגרסאות חדשות). בגרסאות ישנות שאין בהן st.iframe - הישן
    if hasattr(st, "iframe"):
        st.iframe(_SCRIPT, height=1)
    else:
        import streamlit.components.v1 as components
        components.html(_SCRIPT, height=0)
