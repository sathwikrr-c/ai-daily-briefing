#!/usr/bin/env python3
"""Daily briefing generator — fetches news, summarizes with Groq, builds HTML + emails it."""

import os
import re
import json
import smtplib
import requests
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from groq import Groq

# ── Config (all from environment variables / GitHub Secrets) ───────────────────
NEWS_API_KEY     = os.environ["NEWS_API_KEY"]
GROQ_API_KEY     = os.environ["GROQ_API_KEY"]
EMAIL_FROM       = os.environ["EMAIL_FROM"]        # your gmail address
EMAIL_PASSWORD   = os.environ["EMAIL_APP_PASSWORD"] # gmail app password
EMAIL_TO         = os.environ["EMAIL_TO"]           # can be same as EMAIL_FROM

client = Groq(api_key=GROQ_API_KEY)

TODAY = datetime.now().strftime("%B %d, %Y")

# ── Helpers ────────────────────────────────────────────────────────────────────
def fetch_articles(query: str, n: int = 12) -> list[dict]:
    # NewsAPI free plan has a 24h delay, so we skip the `from` filter
    # and sort by publishedAt to get the most recent available articles
    resp = requests.get(
        "https://newsapi.org/v2/everything",
        params={"q": query, "sortBy": "publishedAt",
                "language": "en", "pageSize": n, "apiKey": NEWS_API_KEY},
        timeout=10,
    )
    return [a for a in resp.json().get("articles", [])
            if a.get("title") and a.get("description") and "[Removed]" not in a.get("title", "")]


def ask_groq(prompt: str) -> str:
    for model in ["llama-3.3-70b-versatile", "llama-3.1-70b-versatile", "llama-3.1-8b-instant"]:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"⚠️ Model {model} failed: {e}, trying next...")
    raise RuntimeError("All Groq models failed.")


def parse_json(text: str):
    """Robustly extract JSON array or object from model output."""
    # 1. Strip markdown code fences
    text = re.sub(r"```(?:json)?", "", text).strip()

    # 2. Try parsing the whole response
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 3. Greedy search for array first, then object
    for pat in (r"\[[\s\S]*\]", r"\{[\s\S]*\}"):
        m = re.search(pat, text)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass

    print(f"⚠️ JSON parse failed. Raw response:\n{text[:300]}")
    return None


# ── Section builders ───────────────────────────────────────────────────────────
def top3_news(label: str, query: str) -> list[dict]:
    articles = fetch_articles(query)
    if not articles:
        return []
    pool = articles[:10]
    blurbs = "\n\n".join(
        f"[{i+1}] {a['title']} ({a['source']['name']})\n{a.get('description', '')}"
        for i, a in enumerate(pool)
    )
    prompt = f"""You are a sharp news curator. Today is {TODAY}.
From these {label} articles, pick the TOP 3 most important or interesting.

{blurbs}

Return ONLY a JSON array of exactly 3 objects with these keys:
- index     (the number in brackets, e.g. 1, 2, or 3)
- headline  (punchy, rewritten if needed, max 12 words)
- source    (publication name)
- summary   (2–3 clear sentences)
- why_it_matters  (1 sentence)
- so_what   (1 actionable or conversational takeaway)"""
    result = parse_json(ask_groq(prompt))
    if not isinstance(result, list):
        return []
    # attach URLs from original articles using the returned index
    for item in result:
        idx = item.get("index")
        if idx and isinstance(idx, int) and 1 <= idx <= len(pool):
            item["url"] = pool[idx - 1].get("url", "")
    return result


def stock_market_lesson() -> dict:
    prompt = f"""Today is {TODAY}. Teach a stock market concept to a complete beginner.

Pick ONE concept based on the day of the month (rotate through the list):
P/E Ratio, Dividend Yield, Market Cap, ETFs vs Mutual Funds, Bull vs Bear Market,
Dollar Cost Averaging, Index Funds, Earnings Reports, Bonds vs Stocks, Short Selling,
IPOs, Stock Splits, Portfolio Diversification, Moving Averages, Value vs Growth Investing,
Risk vs Return, Compound Interest, Options Basics, Bid-Ask Spread, 52-Week High/Low,
Sector Rotation, Margin Trading, EBITDA, Book Value, Free Cash Flow

Return ONLY a JSON object with these keys:
- concept       (the name)
- explanation   (3–4 sentences, zero jargon, like explaining to a 15-year-old)
- analogy       (a vivid real-world comparison that makes it stick)
- key_takeaway  (1 sentence to remember forever)"""
    result = parse_json(ask_groq(prompt))
    return result if isinstance(result, dict) else {}


def smart_conversation_starters() -> list[dict]:
    # Pull from sources Morning Brew itself aggregates from
    articles = fetch_articles(
        'site:businessinsider.com OR site:axios.com OR site:fastcompany.com OR site:bloomberg.com OR site:theathletic.com',
        15
    )
    if not articles:
        # fallback: broad interesting business/culture news
        articles = fetch_articles('business OR culture OR science OR economy interesting surprising', 15)

    pool = articles[:12]
    blurbs = "\n\n".join(
        f"[{i+1}] {a['title']} ({a['source']['name']})\n{a.get('description', '')}"
        for i, a in enumerate(pool)
    )

    prompt = f"""Today is {TODAY}. You write exactly like Morning Brew's "Be Smart in Conversations" section.

Morning Brew's style: witty, conversational, treats readers like smart friends. Each item is a real story that makes someone sound informed and interesting — not a trivia fact, but actual news or business insight repackaged with a human angle.

Here are today's real news articles to draw from:
{blurbs}

Pick the 3 most interesting/surprising stories that fit Morning Brew's vibe. Rewrite them in Morning Brew's tone — punchy, a little clever, zero fluff.

Return ONLY a JSON array of 3 objects with these keys:
- index       (the number in brackets, e.g. 1, 2, or 3)
- topic_emoji (emoji + short label, e.g. "💰 Business" or "🌍 World")
- headline    (catchy 1-liner, Morning Brew style)
- fact        (2–3 sentences written conversationally, like you're telling a smart friend)
- drop_it     (natural way to bring this up, e.g. "Next time someone asks about X, mention...")"""
    result = parse_json(ask_groq(prompt))
    if not isinstance(result, list):
        return []
    for item in result:
        idx = item.get("index")
        if idx and isinstance(idx, int) and 1 <= idx <= len(pool):
            item["url"] = pool[idx - 1].get("url", "")
    return result


def claude_code_section() -> dict:
    articles = fetch_articles(
        '"Claude" OR "Anthropic" OR "Claude Code" OR "AI coding assistant" OR "AI agent developer"', 10
    )
    pool = articles[:8]
    blurbs = "\n\n".join(
        f"[{i+1}] {a['title']}\n{a.get('description', '')}"
        for i, a in enumerate(pool)
    )
    prompt = f"""You are an expert on Claude Code and AI developer tools. Today is {TODAY}.

Recent AI/Claude-related news:
{blurbs}

Do two things:
1. Pick the 2–3 most relevant Claude / Anthropic / AI-coding stories above.
2. Share ONE killer Claude Code tip — something non-obvious and genuinely useful for a developer's daily workflow.

Return ONLY a JSON object with these keys:
- news: array of objects with (index — the number in brackets, headline, source, summary, why_it_matters)
- tip:  object with (title, description, example — a sample prompt or slash command to try right now)"""
    result = parse_json(ask_groq(prompt))
    if not isinstance(result, dict):
        return {"news": [], "tip": {}}
    for item in result.get("news", []):
        idx = item.get("index")
        if idx and isinstance(idx, int) and 1 <= idx <= len(pool):
            item["url"] = pool[idx - 1].get("url", "")
    return result


# ── HTML generation ────────────────────────────────────────────────────────────
def story_cards_html(stories: list[dict]) -> str:
    if not stories:
        return "<p class='empty'>No stories found today.</p>"
    html = ""
    for s in stories:
        read_more = f'<a class="read-more" href="{s["url"]}" target="_blank" rel="noopener">Read more →</a>' if s.get("url") else ""
        html += f"""
        <div class="card">
          <div class="card-headline">{s.get('headline','')}</div>
          <div class="card-source">{s.get('source','')}</div>
          <p class="card-summary">{s.get('summary','')}</p>
          <div class="card-meta">
            <span class="tag why">💡 Why it matters</span> {s.get('why_it_matters','')}
          </div>
          <div class="card-meta">
            <span class="tag so">⚡ So what</span> {s.get('so_what','')}
          </div>
          {read_more}
        </div>"""
    return html


def build_html(sections: dict) -> str:
    now = datetime.now().strftime("%I:%M %p · %B %d, %Y")

    # AI section
    ai_html = story_cards_html(sections.get("ai", []))

    # Markets section
    markets_html = story_cards_html(sections.get("markets", []))

    # Tech section
    tech_html = story_cards_html(sections.get("tech", []))

    # Claude Code section
    cc = sections.get("claude_code", {})
    cc_news_html = story_cards_html(cc.get("news", []))
    tip = cc.get("tip", {})
    tip_html = f"""
    <div class="tip-box">
      <div class="tip-title">🛠️ Tip: {tip.get('title','')}</div>
      <p>{tip.get('description','')}</p>
      <div class="tip-example"><code>{tip.get('example','')}</code></div>
    </div>""" if tip else ""

    # Stock lesson
    lesson = sections.get("stock_lesson", {})
    lesson_html = f"""
    <div class="lesson-box">
      <div class="lesson-concept">📖 Today's Concept: {lesson.get('concept','')}</div>
      <p>{lesson.get('explanation','')}</p>
      <div class="analogy"><strong>Think of it like:</strong> {lesson.get('analogy','')}</div>
      <div class="takeaway">💬 Remember: {lesson.get('key_takeaway','')}</div>
    </div>""" if lesson else ""

    # Smart conversations
    convos = sections.get("smart_convos", [])
    convos_html = ""
    for c in convos:
        convo_read_more = f'<a class="read-more" href="{c["url"]}" target="_blank" rel="noopener">Read more →</a>' if c.get("url") else ""
        convos_html += f"""
        <div class="convo-card">
          <div class="convo-topic">{c.get('topic_emoji','')}</div>
          <div class="convo-headline">{c.get('headline','')}</div>
          <p>{c.get('fact','')}</p>
          <div class="convo-drop"><em>💬 {c.get('drop_it','')}</em></div>
          {convo_read_more}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Your Morning Brief</title>
  <style>
    :root {{
      --bg: #f8f9fa; --surface: #fff; --border: #e9ecef;
      --text: #212529; --muted: #6c757d; --accent: #4f46e5;
      --why-bg: #eff6ff; --so-bg: #f0fdf4; --tip-bg: #faf5ff;
      --lesson-bg: #fff7ed; --convo-bg: #fdf2f8;
    }}
    [data-theme="dark"] {{
      --bg: #0f0f13; --surface: #1a1a24; --border: #2d2d3d;
      --text: #e2e8f0; --muted: #94a3b8; --accent: #818cf8;
      --why-bg: #1e1b4b; --so-bg: #052e16; --tip-bg: #2e1065;
      --lesson-bg: #431407; --convo-bg: #500724;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height: 1.6; }}
    .header {{ background: var(--accent); color: #fff; padding: 2rem; text-align: center; }}
    .header h1 {{ font-size: 1.8rem; font-weight: 700; }}
    .header .meta {{ opacity: 0.85; font-size: 0.9rem; margin-top: 0.3rem; }}
    .theme-btn {{ background: rgba(255,255,255,0.2); border: none; color: #fff; padding: 0.4rem 1rem; border-radius: 999px; cursor: pointer; font-size: 0.85rem; margin-top: 1rem; }}
    .container {{ max-width: 800px; margin: 0 auto; padding: 1.5rem 1rem; }}
    .section {{ margin-bottom: 2.5rem; }}
    .section-title {{ font-size: 1.2rem; font-weight: 700; margin-bottom: 1rem; padding-bottom: 0.5rem; border-bottom: 2px solid var(--accent); }}
    .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1.2rem; margin-bottom: 1rem; }}
    .card-headline {{ font-weight: 700; font-size: 1rem; margin-bottom: 0.2rem; }}
    .card-source {{ font-size: 0.78rem; color: var(--muted); margin-bottom: 0.6rem; text-transform: uppercase; letter-spacing: 0.05em; }}
    .card-summary {{ font-size: 0.92rem; margin-bottom: 0.7rem; color: var(--text); }}
    .card-meta {{ font-size: 0.88rem; margin-top: 0.5rem; padding: 0.5rem 0.7rem; border-radius: 8px; }}
    .tag {{ font-weight: 600; margin-right: 0.3rem; }}
    .card-meta:first-of-type {{ background: var(--why-bg); }}
    .card-meta:last-of-type {{ background: var(--so-bg); }}
    .tip-box {{ background: var(--tip-bg); border: 1px solid var(--border); border-radius: 12px; padding: 1.2rem; }}
    .tip-title {{ font-weight: 700; margin-bottom: 0.5rem; }}
    .tip-example {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 0.7rem; margin-top: 0.7rem; font-size: 0.88rem; overflow-x: auto; }}
    .tip-example code {{ font-family: 'Fira Code', monospace; }}
    .lesson-box {{ background: var(--lesson-bg); border: 1px solid var(--border); border-radius: 12px; padding: 1.2rem; }}
    .lesson-concept {{ font-weight: 700; font-size: 1rem; margin-bottom: 0.7rem; }}
    .analogy {{ margin-top: 0.7rem; font-size: 0.9rem; padding: 0.6rem 0.8rem; background: var(--surface); border-radius: 8px; border-left: 3px solid var(--accent); }}
    .takeaway {{ margin-top: 0.7rem; font-size: 0.9rem; color: var(--accent); font-weight: 600; }}
    .convo-card {{ background: var(--convo-bg); border: 1px solid var(--border); border-radius: 12px; padding: 1.1rem; margin-bottom: 1rem; }}
    .convo-topic {{ font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin-bottom: 0.3rem; }}
    .convo-headline {{ font-weight: 700; margin-bottom: 0.5rem; }}
    .convo-drop {{ font-size: 0.88rem; margin-top: 0.6rem; color: var(--muted); }}
    .read-more {{ display: inline-block; margin-top: 0.7rem; font-size: 0.85rem; font-weight: 600; color: var(--accent); text-decoration: none; }}
    .read-more:hover {{ text-decoration: underline; }}
    .empty {{ color: var(--muted); font-style: italic; }}
    .read-time {{ font-size: 0.82rem; color: var(--muted); margin-bottom: 1.5rem; text-align: center; }}
    footer {{ text-align: center; padding: 2rem; color: var(--muted); font-size: 0.82rem; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>☀️ Your Morning Brief</h1>
    <div class="meta">Generated at {now}</div>
    <button class="theme-btn" onclick="toggleTheme()">🌙 Dark mode</button>
  </div>
  <div class="container">
    <p class="read-time">⏱ ~12 min read · 6 sections</p>

    <div class="section">
      <div class="section-title">🤖 AI</div>
      {ai_html}
    </div>

    <div class="section">
      <div class="section-title">📈 Markets</div>
      {markets_html}
    </div>

    <div class="section">
      <div class="section-title">💻 Tech</div>
      {tech_html}
    </div>

    <div class="section">
      <div class="section-title">🛠️ Claude Code</div>
      {cc_news_html}
      {tip_html}
    </div>

    <div class="section">
      <div class="section-title">📚 Learn: Stock Market</div>
      {lesson_html}
    </div>

    <div class="section">
      <div class="section-title">💬 Be Smart in Conversations</div>
      {convos_html}
    </div>
  </div>
  <footer>Built with Claude Code · Powered by Gemini &amp; NewsAPI · Delivered daily at 6 AM</footer>
  <script>
    function toggleTheme() {{
      const html = document.documentElement;
      const isDark = html.getAttribute('data-theme') === 'dark';
      html.setAttribute('data-theme', isDark ? '' : 'dark');
      document.querySelector('.theme-btn').textContent = isDark ? '🌙 Dark mode' : '☀️ Light mode';
    }}
  </script>
</body>
</html>"""


# ── Email ──────────────────────────────────────────────────────────────────────
def send_email(html_content: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"☀️ Your Morning Brief — {TODAY}"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.ehlo()
        server.starttls()
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    print("✅ Email sent.")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("🔄 Fetching AI news...")
    ai = top3_news("AI", "artificial intelligence OR machine learning OR LLM OR GPT")

    print("🔄 Fetching Markets news...")
    markets = top3_news("Markets", "stock market OR S&P 500 OR Fed interest rates OR Wall Street OR earnings")

    print("🔄 Fetching Tech news...")
    tech = top3_news("Tech", "technology startup OR Silicon Valley OR big tech OR software engineering")

    print("🔄 Building Claude Code section...")
    claude_code = claude_code_section()

    print("🔄 Generating stock market lesson...")
    lesson = stock_market_lesson()

    print("🔄 Generating conversation starters...")
    convos = smart_conversation_starters()

    sections = {
        "ai": ai,
        "markets": markets,
        "tech": tech,
        "claude_code": claude_code,
        "stock_lesson": lesson,
        "smart_convos": convos,
    }

    print("🔄 Building HTML...")
    html = build_html(sections)

    with open("index.html", "w") as f:
        f.write(html)
    print("✅ index.html written.")

    print("🔄 Sending email...")
    try:
        send_email(html)
    except Exception as e:
        print(f"⚠️ Email failed (website still deployed): {e}")


if __name__ == "__main__":
    main()
