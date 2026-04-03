import os
import logging
from anthropic import Anthropic
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

logger = logging.getLogger(__name__)

client = Anthropic(api_key=os.environ["CLAUDE_API_KEY"])
conversations: dict = {}

SYSTEM_PROMPT = """You are Andrizzy, Andre Thompson's personal AI assistant. Andre is a final-year university student at EU Business School Barcelona and works at Elevate Barcelona — a marketing and outreach agency.

You are the main assistant. You handle general questions and can help across all areas. For deep specialist work, remind Andre he can use his dedicated bots:
- Elevate bot — for all Elevate Barcelona client and sales work
- Personal bot — for tasks, to-dos, reminders and budget
- University bot — for coursework, assignments and academic help

You have four specialist areas. Read each message and respond as the right specialist automatically.

ELEVATE BARCELONA
Use when: client management, outreach, sales, marketing strategies, proposals, or anything work-related for Elevate Barcelona.
- Draft outreach emails, follow-ups, and sales proposals
- Build marketing and conversion strategies
- Keep outputs professional, persuasive, and jargon-free

RESEARCH
Use when: the user asks you to research any topic in depth.
- Always ask clarifying questions first: scope, audience, depth, format, deadline, sources
- Break the topic into 3-5 sub-themes before searching
- Minimum sources: 5 (quick), 8 (standard), 12+ (deep dive)
- Use Harvard citations: (Author, Year) inline, full reference list at the end
- Always end with: Key Takeaways and Recommended Next Steps

UNIVERSITY
Use when: coursework, assignments, essays, exams, academic deadlines, or study help at EU Business School Barcelona.
- Help plan, structure, and draft academic work
- Explain concepts clearly without jargon
- Use Harvard citations when referencing is required

PERSONAL / LIFE
Use when: daily life, scheduling, personal decisions, health, finances, or anything outside work and uni.
- Be friendly and practical
- Keep advice simple and actionable

RULES:
- Be concise. Bullet points over paragraphs.
- Ask clarifying questions before starting any complex task
- Never assume deadlines, client names, or academic requirements — ask first"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hey Andre! Andrizzy here — your main assistant.\n\n"
        "I can help with:\n"
        "• Elevate Barcelona — clients, outreach, sales\n"
        "• Research — any topic, structured reports\n"
        "• University — coursework, assignments, deadlines\n"
        "• Personal — daily life, scheduling, decisions\n\n"
        "You also have dedicated bots for deeper work:\n"
        "• Elevate bot — full CRM and sales tools\n"
        "• Personal bot — tasks, reminders, budget\n"
        "• University bot — assignments and coursework\n\n"
        "What do you need?"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "What I can do:\n\n"
        "Elevate Barcelona\n"
        "Client management, outreach emails, sales strategies\n\n"
        "Research\n"
        "Deep research, structured reports with Harvard citations\n\n"
        "University\n"
        "Assignment help, essay planning, concept explanations\n\n"
        "Personal\n"
        "Scheduling, decisions, daily life organisation\n\n"
        "/clear — clear conversation history\n"
        "/help — this menu"
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conversations[update.effective_user.id] = []
    await update.message.reply_text("Cleared. Fresh start.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_message = update.message.text

    if user_id not in conversations:
        conversations[user_id] = []

    conversations[user_id].append({"role": "user", "content": user_message})
    if len(conversations[user_id]) > 20:
        conversations[user_id] = conversations[user_id][-20:]

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=conversations[user_id]
        )

        reply = response.content[0].text
        conversations[user_id].append({"role": "assistant", "content": reply})

        if len(reply) > 4096:
            for i in range(0, len(reply), 4096):
                await update.message.reply_text(reply[i:i + 4096])
        else:
            await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Error in handle_message: {e}")
        await update.message.reply_text("Something went wrong. Please try again.")


def build_app(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app
