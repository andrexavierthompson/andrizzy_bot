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

logger = logging.getLogger(__name__)
client = Anthropic(api_key=os.environ["CLAUDE_API_KEY"])
conversations: dict = {}

DATA_DIR = Path(os.environ.get("DATA_PATH", "data"))
UNI_FILE = DATA_DIR / "university.json"
BOT_NAME = "university"
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
    },
    {
        "name": "generate_document",
        "description": "Generate a Word (.docx) or PDF document and send it to Andre. Use for essay drafts, assignment reports, coursework submissions, study notes.",
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
                "style": {"type": "string", "enum": ["plain", "polished"], "description": "plain = clean academic format. polished = formatted with styling."},
                "filename_hint": {"type": "string", "description": "Short slug for the filename e.g. 'marketing-essay-draft'"}
            },
            "required": ["doc_type", "title", "sections"]
        }
    },
    {
        "name": "generate_spreadsheet",
        "description": "Generate an Excel (.xlsx) spreadsheet and send it to Andre. Use for assignment trackers, revision schedules, reference lists, study planners.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Spreadsheet title"},
                "sheet_name": {"type": "string", "description": "Name for the worksheet tab"},
                "headers": {"type": "array", "items": {"type": "string"}, "description": "Column headers"},
                "rows": {"type": "array", "items": {"type": "array"}, "description": "Data rows, each row is an array of values"},
                "style": {"type": "string", "enum": ["plain", "polished"], "description": "plain = simple. polished = formatted with header styling."},
                "filename_hint": {"type": "string", "description": "Short slug for the filename"}
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

    elif name == "generate_document":
        try:
            doc_type = inputs.get("doc_type", "docx")
            style = inputs.get("style", "plain")
            hint = inputs.get("filename_hint", "")
            if doc_type == "pdf":
                file_bytes, filename = file_generator.generate_pdf(
                    inputs["title"], inputs["sections"], style, "EU Business School Barcelona", hint)
            else:
                file_bytes, filename = file_generator.generate_word(
                    inputs["title"], inputs["sections"], style, "EU Business School Barcelona", hint)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{doc_type}")
            tmp.write(file_bytes)
            tmp.close()
            pending_files.append((tmp.name, filename, "university"))
            return f"Document ready: {filename}"
        except Exception as e:
            return f"Error generating document: {e}"

    elif name == "generate_spreadsheet":
        try:
            style = inputs.get("style", "plain")
            hint = inputs.get("filename_hint", "")
            sheet = inputs.get("sheet_name", "Sheet1")
            file_bytes, filename = file_generator.generate_excel(
                inputs["title"], inputs["headers"], inputs["rows"], sheet, style, "EU Business School Barcelona", hint)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
            tmp.write(file_bytes)
            tmp.close()
            pending_files.append((tmp.name, filename, "university"))
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

FILE GENERATION
- You can generate Word documents (.docx), PDFs, and Excel spreadsheets
- Use generate_document for: essay drafts, assignment reports, coursework submissions, study notes
- Use generate_spreadsheet for: assignment trackers, revision schedules, reference lists, study planners
- Default to style="plain" for all academic work unless Andre asks for polished format
- Filename should reflect the assignment e.g. "marketing-essay-draft" or "assignment-tracker"
- If Andre uploads a requirements or brief document, read it and generate the appropriate file

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
        await update.message.reply_text("Usage: /learn [something to remember]\nExample: /learn Harvard referencing is required for all submissions")
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
        assignments = data.get("assignments", [])
        pending = [a for a in assignments if a.get("status") != "done"]

        lines = [f"Good morning! University briefing for {today.strftime('%A %d %B')}.\n"]

        if not pending:
            lines.append("No pending assignments. You're all caught up.")
        else:
            pending.sort(key=lambda a: a.get("deadline", "9999-12-31"))
            urgent = []
            upcoming = []
            later = []
            for a in pending:
                if a.get("deadline"):
                    try:
                        due = date.fromisoformat(a["deadline"])
                        days_left = (due - today).days
                        if days_left <= 3:
                            urgent.append((days_left, a))
                        elif days_left <= 14:
                            upcoming.append((days_left, a))
                        else:
                            later.append((days_left, a))
                    except ValueError:
                        upcoming.append((999, a))
                else:
                    upcoming.append((999, a))

            if urgent:
                lines.append("URGENT (due in 3 days or less)")
                for days, a in urgent:
                    due_str = "today" if days == 0 else f"in {days} day(s)"
                    line = f"• {a['title']}"
                    if a.get("course"):
                        line += f" — {a['course']}"
                    line += f" | due {due_str} [{a.get('status', 'not started')}]"
                    lines.append(line)

            if upcoming:
                lines.append("\nUPCOMING (next 14 days)")
                for days, a in upcoming:
                    line = f"• {a['title']}"
                    if a.get("course"):
                        line += f" — {a['course']}"
                    if a.get("deadline"):
                        line += f" | due {a['deadline']}"
                    line += f" [{a.get('status', 'not started')}]"
                    lines.append(line)

            if later:
                lines.append(f"\n{len(later)} more assignment(s) due later.")

        await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))
    except Exception as e:
        logger.error(f"Error sending university morning briefing: {e}")


def build_app(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("assignments", show_assignments))
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
