import os
import json
import logging
import httpx
from datetime import date
from pathlib import Path
from anthropic import Anthropic
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

logger = logging.getLogger(__name__)

client = Anthropic(api_key=os.environ["CLAUDE_API_KEY"])
conversations: dict = {}

DATA_DIR = Path(os.environ.get("DATA_PATH", "data"))
BOT_NAME = "andrizzy"
KNOWLEDGE_FILE = DATA_DIR / f"{BOT_NAME}-knowledge.json"

BRIDGE_URL = os.environ.get("BRIDGE_URL", "")
BRIDGE_SECRET = os.environ.get("BRIDGE_SECRET", "changeme")


def load_knowledge() -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if not KNOWLEDGE_FILE.exists():
        KNOWLEDGE_FILE.write_text(json.dumps({"entries": []}, indent=2))
    return json.loads(KNOWLEDGE_FILE.read_text())


def save_knowledge(data: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    KNOWLEDGE_FILE.write_text(json.dumps(data, indent=2))


def build_knowledge_prompt() -> str:
    entries = load_knowledge().get("entries", [])
    if not entries:
        return ""
    lines = ["\n\nADDITIONAL KNOWLEDGE (learned from Andre):"]
    for e in entries:
        lines.append(f"- {e['text']}")
    return "\n".join(lines)


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
        "Dedicated bots for deeper work:\n"
        "• Elevate bot — full CRM and sales tools\n"
        "• Personal bot — tasks, reminders, budget\n"
        "• University bot — assignments and coursework\n\n"
        "/claude [instruction] — run Claude Code on your Mac\n\n"
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
        "/claude [instruction] — run Claude Code on your Mac\n"
        "/learn [text] — teach me something to remember\n"
        "/knowledge — view everything I know\n"
        "/forget — clear all learned knowledge\n"
        "/clear — clear conversation history\n"
        "/help — this menu"
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conversations[update.effective_user.id] = []
    await update.message.reply_text("Cleared. Fresh start.")


async def learn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Usage: /learn [something to remember]\nExample: /learn I prefer bullet point formats in all responses")
        return
    data = load_knowledge()
    data["entries"].append({"text": text, "added": str(date.today())})
    save_knowledge(data)
    await update.message.reply_text(f"Got it. I'll remember: {text}")


async def show_knowledge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    entries = load_knowledge().get("entries", [])
    if not entries:
        await update.message.reply_text("Nothing learned yet. Use /learn [text] to teach me something.")
        return
    lines = [f"Stored knowledge ({len(entries)} entries)\n"]
    for i, e in enumerate(entries, 1):
        lines.append(f"{i}. {e['text']}  (added {e['added']})")
    await update.message.reply_text("\n".join(lines))


async def forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    save_knowledge({"entries": []})
    await update.message.reply_text("All learned knowledge cleared.")


async def claude_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    instruction = " ".join(context.args).strip()
    if not instruction:
        await update.message.reply_text("Usage: /claude [instruction]\nExample: /claude summarise my Elevate client list")
        return
    if not BRIDGE_URL:
        await update.message.reply_text("Bridge not set up yet. BRIDGE_URL is not configured.")
        return
    await update.message.reply_text("Sending to Claude Code on your Mac...")
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            await http.post(
                f"{BRIDGE_URL}/run",
                json={"secret": BRIDGE_SECRET, "instruction": instruction}
            )
        await update.message.reply_text("Task started. Result will come back shortly.")
    except Exception as e:
        await update.message.reply_text(f"Could not reach your Mac. Make sure the bridge server is running.\nError: {e}")


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
            system=SYSTEM_PROMPT + build_knowledge_prompt(),
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
    app.add_handler(CommandHandler("learn", learn))
    app.add_handler(CommandHandler("knowledge", show_knowledge))
    app.add_handler(CommandHandler("forget", forget))
    app.add_handler(CommandHandler("claude", claude_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app
