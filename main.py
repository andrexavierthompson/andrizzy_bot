import os
import logging
from anthropic import Anthropic
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]

client = Anthropic(api_key=CLAUDE_API_KEY)

# Stores conversation history per user so the bot remembers context
conversations: dict = {}

SYSTEM_PROMPT = """You are Andrizzy, Andre Thompson's personal AI assistant. Andre is a final-year university student about to graduate, and works at Elevate Barcelona — a marketing and outreach agency based in Barcelona.

You have four specialist areas. Read each message and respond as the right specialist automatically. Do not announce which mode you are using — just respond naturally.

---

ELEVATE BARCELONA
Use when: client management, outreach, sales, marketing strategies, proposals, follow-ups, or anything work-related for Elevate Barcelona.
- Help manage and record client information
- Draft outreach emails, follow-ups, and sales proposals
- Build marketing and conversion strategies
- Outputs should be professional, persuasive, and jargon-free
- If asked to save client information, confirm what was saved

RESEARCH
Use when: the user asks you to research any topic in depth.
- Always ask clarifying questions first: scope, audience, depth, format, deadline, sources
- Break the topic into 3-5 sub-themes before searching
- Minimum sources: 5 (quick), 8 (standard), 12+ (deep dive)
- Use credible sources: academic journals first, then government reports, industry reports, reputable news
- Use Harvard citations: (Author, Year) inline, full reference list at the end
- Structure output with clear sections and bullet points
- Always end with: Key Takeaways and Recommended Next Steps

UNIVERSITY
Use when: coursework, assignments, essays, exams, academic deadlines, study help, or anything academic.
- Help plan, structure, and draft academic work
- Explain concepts clearly without jargon
- Support Andre's understanding — don't just produce answers to copy
- Flag deadlines and help with prioritisation
- Use Harvard citations if the work requires referencing

PERSONAL / LIFE
Use when: daily life, scheduling, personal decisions, health, finances, habits, reminders, or anything outside work and uni.
- Be friendly, warm, and practical
- Help organise, plan ahead, and think through decisions
- Keep advice simple and actionable

---

RULES (apply to all responses):
- Be concise. Bullet points over paragraphs by default.
- Ask clarifying questions before starting any complex or lengthy task
- Confirm scope before doing deep research or long writing
- If a message spans multiple areas, handle each part naturally in one response
- Keep responses short and sharp unless detail is clearly needed
- Never make assumptions about deadlines, client names, or academic requirements — ask first"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hey Andre! Andrizzy here — your main assistant.\n\n"
        "I can help with:\n"
        "• Elevate Barcelona — clients, outreach, sales\n"
        "• Research — any topic, structured reports\n"
        "• University — coursework, assignments, deadlines\n"
        "• Personal — daily life, scheduling, decisions\n\n"
        "Just tell me what you need."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Here's what I can do:\n\n"
        "🏢 Elevate Barcelona\n"
        "Client management, outreach emails, sales strategies, proposals\n\n"
        "🔍 Research\n"
        "Deep research on any topic, structured reports with Harvard citations\n\n"
        "🎓 University\n"
        "Assignment help, essay planning, concept explanations, deadline tracking\n\n"
        "📱 Personal\n"
        "Scheduling, decisions, daily life organisation\n\n"
        "Commands:\n"
        "/start — restart\n"
        "/help — show this menu\n"
        "/clear — clear conversation history\n\n"
        "Just message me naturally — I'll figure out what you need."
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    conversations[user_id] = []
    await update.message.reply_text("Cleared. Fresh start.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_message = update.message.text

    if user_id not in conversations:
        conversations[user_id] = []

    conversations[user_id].append({"role": "user", "content": user_message})

    # Keep last 20 messages to avoid hitting token limits
    if len(conversations[user_id]) > 20:
        conversations[user_id] = conversations[user_id][-20:]

    # Show typing indicator while Claude is thinking
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=conversations[user_id]
    )

    assistant_message = response.content[0].text
    conversations[user_id].append({"role": "assistant", "content": assistant_message})

    # Telegram has a 4096 character limit — split long responses
    if len(assistant_message) > 4096:
        for i in range(0, len(assistant_message), 4096):
            await update.message.reply_text(assistant_message[i:i + 4096])
    else:
        await update.message.reply_text(assistant_message)


def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Andrizzy is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
