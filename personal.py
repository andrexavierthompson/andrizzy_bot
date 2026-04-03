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
PERSONAL_FILE = DATA_DIR / "personal.json"


def load_data() -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if not PERSONAL_FILE.exists():
        PERSONAL_FILE.write_text(json.dumps({
            "tasks": [],
            "expenses": [],
            "monthly_budget": None
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
    }
]


def handle_tool(name: str, inputs: dict) -> str:
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

DAILY PLANNING
- Help Andre plan his day or week
- Suggest priorities based on current tasks and deadlines
- Help with scheduling and time management

PERSONAL DECISIONS & LIFE
- Help think through personal decisions
- Give practical, honest advice
- Keep suggestions simple and actionable

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
        "/clear — reset conversation\n"
        "/help — this menu"
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
    app.add_handler(CommandHandler("tasks", show_tasks))
    app.add_handler(CommandHandler("budget", show_budget))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app
