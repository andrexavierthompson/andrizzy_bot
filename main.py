import os
import json
import logging
import httpx
from datetime import date
from pathlib import Path
from anthropic import Anthropic
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
import elevate as elevate_bot
import university as university_bot
import personal as personal_bot
import usage_tracker

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


TOOLS = [
    {
        "name": "route_to_specialist",
        "description": (
            "Route this message to a specialist bot that has dedicated data tools. "
            "Use when the request involves: saving/reading clients or outreach (elevate), "
            "saving/reading assignments or academic deadlines (university), "
            "saving/reading tasks, budget, or subscriptions (personal). "
            "Do NOT route for general chat, research, quick answers, or advice — handle those directly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "bot": {
                    "type": "string",
                    "enum": ["elevate", "university", "personal"],
                    "description": "Which specialist to route to"
                },
                "reason": {"type": "string", "description": "One-line reason for routing"}
            },
            "required": ["bot"]
        }
    }
]


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

ROUTING:
- If the request requires saving or reading data (clients, assignments, tasks, budget, subscriptions), use route_to_specialist
- Handle everything else directly: general questions, research, quick advice, strategy, explanations
- When routing, do not reply with text — just call the tool

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
        "/claude [instruction] — run Claude Code on your Mac\n"
        "/usage — API credit usage\n\n"
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
        "/usage — Anthropic API credit usage and cost estimate\n"
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


async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = usage_tracker.load_usage()
    input_tok = data["input_tokens"]
    output_tok = data["output_tokens"]
    calls = data["calls"]
    since = data.get("since", "unknown")
    input_cost, output_cost, total_cost = usage_tracker.calc_cost(input_tok, output_tok)
    balance = data.get("balance", 4.43)
    remaining = max(0, balance - total_cost)
    await update.message.reply_text(
        f"API Usage (since {since})\n\n"
        f"Tokens used:\n"
        f"  Input:  {input_tok:,}\n"
        f"  Output: {output_tok:,}\n"
        f"  Calls:  {calls}\n\n"
        f"Estimated cost (this session): ${total_cost:.4f}\n"
        f"  Input:  ${input_cost:.4f}\n"
        f"  Output: ${output_cost:.4f}\n\n"
        f"Balance: ${balance:.2f}\n"
        f"Remaining estimate: ~${remaining:.2f}\n\n"
        f"/usage_reset — reset counter\n"
        f"/usage_setbalance [amount] — update balance after top-up"
    )


async def usage_setbalance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /usage_setbalance [amount]\nExample: /usage_setbalance 10.00")
        return
    try:
        amount = float(context.args[0].replace("$", "").replace(",", ""))
    except ValueError:
        await update.message.reply_text("Invalid amount. Example: /usage_setbalance 10.00")
        return
    usage_tracker.set_balance(amount)
    await update.message.reply_text(f"Balance updated to ${amount:.2f}. Tracker will count down from here.")


async def usage_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    old = usage_tracker.load_usage()
    _, _, old_cost = usage_tracker.calc_cost(old["input_tokens"], old["output_tokens"])
    usage_tracker.reset_usage()
    await update.message.reply_text(
        f"Counter reset.\n"
        f"Previous total: {old['calls']} calls, ${old_cost:.4f} estimated"
    )


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
        messages = list(conversations[user_id])
        route_target = None
        final_reply = ""

        while True:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=SYSTEM_PROMPT + build_knowledge_prompt(),
                tools=TOOLS,
                messages=messages
            )
            usage_tracker.track_usage(response.usage.input_tokens, response.usage.output_tokens)

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        if block.name == "route_to_specialist":
                            route_target = block.input.get("bot")
                            result = f"Routing to {route_target} specialist."
                        else:
                            result = "Unknown tool."
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result
                        })
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
            else:
                for block in response.content:
                    if hasattr(block, "text"):
                        final_reply = block.text
                break

        if route_target:
            conversations[user_id].append({"role": "assistant", "content": f"[Routed to {route_target} specialist]"})
            specialist_convs = {
                "elevate": elevate_bot.conversations,
                "university": university_bot.conversations,
                "personal": personal_bot.conversations,
            }[route_target]
            specialist_run = {
                "elevate": elevate_bot._run_claude,
                "university": university_bot._run_claude,
                "personal": personal_bot._run_claude,
            }[route_target]

            if user_id not in specialist_convs:
                specialist_convs[user_id] = []
            specialist_convs[user_id].append({"role": "user", "content": user_message})
            if len(specialist_convs[user_id]) > 20:
                specialist_convs[user_id] = specialist_convs[user_id][-20:]

            await specialist_run(user_id, list(specialist_convs[user_id]), update, context)
        elif final_reply:
            conversations[user_id].append({"role": "assistant", "content": final_reply})
            if len(final_reply) > 4096:
                for i in range(0, len(final_reply), 4096):
                    await update.message.reply_text(final_reply[i:i + 4096])
            else:
                await update.message.reply_text(final_reply)

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
    app.add_handler(CommandHandler("usage", usage_command))
    app.add_handler(CommandHandler("usage_reset", usage_reset))
    app.add_handler(CommandHandler("usage_setbalance", usage_setbalance))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app
