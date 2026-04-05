import os
import io
import json
import logging
import tempfile
import datetime
from datetime import date
from pathlib import Path
from anthropic import Anthropic
from telegram import Update, InputFile
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
import file_generator
import usage_tracker

logger = logging.getLogger(__name__)
client = Anthropic(api_key=os.environ["CLAUDE_API_KEY"])
conversations: dict = {}

DATA_DIR = Path(os.environ.get("DATA_PATH", "data"))
PERSONAL_FILE = DATA_DIR / "personal.json"
BOT_NAME = "personal"
KNOWLEDGE_FILE = DATA_DIR / f"{BOT_NAME}-knowledge.json"
PROJECTS_FILE = DATA_DIR / f"{BOT_NAME}-projects.json"
CONFIG_FILE = DATA_DIR / f"{BOT_NAME}-config.json"
BRIDGE_URL = os.environ.get("BRIDGE_URL", "")
BRIDGE_SECRET = os.environ.get("BRIDGE_SECRET", "changeme")


def save_chat_id(chat_id: int) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    CONFIG_FILE.write_text(json.dumps({"chat_id": chat_id}, indent=2))


def load_chat_id() -> int | None:
    if not CONFIG_FILE.exists():
        return None
    try:
        return json.loads(CONFIG_FILE.read_text()).get("chat_id")
    except Exception:
        return None


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


def load_projects() -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if not PROJECTS_FILE.exists():
        PROJECTS_FILE.write_text(json.dumps({"active": None, "projects": {}}, indent=2))
    return json.loads(PROJECTS_FILE.read_text())


def save_projects(data: dict) -> None:
    PROJECTS_FILE.write_text(json.dumps(data, indent=2))


def build_project_prompt() -> str:
    data = load_projects()
    name = data.get("active")
    if not name or name not in data["projects"]:
        return ""
    p = data["projects"][name]
    lines = [f"\n\nACTIVE PROJECT: {p['name']}"]
    if p.get("instructions"):
        lines.append(f"Instructions: {p['instructions']}")
    if p.get("entries"):
        lines.append("Project Knowledge:")
        for e in p["entries"]:
            lines.append(f"- {e['text']}")
    return "\n".join(lines)


def load_data() -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if not PERSONAL_FILE.exists():
        PERSONAL_FILE.write_text(json.dumps({
            "tasks": [],
            "expenses": [],
            "monthly_budget": None,
            "subscriptions": []
        }, indent=2))
    return json.loads(PERSONAL_FILE.read_text())


def save_data(data: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    PERSONAL_FILE.write_text(json.dumps(data, indent=2))


TOOLS = [
    {
        "name": "add_task",
        "description": "Add a new task or to-do item.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Description of the task"},
                "due": {"type": "string", "description": "Due date in YYYY-MM-DD format, if mentioned"},
                "priority": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Priority level"
                },
                "category": {"type": "string", "description": "Category e.g. personal, work, errands, health"}
            },
            "required": ["task"]
        }
    },
    {
        "name": "get_tasks",
        "description": "Get all current tasks. Use this to show the task list or check what's pending.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "complete_task",
        "description": "Mark a task as done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The task description or keyword to match"}
            },
            "required": ["task"]
        }
    },
    {
        "name": "delete_task",
        "description": "Delete a task entirely.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The task description or keyword to match"}
            },
            "required": ["task"]
        }
    },
    {
        "name": "add_expense",
        "description": "Log an expense for budget tracking.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "description": "Amount spent"},
                "currency": {"type": "string", "description": "Currency code e.g. EUR, GBP, USD"},
                "description": {"type": "string", "description": "What the expense was for"},
                "category": {"type": "string", "description": "Category e.g. food, transport, entertainment, subscriptions"}
            },
            "required": ["amount", "description"]
        }
    },
    {
        "name": "get_budget_summary",
        "description": "Get a summary of expenses and budget status.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "set_monthly_budget",
        "description": "Set the monthly budget amount.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "description": "Monthly budget amount"},
                "currency": {"type": "string", "description": "Currency code e.g. EUR, GBP, USD"}
            },
            "required": ["amount"]
        }
    },
    {
        "name": "add_subscription",
        "description": "Save a new recurring subscription like Spotify, Netflix, gym membership etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the subscription e.g. Spotify"},
                "amount": {"type": "number", "description": "Amount charged per cycle"},
                "currency": {"type": "string", "description": "Currency code e.g. EUR, GBP, USD"},
                "cycle": {
                    "type": "string",
                    "enum": ["weekly", "monthly", "yearly"],
                    "description": "How often it recurs"
                },
                "next_due": {"type": "string", "description": "Next due date in YYYY-MM-DD format"},
                "category": {"type": "string", "description": "Category e.g. entertainment, health, software, utilities"}
            },
            "required": ["name", "amount", "cycle"]
        }
    },
    {
        "name": "get_subscriptions",
        "description": "Get all saved recurring subscriptions.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "delete_subscription",
        "description": "Delete a recurring subscription by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the subscription to delete"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "generate_document",
        "description": "Generate a Word (.docx) or PDF document and send it to Andre. Use for weekly plans, goal tracking docs, decision frameworks, personal reviews.",
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_type": {"type": "string", "enum": ["docx", "pdf"], "description": "File format"},
                "title": {"type": "string", "description": "Document title"},
                "sections": {
                    "type": "array",
                    "description": "List of sections with heading and body text",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string"},
                            "body": {"type": "string"}
                        }
                    }
                },
                "style": {"type": "string", "enum": ["plain", "polished"], "description": "plain = simple. polished = formatted with styling."},
                "filename_hint": {"type": "string", "description": "Short slug for the filename e.g. 'weekly-plan' or 'april-goals'"}
            },
            "required": ["doc_type", "title", "sections"]
        }
    },
    {
        "name": "generate_spreadsheet",
        "description": "Generate an Excel (.xlsx) spreadsheet and send it to Andre. Use for budget exports, monthly expense summaries, subscription lists, task trackers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Spreadsheet title"},
                "sheet_name": {"type": "string", "description": "Name for the worksheet tab"},
                "headers": {"type": "array", "items": {"type": "string"}, "description": "Column headers"},
                "rows": {"type": "array", "items": {"type": "array"}, "description": "Data rows, each row is an array of values"},
                "style": {"type": "string", "enum": ["plain", "polished"], "description": "plain = simple. polished = formatted with header styling."},
                "filename_hint": {"type": "string", "description": "Short slug for the filename e.g. 'april-2026-budget'"}
            },
            "required": ["title", "headers", "rows"]
        }
    },
    {
        "name": "save_to_project",
        "description": "Save a note, fact, or summary into the active project's knowledge. Use this when you generate something worth remembering for this project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to save"}
            },
            "required": ["text"]
        }
    }
]


def handle_tool(name: str, inputs: dict, pending_files: list = None) -> str:
    if pending_files is None:
        pending_files = []
    data = load_data()

    if name == "add_task":
        inputs["done"] = False
        inputs["added"] = str(date.today())
        data["tasks"].append(inputs)
        save_data(data)
        return f"Task added: {inputs['task']}"

    elif name == "get_tasks":
        pending = [t for t in data["tasks"] if not t.get("done")]
        done = [t for t in data["tasks"] if t.get("done")]
        return json.dumps({"pending": pending, "done_count": len(done)})

    elif name == "complete_task":
        keyword = inputs["task"].lower()
        matched = False
        for t in data["tasks"]:
            if keyword in t["task"].lower() and not t.get("done"):
                t["done"] = True
                t["completed_on"] = str(date.today())
                matched = True
                break
        save_data(data)
        return f"Marked done: {inputs['task']}" if matched else "Task not found"

    elif name == "delete_task":
        keyword = inputs["task"].lower()
        original = len(data["tasks"])
        data["tasks"] = [t for t in data["tasks"] if keyword not in t["task"].lower()]
        save_data(data)
        removed = original - len(data["tasks"])
        return f"Deleted {removed} task(s)" if removed else "No matching tasks found"

    elif name == "add_expense":
        inputs["date"] = str(date.today())
        data["expenses"].append(inputs)
        save_data(data)
        currency = inputs.get("currency", "")
        return f"Logged: {inputs['description']} — {inputs['amount']} {currency}"

    elif name == "get_budget_summary":
        expenses = data["expenses"]
        budget = data.get("monthly_budget")
        total = sum(e["amount"] for e in expenses)
        by_category: dict = {}
        for e in expenses:
            cat = e.get("category", "other")
            by_category[cat] = by_category.get(cat, 0) + e["amount"]
        return json.dumps({
            "monthly_budget": budget,
            "total_spent": total,
            "remaining": (budget["amount"] - total) if budget else None,
            "by_category": by_category,
            "expense_count": len(expenses)
        })

    elif name == "set_monthly_budget":
        data["monthly_budget"] = inputs
        save_data(data)
        return f"Budget set: {inputs['amount']} {inputs.get('currency', '')}"

    elif name == "add_subscription":
        if "subscriptions" not in data:
            data["subscriptions"] = []
        existing = next((s for s in data["subscriptions"] if s["name"].lower() == inputs["name"].lower()), None)
        if existing:
            existing.update(inputs)
            save_data(data)
            return f"Updated subscription: {inputs['name']}"
        inputs["added"] = str(date.today())
        data["subscriptions"].append(inputs)
        save_data(data)
        return f"Saved subscription: {inputs['name']} — {inputs['amount']} {inputs.get('currency', '')} {inputs['cycle']}"

    elif name == "get_subscriptions":
        return json.dumps(data.get("subscriptions", []))

    elif name == "delete_subscription":
        keyword = inputs["name"].lower()
        original = len(data.get("subscriptions", []))
        data["subscriptions"] = [s for s in data.get("subscriptions", []) if keyword not in s["name"].lower()]
        save_data(data)
        removed = original - len(data["subscriptions"])
        return f"Deleted {removed} subscription(s)" if removed else "Subscription not found"

    elif name == "generate_document":
        try:
            doc_type = inputs.get("doc_type", "docx")
            style = inputs.get("style", "plain")
            hint = inputs.get("filename_hint", "")
            if doc_type == "pdf":
                file_bytes, filename = file_generator.generate_pdf(
                    inputs["title"], inputs["sections"], style, "Andre", hint)
            else:
                file_bytes, filename = file_generator.generate_word(
                    inputs["title"], inputs["sections"], style, "Andre", hint)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{doc_type}")
            tmp.write(file_bytes)
            tmp.close()
            pending_files.append((tmp.name, filename, "personal"))
            return f"Document ready: {filename}"
        except Exception as e:
            return f"Error generating document: {e}"

    elif name == "generate_spreadsheet":
        try:
            style = inputs.get("style", "plain")
            hint = inputs.get("filename_hint", "")
            sheet = inputs.get("sheet_name", "Sheet1")
            file_bytes, filename = file_generator.generate_excel(
                inputs["title"], inputs["headers"], inputs["rows"], sheet, style, "Andre", hint)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
            tmp.write(file_bytes)
            tmp.close()
            pending_files.append((tmp.name, filename, "personal"))
            return f"Spreadsheet ready: {filename}"
        except Exception as e:
            return f"Error generating spreadsheet: {e}"

    elif name == "save_to_project":
        proj_data = load_projects()
        active = proj_data.get("active")
        if not active or active not in proj_data["projects"]:
            return "No active project to save to."
        proj_data["projects"][active]["entries"].append({"text": inputs["text"], "added": str(date.today())})
        save_projects(proj_data)
        return f"Saved to project '{proj_data['projects'][active]['name']}'."

    return "Unknown tool"


SYSTEM_PROMPT = """You are Andre Thompson's personal life assistant. Your job is to help Andre stay organised, on top of his tasks, and in control of his finances.

TASKS & TO-DOS
- Add, view, complete, and delete tasks using your tools
- Help Andre prioritise what to work on
- When showing tasks, group by priority (high first) and show due dates if set
- Encourage completing overdue or high-priority tasks

BUDGET TRACKING
- Log expenses and track spending
- Help Andre set and stick to a monthly budget
- Show breakdowns by category when summarising
- Flag if spending is close to or over budget

SUBSCRIPTIONS
- Save, view, and delete recurring subscriptions using your tools
- When adding, confirm name, amount, currency, cycle, and next due date
- When showing budget summary, include total monthly subscription cost
- Flag any subscription with a next_due date within 3 days

DAILY PLANNING
- Help Andre plan his day or week
- Suggest priorities based on current tasks and deadlines
- Help with scheduling and time management

PERSONAL DECISIONS & LIFE
- Help think through personal decisions
- Give practical, honest advice
- Keep suggestions simple and actionable

FILE GENERATION
- You can generate Excel spreadsheets and Word documents
- Use generate_spreadsheet for: budget exports, monthly expense summaries, subscription tables, task trackers
- Use generate_document for: weekly plans, goal tracking docs, decision frameworks, personal reviews
- Default to style="plain" for all personal files unless asked for polished
- Filename should be descriptive e.g. "april-2026-budget" or "weekly-plan"
- If Andre uploads a document, read it and generate the appropriate file

RULES:
- Be warm, friendly, and direct
- Bullet points for lists, short sentences for advice
- Ask for missing details before logging (e.g. amount, currency, due date)
- Never judge spending or decisions — just help organise and plan"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Personal assistant here.\n\n"
        "I can help with:\n"
        "• Tasks and to-dos\n"
        "• Budget tracking and expenses\n"
        "• Daily planning and scheduling\n"
        "• Personal decisions and life stuff\n\n"
        "/tasks — view current tasks\n"
        "/budget — view budget summary\n"
        "/clear — reset conversation\n\n"
        "What do you need?"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Personal Assistant\n\n"
        "Tasks\n"
        "Add, complete, and manage your to-do list\n\n"
        "Budget\n"
        "Log expenses, set a monthly budget, view breakdowns\n\n"
        "Planning\n"
        "Daily and weekly planning, prioritisation\n\n"
        "Personal\n"
        "Decisions, advice, life organisation\n\n"
        "/tasks — view all tasks\n"
        "/budget — view budget summary\n"
        "/subscriptions — view recurring subscriptions\n"
        "/learn [text] — teach me something to remember\n"
        "/knowledge — view everything I know\n"
        "/forget — clear all learned knowledge\n"
        "/clear — reset conversation\n"
        "/help — this menu\n\n"
        "Projects\n"
        "/project create <name> — create a new project\n"
        "/project <name> — switch active project\n"
        "/project list — list all projects\n"
        "/project info — show active project\n"
        "/project delete <name> — delete a project\n"
        "/plearn [text] — add to active project knowledge\n"
        "/pinstruct [text] — set project instructions\n"
        "/pknowledge — view project knowledge\n"
        "/pforget — clear project knowledge"
    )


async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_data()
    pending = [t for t in data["tasks"] if not t.get("done")]
    if not pending:
        await update.message.reply_text("No tasks. You're clear!")
        return
    priority_order = {"high": 0, "medium": 1, "low": 2}
    pending.sort(key=lambda t: priority_order.get(t.get("priority", "medium"), 1))
    lines = [f"Tasks ({len(pending)} pending)\n"]
    for t in pending:
        p = t.get("priority", "")
        due = t.get("due", "")
        line = f"• {t['task']}"
        if p:
            line += f" [{p}]"
        if due:
            line += f" — due {due}"
        lines.append(line)
    await update.message.reply_text("\n".join(lines))


async def show_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_data()
    expenses = data["expenses"]
    budget = data.get("monthly_budget")
    if not expenses and not budget:
        await update.message.reply_text("No budget or expenses logged yet. Tell me your monthly budget to get started.")
        return
    total = sum(e["amount"] for e in expenses)
    lines = ["Budget Summary\n"]
    if budget:
        remaining = budget["amount"] - total
        lines.append(f"Monthly budget: {budget['amount']} {budget.get('currency', '')}")
        lines.append(f"Spent so far: {total:.2f}")
        lines.append(f"Remaining: {remaining:.2f}")
    else:
        lines.append(f"Total spent: {total:.2f} (no budget set)")
    by_category: dict = {}
    for e in expenses:
        cat = e.get("category", "other")
        by_category[cat] = by_category.get(cat, 0) + e["amount"]
    if by_category:
        lines.append("\nBy category:")
        for cat, amount in sorted(by_category.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {cat}: {amount:.2f}")
    await update.message.reply_text("\n".join(lines))


async def show_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from datetime import datetime, timedelta
    data = load_data()
    subs = data.get("subscriptions", [])
    if not subs:
        await update.message.reply_text("No subscriptions saved yet. Tell me about one to add it.")
        return
    today = date.today()
    lines = [f"Subscriptions ({len(subs)} total)\n"]
    for s in sorted(subs, key=lambda x: x.get("next_due", "9999-12-31")):
        line = f"• {s['name']} — {s['amount']} {s.get('currency', '')} / {s.get('cycle', '')}"
        if s.get("next_due"):
            line += f" | next due {s['next_due']}"
            try:
                due = date.fromisoformat(s["next_due"])
                days_left = (due - today).days
                if days_left <= 3:
                    line += f" ⚠️ due in {days_left} day(s)"
            except ValueError:
                pass
        lines.append(line)
    total_monthly = sum(
        s["amount"] for s in subs if s.get("cycle") == "monthly"
    )
    if total_monthly:
        lines.append(f"\nTotal monthly: {total_monthly:.2f}")
    await update.message.reply_text("\n".join(lines))


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conversations[update.effective_user.id] = []
    await update.message.reply_text("Cleared.")


async def _run_claude(user_id: int, messages: list, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending_files = []
    final_reply = ""

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT + build_knowledge_prompt() + build_project_prompt(),
            tools=TOOLS,
            messages=messages
        )
        usage_tracker.track_usage(response.usage.input_tokens, response.usage.output_tokens)

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = handle_tool(block.name, block.input, pending_files)
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

    for tmp_path, display_name, subfolder in pending_files:
        try:
            with open(tmp_path, "rb") as f:
                file_bytes = f.read()
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=InputFile(io.BytesIO(file_bytes), filename=display_name),
                caption=display_name
            )
            await file_generator.save_to_local(display_name, file_bytes, subfolder, BRIDGE_URL, BRIDGE_SECRET)
        except Exception as e:
            logger.error(f"Error sending file {display_name}: {e}")
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    conversations[user_id].append({"role": "assistant", "content": final_reply})

    if final_reply:
        if len(final_reply) > 4096:
            for i in range(0, len(final_reply), 4096):
                await update.message.reply_text(final_reply[i:i + 4096])
        else:
            await update.message.reply_text(final_reply)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_message = update.message.text
    save_chat_id(update.effective_chat.id)

    if user_id not in conversations:
        conversations[user_id] = []

    conversations[user_id].append({"role": "user", "content": user_message})
    if len(conversations[user_id]) > 20:
        conversations[user_id] = conversations[user_id][-20:]

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        await _run_claude(user_id, list(conversations[user_id]), update, context)
    except Exception as e:
        logger.error(f"Error in handle_message: {e}")
        await update.message.reply_text("Something went wrong. Please try again.")


async def handle_document_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    doc = update.message.document

    supported = {
        "text/plain", "text/markdown",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/pdf"
    }
    if doc.mime_type not in supported and not doc.file_name.endswith((".txt", ".md", ".docx", ".pdf")):
        await update.message.reply_text(
            "I can read .txt, .md, .docx, and .pdf files. Please upload one of those."
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        tg_file = await doc.get_file()
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        buf.seek(0)
        raw = buf.read()

        extracted = ""
        fname = doc.file_name.lower()

        if fname.endswith(".docx"):
            from docx import Document as DocxDocument
            d = DocxDocument(io.BytesIO(raw))
            extracted = "\n".join(p.text for p in d.paragraphs if p.text.strip())
        elif fname.endswith(".pdf"):
            try:
                import pypdf
                reader = pypdf.PdfReader(io.BytesIO(raw))
                extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
            except ImportError:
                extracted = raw.decode("utf-8", errors="ignore")
        else:
            extracted = raw.decode("utf-8", errors="ignore")

        extracted = extracted[:4000]

        if user_id not in conversations:
            conversations[user_id] = []

        injected = (
            f'[Andre uploaded a document: "{doc.file_name}"]\n\n'
            f'Content:\n---\n{extracted}\n---\n\n'
            f'Read this document and generate the appropriate deliverable based on its content. '
            f'Ask if you need any clarification on format or style.'
        )
        conversations[user_id].append({"role": "user", "content": injected})
        if len(conversations[user_id]) > 20:
            conversations[user_id] = conversations[user_id][-20:]

        await _run_claude(user_id, list(conversations[user_id]), update, context)

    except Exception as e:
        logger.error(f"Error in handle_document_upload: {e}")
        await update.message.reply_text("Couldn't read that file. Please try again.")


async def learn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Usage: /learn [something to remember]\nExample: /learn My monthly budget is €1200")
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


async def project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage:\n"
            "/project create <name> — create a new project\n"
            "/project <name> — switch to a project\n"
            "/project list — list all projects\n"
            "/project info — show active project\n"
            "/project delete <name> — delete a project"
        )
        return
    sub = args[0].lower()
    data = load_projects()

    if sub == "create":
        raw = " ".join(args[1:]).strip()
        if not raw:
            await update.message.reply_text("Usage: /project create <name>")
            return
        key = raw.lower().replace(" ", "_")
        if key in data["projects"]:
            await update.message.reply_text(f"Project '{raw}' already exists. Use /project {raw} to switch to it.")
            return
        data["projects"][key] = {"name": raw, "instructions": "", "entries": [], "created": str(date.today())}
        data["active"] = key
        save_projects(data)
        await update.message.reply_text(f"Project '{raw}' created and set as active.")

    elif sub == "list":
        if not data["projects"]:
            await update.message.reply_text("No projects yet. Use /project create <name> to start one.")
            return
        active = data.get("active")
        lines = ["Projects:\n"]
        for k, p in data["projects"].items():
            marker = " [ACTIVE]" if k == active else ""
            lines.append(f"• {p['name']}{marker} — {len(p['entries'])} entries")
        await update.message.reply_text("\n".join(lines))

    elif sub == "info":
        active = data.get("active")
        if not active or active not in data["projects"]:
            await update.message.reply_text("No active project. Use /project create <name> or /project <name> to set one.")
            return
        p = data["projects"][active]
        lines = [f"Active project: {p['name']}", f"Created: {p['created']}", f"Instructions: {p['instructions'] or '(none)'}"]
        if p["entries"]:
            lines.append(f"\nKnowledge ({len(p['entries'])} entries):")
            for i, e in enumerate(p["entries"], 1):
                lines.append(f"{i}. {e['text']}  ({e['added']})")
        else:
            lines.append("Knowledge: (none)")
        await update.message.reply_text("\n".join(lines))

    elif sub == "delete":
        raw = " ".join(args[1:]).strip()
        key = raw.lower().replace(" ", "_")
        if key not in data["projects"]:
            await update.message.reply_text(f"No project named '{raw}'. Use /project list to see all.")
            return
        del data["projects"][key]
        if data.get("active") == key:
            data["active"] = None
        save_projects(data)
        await update.message.reply_text(f"Project '{raw}' deleted.")

    else:
        raw = " ".join(args).strip()
        key = raw.lower().replace(" ", "_")
        if key not in data["projects"]:
            await update.message.reply_text(f"No project named '{raw}'. Use /project list to see all.")
            return
        data["active"] = key
        save_projects(data)
        await update.message.reply_text(f"Switched to project '{data['projects'][key]['name']}'.")


async def plearn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Usage: /plearn [something to remember for this project]")
        return
    data = load_projects()
    active = data.get("active")
    if not active or active not in data["projects"]:
        await update.message.reply_text("No active project. Use /project create <name> to start one.")
        return
    data["projects"][active]["entries"].append({"text": text, "added": str(date.today())})
    save_projects(data)
    await update.message.reply_text(f"Saved to project '{data['projects'][active]['name']}': {text}")


async def pinstruct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Usage: /pinstruct [instructions for this project]")
        return
    data = load_projects()
    active = data.get("active")
    if not active or active not in data["projects"]:
        await update.message.reply_text("No active project. Use /project create <name> to start one.")
        return
    data["projects"][active]["instructions"] = text
    save_projects(data)
    await update.message.reply_text(f"Instructions set for '{data['projects'][active]['name']}'.")


async def pknowledge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_projects()
    active = data.get("active")
    if not active or active not in data["projects"]:
        await update.message.reply_text("No active project. Use /project create <name> to start one.")
        return
    p = data["projects"][active]
    entries = p.get("entries", [])
    if not entries:
        await update.message.reply_text(f"No entries in project '{p['name']}'. Use /plearn [text] to add some.")
        return
    lines = [f"Project '{p['name']}' knowledge ({len(entries)} entries)\n"]
    for i, e in enumerate(entries, 1):
        lines.append(f"{i}. {e['text']}  ({e['added']})")
    await update.message.reply_text("\n".join(lines))


async def pforget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_projects()
    active = data.get("active")
    if not active or active not in data["projects"]:
        await update.message.reply_text("No active project.")
        return
    name = data["projects"][active]["name"]
    data["projects"][active]["entries"] = []
    save_projects(data)
    await update.message.reply_text(f"Cleared all knowledge entries for project '{name}'.")


async def send_morning_briefing(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = load_chat_id()
    if not chat_id:
        return
    try:
        data = load_data()
        today = date.today()
        lines = [f"Good morning! Personal briefing for {today.strftime('%A %d %B')}.\n"]

        pending = [t for t in data["tasks"] if not t.get("done")]
        if pending:
            priority_order = {"high": 0, "medium": 1, "low": 2}
            pending.sort(key=lambda t: priority_order.get(t.get("priority", "medium"), 1))
            lines.append(f"TASKS ({len(pending)} pending)")
            for t in pending[:8]:
                line = f"• {t['task']}"
                if t.get("priority"):
                    line += f" [{t['priority'].upper()}]"
                if t.get("due"):
                    line += f" — due {t['due']}"
                lines.append(line)
        else:
            lines.append("TASKS\nNo pending tasks.")

        subs = data.get("subscriptions", [])
        upcoming_subs = []
        for s in subs:
            if s.get("next_due"):
                try:
                    due = date.fromisoformat(s["next_due"])
                    days_left = (due - today).days
                    if 0 <= days_left <= 5:
                        upcoming_subs.append((days_left, s))
                except ValueError:
                    pass
        if upcoming_subs:
            upcoming_subs.sort(key=lambda x: x[0])
            lines.append("\nSUBSCRIPTIONS DUE SOON")
            for days, s in upcoming_subs:
                due_str = "today" if days == 0 else f"in {days} day(s)"
                lines.append(f"• {s['name']} — {s['amount']} {s.get('currency', '')} ({due_str})")

        await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))
    except Exception as e:
        logger.error(f"Error sending personal morning briefing: {e}")


def build_app(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("tasks", show_tasks))
    app.add_handler(CommandHandler("budget", show_budget))
    app.add_handler(CommandHandler("subscriptions", show_subscriptions))
    app.add_handler(CommandHandler("learn", learn))
    app.add_handler(CommandHandler("knowledge", show_knowledge))
    app.add_handler(CommandHandler("forget", forget))
    app.add_handler(CommandHandler("project", project))
    app.add_handler(CommandHandler("plearn", plearn))
    app.add_handler(CommandHandler("pinstruct", pinstruct))
    app.add_handler(CommandHandler("pknowledge", pknowledge))
    app.add_handler(CommandHandler("pforget", pforget))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document_upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.job_queue.run_daily(send_morning_briefing, datetime.time(6, 0, 0))
    return app
