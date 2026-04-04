import os
import io
import json
import logging
import tempfile
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
CLIENTS_FILE = DATA_DIR / "clients.json"
BOT_NAME = "elevate"
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
    },
    {
        "name": "generate_document",
        "description": "Generate a Word (.docx) or PDF document and send it to Andre. Use for proposals, client reports, outreach packs, meeting summaries, pitch documents.",
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
                "style": {"type": "string", "enum": ["plain", "polished"], "description": "plain = clean draft. polished = branded with styling, use for client-facing docs."},
                "filename_hint": {"type": "string", "description": "Short slug for the filename e.g. 'acme-proposal' or 'q2-report'"}
            },
            "required": ["doc_type", "title", "sections"]
        }
    },
    {
        "name": "generate_spreadsheet",
        "description": "Generate an Excel (.xlsx) spreadsheet and send it to Andre. Use for client pipeline trackers, outreach lists, quote tables, contact sheets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Spreadsheet title"},
                "sheet_name": {"type": "string", "description": "Name for the worksheet tab"},
                "headers": {"type": "array", "items": {"type": "string"}, "description": "Column headers"},
                "rows": {"type": "array", "items": {"type": "array"}, "description": "Data rows, each row is an array of values"},
                "style": {"type": "string", "enum": ["plain", "polished"], "description": "plain = simple. polished = formatted with header styling and alternating rows."},
                "filename_hint": {"type": "string", "description": "Short slug for the filename"}
            },
            "required": ["title", "headers", "rows"]
        }
    },
    {
        "name": "generate_presentation",
        "description": "Generate a PowerPoint (.pptx) presentation and send it to Andre. Use for pitch decks, client presentations, strategy decks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Presentation title"},
                "slides": {
                    "type": "array",
                    "description": "List of slides each with a title and bullets",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "bullets": {"type": "array", "items": {"type": "string"}}
                        }
                    }
                },
                "style": {"type": "string", "enum": ["plain", "polished"], "description": "plain = default layout. polished = dark branded theme."},
                "filename_hint": {"type": "string", "description": "Short slug for the filename"}
            },
            "required": ["title", "slides"]
        }
    }
]


def handle_tool(name: str, inputs: dict, pending_files: list = None) -> str:
    if pending_files is None:
        pending_files = []

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

    elif name == "generate_document":
        try:
            doc_type = inputs.get("doc_type", "docx")
            style = inputs.get("style", "plain")
            hint = inputs.get("filename_hint", "")
            if doc_type == "pdf":
                file_bytes, filename = file_generator.generate_pdf(
                    inputs["title"], inputs["sections"], style, "Elevate Barcelona", hint)
            else:
                file_bytes, filename = file_generator.generate_word(
                    inputs["title"], inputs["sections"], style, "Elevate Barcelona", hint)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{doc_type}")
            tmp.write(file_bytes)
            tmp.close()
            pending_files.append((tmp.name, filename, "elevate"))
            return f"Document ready: {filename}"
        except Exception as e:
            return f"Error generating document: {e}"

    elif name == "generate_spreadsheet":
        try:
            style = inputs.get("style", "plain")
            hint = inputs.get("filename_hint", "")
            sheet = inputs.get("sheet_name", "Sheet1")
            file_bytes, filename = file_generator.generate_excel(
                inputs["title"], inputs["headers"], inputs["rows"], sheet, style, "Elevate Barcelona", hint)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
            tmp.write(file_bytes)
            tmp.close()
            pending_files.append((tmp.name, filename, "elevate"))
            return f"Spreadsheet ready: {filename}"
        except Exception as e:
            return f"Error generating spreadsheet: {e}"

    elif name == "generate_presentation":
        try:
            style = inputs.get("style", "plain")
            hint = inputs.get("filename_hint", "")
            file_bytes, filename = file_generator.generate_pptx(
                inputs["title"], inputs["slides"], style, "Elevate Barcelona", hint)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pptx")
            tmp.write(file_bytes)
            tmp.close()
            pending_files.append((tmp.name, filename, "elevate"))
            return f"Presentation ready: {filename}"
        except Exception as e:
            return f"Error generating presentation: {e}"

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

FILE GENERATION
- You can generate Word documents (.docx), PDFs, Excel spreadsheets, and PowerPoint presentations
- Use generate_document for: proposals, client reports, outreach packs, pitch docs, meeting summaries
- Use generate_spreadsheet for: client pipeline trackers, outreach lists, quote tables, contact sheets
- Use generate_presentation for: pitch decks, strategy decks, client presentations
- For client-facing output always use style="polished"
- For internal drafts use style="plain"
- Always confirm the filename with the client name and date e.g. "acme-proposal" or "q2-report"
- If Andre uploads a requirements document, read it and generate the appropriate file

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
        "/learn [text] — teach me something to remember\n"
        "/knowledge — view everything I know\n"
        "/forget — clear all learned knowledge\n"
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


async def _run_claude(user_id: int, messages: list, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Core Claude loop — shared by handle_message and handle_document_upload."""
    pending_files = []
    final_reply = ""

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT + build_knowledge_prompt(),
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

    # Send any generated files
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
        await update.message.reply_text("Usage: /learn [something to remember]\nExample: /learn Our standard proposal fee is €2500")
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


def build_app(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("clients", list_clients))
    app.add_handler(CommandHandler("learn", learn))
    app.add_handler(CommandHandler("knowledge", show_knowledge))
    app.add_handler(CommandHandler("forget", forget))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document_upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app
