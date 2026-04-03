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
UNI_FILE = DATA_DIR / "university.json"


def load_data() -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if not UNI_FILE.exists():
        UNI_FILE.write_text(json.dumps({"assignments": []}, indent=2))
    return json.loads(UNI_FILE.read_text())


def save_data(data: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    UNI_FILE.write_text(json.dumps(data, indent=2))


TOOLS = [
    {
        "name": "save_assignment",
        "description": "Save a new assignment or update an existing one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Assignment or project title"},
                "course": {"type": "string", "description": "Course or module name"},
                "deadline": {"type": "string", "description": "Deadline in YYYY-MM-DD format"},
                "type": {
                    "type": "string",
                    "description": "Type of work e.g. essay, presentation, group project, exam, report"
                },
                "word_count": {"type": "string", "description": "Word count or length requirement if known"},
                "status": {
                    "type": "string",
                    "enum": ["not started", "in progress", "done"],
                    "description": "Current status"
                },
                "notes": {"type": "string", "description": "Any additional notes, brief, or requirements"}
            },
            "required": ["title"]
        }
    },
    {
        "name": "get_assignments",
        "description": "Get all assignments. Use this to show deadlines, check status, or find a specific assignment.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "update_assignment_status",
        "description": "Update the status of an assignment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Assignment title or keyword"},
                "status": {
                    "type": "string",
                    "enum": ["not started", "in progress", "done"],
                    "description": "New status"
                }
            },
            "required": ["title", "status"]
        }
    },
    {
        "name": "delete_assignment",
        "description": "Delete an assignment that is no longer relevant.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Assignment title or keyword to match"}
            },
            "required": ["title"]
        }
    }
]


def handle_tool(name: str, inputs: dict) -> str:
    data = load_data()

    if name == "save_assignment":
        assignments = data["assignments"]
        existing = next((a for a in assignments if a["title"].lower() == inputs["title"].lower()), None)
        if existing:
            existing.update(inputs)
            save_data(data)
            return f"Updated: {inputs['title']}"
        else:
            inputs.setdefault("status", "not started")
            inputs["added"] = str(date.today())
            assignments.append(inputs)
            save_data(data)
            return f"Saved assignment: {inputs['title']}"

    elif name == "get_assignments":
        return json.dumps(data)

    elif name == "update_assignment_status":
        keyword = inputs["title"].lower()
        for a in data["assignments"]:
            if keyword in a["title"].lower():
                a["status"] = inputs["status"]
                save_data(data)
                return f"Updated '{a['title']}' to: {inputs['status']}"
        return "Assignment not found"

    elif name == "delete_assignment":
        keyword = inputs["title"].lower()
        original = len(data["assignments"])
        data["assignments"] = [a for a in data["assignments"] if keyword not in a["title"].lower()]
        save_data(data)
        removed = original - len(data["assignments"])
        return f"Deleted {removed} assignment(s)" if removed else "No match found"

    return "Unknown tool"


SYSTEM_PROMPT = """You are the university assistant for Andre Thompson, a final-year student at EU Business School Barcelona.

Your job covers everything academic:

ASSIGNMENT MANAGEMENT
- Save, track, and update assignments using your tools
- Always check existing assignments before adding new ones
- When saving, confirm with a short summary including title, course, and deadline
- Flag assignments that are upcoming or overdue

ACADEMIC WRITING & COURSEWORK HELP
- Help plan, structure, and draft essays, reports, and presentations
- Suggest outlines and section breakdowns before writing
- Explain academic concepts clearly and simply
- Help with Harvard referencing and citations
- Review and improve drafts when asked

EXAM & STUDY HELP
- Help create study plans and revision schedules
- Explain topics and summarise key concepts
- Create practice questions or revision notes

RULES:
- Always use Harvard citation format when referencing: (Author, Year) inline, full list at end
- Ask for the assignment brief or requirements before helping with coursework
- Support understanding — help Andre learn, don't just produce answers to copy
- Be encouraging but honest about workload and deadlines
- Keep advice practical and actionable
- Bullet points for plans and lists; short paragraphs for explanations"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "University assistant here — EU Business School Barcelona.\n\n"
        "I can help with:\n"
        "• Assignment tracking — deadlines, status, briefs\n"
        "• Essay and report writing\n"
        "• Harvard referencing\n"
        "• Exam prep and study planning\n"
        "• Concept explanations\n\n"
        "/assignments — view all assignments\n"
        "/clear — reset conversation\n\n"
        "What are you working on?"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "University Assistant — EU Business School Barcelona\n\n"
        "Assignments\n"
        "Track deadlines, status, and briefs for all your work\n\n"
        "Writing\n"
        "Essays, reports, presentations — planning and drafting\n\n"
        "Referencing\n"
        "Harvard citations and reference lists\n\n"
        "Studying\n"
        "Revision plans, concept explanations, practice questions\n\n"
        "/assignments — view all assignments\n"
        "/clear — reset conversation\n"
        "/help — this menu"
    )


async def show_assignments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_data()
    assignments = data["assignments"]
    if not assignments:
        await update.message.reply_text("No assignments saved yet. Tell me about one to add it.")
        return
    pending = [a for a in assignments if a.get("status") != "done"]
    done = [a for a in assignments if a.get("status") == "done"]
    lines = [f"Assignments ({len(pending)} pending, {len(done)} done)\n"]
    if pending:
        pending.sort(key=lambda a: a.get("deadline", "9999-12-31"))
        for a in pending:
            line = f"• {a['title']}"
            if a.get("course"):
                line += f" — {a['course']}"
            if a.get("deadline"):
                line += f" | due {a['deadline']}"
            line += f" [{a.get('status', 'not started')}]"
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

    try:
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

    except Exception as e:
        logger.error(f"Error in handle_message: {e}")
        await update.message.reply_text("Something went wrong. Please try again.")


def build_app(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("assignments", show_assignments))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app
