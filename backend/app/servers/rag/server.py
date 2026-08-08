import json
from pathlib import Path

import anyio
from mcp.server.fastmcp import FastMCP

from . import insights, store

mcp = FastMCP("rag", streamable_http_path="/", instructions=(
    "The user can upload documents — text, Markdown, or PDF — and call recordings, "
    "which are transcribed automatically. Whenever a question might touch their "
    "files, notes, documents, or something said on a recorded call, call the ask "
    "tool and base your answer only on what it returns; never guess at what a "
    "document says. Use summarize for an overview of a document, recording_insights "
    "for a call recording — it writes up the decisions, action items, and follow-ups "
    "— and list_documents to tell them what they've uploaded.\n"
    "When something is just uploaded, don't dive in. Ask, in one short sentence, "
    "whether they want you to go through it or research what it's about, and wait "
    "for their answer. If they want it gone through, call recording_insights for a "
    "recording or summarize for a document. If they want research, research the "
    "subject — if you don't know it yet, call summarize first and research what it "
    "turns out to be about. Do one or the other, never both, and never before they "
    "have chosen."
))

@mcp.tool(description=(
    "Answer a question using the user's uploaded documents and call recordings.\n\n"
    "question: what the user wants to know. Call this whenever the user asks about "
    "their files, notes, documents, or something that was said on a recorded call."
))
async def ask(question: str, session_id: str = "") -> str:
    if not session_id:
        return ""
    passages = await anyio.to_thread.run_sync(store.search, session_id, question)
    if not passages:
        return "I couldn't find anything about that in your documents."
    return (
        "Answer the user out loud in one or two natural sentences, using only the "
        "passages below. Speak in your own words — never read them back or copy "
        "their wording, layout, or formatting. No markdown, no asterisks, no "
        "bullet points, no square brackets.\n\nPassages:\n" + " … ".join(passages)
    )

@mcp.tool(description="Summarize an uploaded document.\n\nfilename: which document to summarize. Leave blank for the most recent upload.")
async def summarize(filename: str = "", session_id: str = "") -> str:
    if not session_id:
        return ""
    text = await anyio.to_thread.run_sync(store.get_document_text, session_id, filename)
    if not text:
        return "I don't have that document to summarize."
    return (
        "Summarize this out loud in two or three natural spoken sentences: what it "
        "is, and the few things that actually matter in it. Speak in your own words "
        "— never copy its wording, layout, headings, or formatting, and never read "
        "it back section by section. No markdown, no asterisks, no bullet points, no "
        "square brackets, no headings. Don't recite phone numbers, email addresses, "
        "or links unless the user asks for them. Finish by inviting a question."
        "\n\nDocument:\n" + text
    )

@mcp.tool(description=(
    "Debrief an uploaded call recording: what happened, what was decided, what "
    "everyone committed to, and what's still open. Writes the debrief onto the "
    "user's screen and hands back a short spoken version.\n\n"
    "Use this for any recorded call, meeting, or interview the user has uploaded — "
    "prefer it over summarize for those.\n\n"
    "filename: which recording. Leave blank for the most recent one."
))
async def recording_insights(filename: str = "", session_id: str = "") -> str:
    if not session_id:
        return ""
    if not filename:
        uploads = await anyio.to_thread.run_sync(store.list_documents, session_id)
        filename = next((u["filename"] for u in uploads if u["kind"] == "recording"), "")
        if not filename:
            return "You haven't uploaded a recording yet. Add one and I'll go through it."
    transcript = await anyio.to_thread.run_sync(
        store.get_document_text, session_id, filename, insights.TRANSCRIPT_BUDGET
    )
    if not transcript:
        return "I don't have that recording. Upload it and I'll go through it."
    try:
        result = await insights.debrief(session_id, filename, transcript)
    except insights.InsightsFailed as exc:
        return str(exc)
    return json.dumps({
        **result,
        "note": "Say the spoken line in your own words and stop — the full write-up "
                "is already on the user's screen. Read the decisions, action items, "
                "or follow-ups aloud only if they ask for them.",
    })

@mcp.tool(description="Host-only: what this skill already knows about the user.")
async def briefing(session_id: str = "") -> str:
    if not session_id:
        return ""
    uploads = await anyio.to_thread.run_sync(store.list_documents, session_id)
    if not uploads:
        return ""
    docs = [Path(u["filename"]).stem for u in uploads if u["kind"] != "recording"][:6]
    recordings = [Path(u["filename"]).stem for u in uploads if u["kind"] == "recording"][:6]
    lines = []
    if docs:
        lines.append("Documents they've uploaded: " + ", ".join(docs) + ".")
    if recordings:
        lines.append("Call recordings they've uploaded: " + ", ".join(recordings) + ".")
    lines.append(
        "You already know these exist — don't ask what they've uploaded, and call ask "
        "or summarize when a question touches one."
    )
    return " ".join(lines)

@mcp.tool(description="List the documents and call recordings the user has uploaded.")
async def list_documents(session_id: str = "") -> str:
    if not session_id:
        return ""
    uploads = await anyio.to_thread.run_sync(store.list_documents, session_id)
    if not uploads:
        return "You haven't uploaded any documents yet."
    docs = [Path(u["filename"]).stem for u in uploads if u["kind"] != "recording"]
    recordings = [Path(u["filename"]).stem for u in uploads if u["kind"] == "recording"]
    said = []
    if docs:
        said.append("documents: " + ", ".join(docs))
    if recordings:
        said.append("recordings: " + ", ".join(recordings))
    return "You've uploaded " + "; and ".join(said) + "."
