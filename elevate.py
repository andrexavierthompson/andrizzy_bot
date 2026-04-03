import os
import json
import logging
from datetime import date
from pathlib import Path
from anthropic import Anthropic
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

logger = logging.getLogger(__name__)
client = Anthropic(api_key=os.environ["CLAUDE_API_KEY"])
conversations: dict = {}

DATA_DIR = Path("data")
CLIENTS_FILE = DATA_DIR / "clients.json"


def load_clients() -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if not CLIENTS_FILE.exists():
        CLIENTS_FILE.write_text(json.dumps({"clients": []}, indent=2))
    return json.loads(CLIENTS_FILE.read_text())


def save_clients(data: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    CLIENTS_FILE.write_text(json.dumps(data, indent=2))


TOOLS = [
    {
        "name": "save_client",
        "description": "Save a new client or update an existing client in the CRM. Use this whenever the user mentions a client they want to add or update.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Company or client name"},
                "contact": {"type": "string", "description": "Primary contact person's name"},
                "email": {"type": "string", "description": "Email address"},
                "phone": {"type": "string", "description": "Phone number"},
                "status": {
                    "type": "string",
                    "enum": ["prospect", "active", "inactive", "closed"],
                    "description": "Client status in the pipeline"
                },
                "industry": {"type": "string", "description": "Industry or sector"},
                "notes": {"type": "string", "description": "Notes, context, or background on this client"},
                "next_action": {"type": "string", "description": "Next step or action to take with this client"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "get_all_clients",
        "description": "Retrieve all clients from the CRM. Use this to show the client list or find a specific client.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "delete_client",
        "description": "Delete a client from the CRM permanently.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the client to delete"}
            },
            "required": ["name"]
        }
    }
]


def handle_tool(name: str, inputs: dict) -> str:
    data = load_clients()

    if name == "get_all_clients":
        return json.dumps(data)

    elif name == "save_client":
        clients = data["clients"]
        existing = next((c for c in clients if c["name"].lower() == inputs["name"].lower()), None)
        if existing:
            existing.update(inputs)
            save_clients(data)
            return f"Updated client: {inputs['name']}"
        else:
            inputs["added"] = str(date.today())
            clients.append(inputs)
            save_clients(data)
            return f"Saved new client: {inputs['name']}"

    elif name == "delete_client":
        original = len(data["clients"])
        data["clients"] = [c for c in data["clients"] if c["name"].lower() != inputs["name"].lower()]
        if len(data["clients"]) < original:
            save_clients(data)
            return f"Deleted: {inputs['name']}"
        return f"Client not found: {inputs['name']}"

    return "Unknown tool"


SYSTEM_PROMPT = """You are the Elevate Barcelona assistant for Andre Thompson. Elevate Barcelona is a marketing and outreach agency.

Your job covers everything related to Elevate Barcelona:

CRM & CLIENT MANAGEMENT
- Use your tools to save, update, retrieve, and delete client records
- Track pipeline status: prospect → active → inactive → closed
- Always check existing clients before adding a new one (use get_all_clients)
- When saving a client, confirm with a short summary of what was saved

OUTREACH & SALES
- Draft cold outreach emails and follow-up sequences
- Build messaging strategies tailored to specific prospects
- Suggest objection handling and closing techniques
- Create proposal content and pitch structures

MEETING PREP & STRATEGY
- Help prepare for client meetings with talking points and questions
- Draft post-meeting follow-up messages
- Suggest next steps after calls or meetings

RULES:
- Always be professional, persuasive, and concise
- Ask for missing details before drafting outreach (who is the prospect, what do they need, what's the angle?)
- Use bullet points for strategies and action lists
- When showing the client list, format it cleanly with status and contact name"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Elevate Barcelona assistant here.\n\n"
        "I can help with:\n"
        "• Client CRM — save, view, update clients\n"
        "• Outreach — cold emails, follow-ups, sequences\n"
        "• Sales strategy — pitches, objection handling\n"
        "• Meeting prep and follow-ups\n\n"
        "/clients — view all clients\n"
        "/clear — clear conversation\n\n"
        "What do you need?"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Elevate Barcelona Assistant\n\n"
        "Client CRM\n"
        "Add, view, update, delete clients. Track pipeline status.\n\n"
        "Outreach\n"
        "Cold emails, follow-up sequences, messaging strategies\n\n"
        "Sales\n"
        "Proposals, pitch structures, objection handling\n\n"
        "Meetings\n"
        "Prep, talking points, post-meeting follow-ups\n\n"
        "/clients — view all clients\n"
        "/clear — reset conversation\n"
        "/help — this menu"
    )


async def list_clients(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_clients()
    clients = data["clients"]
    if not clients:
        await update.message.reply_text("No clients saved yet. Tell me about a client to add them.")
        return
    lines = [f"Clients ({len(clients)} total)\n"]
    for c in clients:
        line = f"• {c['name']} — {c.get('status', 'no status')}"
        if c.get("contact"):
            line += f" | {c['contact']}"
        if c.get("next_action"):
            line += f"\n  Next: {c['next_action']}"
        lines.append(line)
    await update.message.reply_text("\n".join(lines))


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conversations[update.effective_user.id] = []
    await update.message.reply_text("Cleared.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_message = update.message.text

    if user_id not in conversations:
        conversations[user_id] = []

    conversations[user_id].append({"role": "user", "content": user_message})
    if len(conversations[user_id]) > 20:
        conversations[user_id] = conversations[user_id][-20:]

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    messages = list(conversations[user_id])
    final_reply = ""

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages
        )

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = handle_tool(block.name, block.input)
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

    conversations[user_id].append({"role": "assistant", "content": final_reply})

    if len(final_reply) > 4096:
        for i in range(0, len(final_reply), 4096):
            await update.message.reply_text(final_reply[i:i + 4096])
    else:
        await update.message.reply_text(final_reply)


def build_app(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("clients", list_clients))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app
