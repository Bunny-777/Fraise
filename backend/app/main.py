import asyncio
import contextlib
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import anyio
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.servers.calendar_auth import router as calendar_auth_router
from app.host import bus
from app.host.mcp_manager import manager
from app.servers.calculator import mcp
from app.servers import daily
from app.servers.memory import store as memory_store
from app.servers.rag import embeddings, extract, reranker, store as rag_store
from app.storage import db
from app.host.voice_agent import bridge
from app.servers import mailer
from app.stt import engine as stt_engine, format as stt_format, punctuation
from app.stt.segmenter import Segmenter

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

logger = logging.getLogger(__name__)

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
_DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "https://fraise.vercel.app")
_configured_cors_origins = os.getenv("CORS_ORIGINS", "").split(",")
CORS_ORIGINS = list(dict.fromkeys([
    *_DEFAULT_CORS_ORIGINS,
    *(o.strip() for o in _configured_cors_origins if o.strip()),
]))

@asynccontextmanager
async def lifespan(_: FastAPI):
    async with mcp.session_manager.run():
        await manager.connect_all()
        warm_task = asyncio.create_task(anyio.to_thread.run_sync(_warm_rag))
        yield
        warm_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await warm_task
    await manager.aclose()

def _warm_rag() -> None:
    try:
        embeddings.warm()
        reranker.warm()
    except Exception:
        logger.exception("RAG warm-up failed — first query will be slow")

app = FastAPI(title="fraise", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health() -> dict:
    return {"ok": True}

@app.get("/agents/stream")
async def agents_stream(sid: str = Query(...)) -> StreamingResponse:
    queue = bus.subscribe(sid)

    async def events():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            bus.unsubscribe(sid, queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )

@app.get("/history")
async def history(sid: str = Query(...), limit: int = Query(40)) -> dict:
    turns = await anyio.to_thread.run_sync(memory_store.recent_turns, sid, limit)
    facts = await anyio.to_thread.run_sync(memory_store.recall, sid, "", 20)
    return {
        "turns": [{"role": r, "text": c} for r, c in turns],
        "memories": facts,
    }

@app.get("/artifacts")
async def artifacts(sid: str = Query(...)) -> list[dict]:
    return await anyio.to_thread.run_sync(db.list_artifacts, sid)

@app.get("/artifacts/{artifact_id}")
async def artifact(artifact_id: str, sid: str = Query(...)) -> dict:
    found = await anyio.to_thread.run_sync(db.get_artifact, artifact_id, sid)
    if not found:
        raise HTTPException(status_code=404, detail="no such artifact")
    return found

@app.get("/days")
async def days(sid: str = Query(...), limit: int = Query(20)) -> list[dict]:
    return await anyio.to_thread.run_sync(db.list_days, sid, limit)


@app.get("/days/{day_id}")
async def day(day_id: str, sid: str = Query(...)) -> dict:
    found = await anyio.to_thread.run_sync(db.get_day, day_id, sid)
    if not found:
        raise HTTPException(status_code=404, detail="no such day")
    return found


@app.get("/dictations")
async def dictations(sid: str = Query(...), limit: int = Query(20)) -> list[dict]:
    return await anyio.to_thread.run_sync(db.list_dictations, sid, limit)


@app.get("/dictations/{dictation_id}")
async def dictation(dictation_id: str, sid: str = Query(...)) -> dict:
    found = await anyio.to_thread.run_sync(db.get_dictation, dictation_id, sid)
    if not found:
        raise HTTPException(status_code=404, detail="no such dictation")
    return found


@app.post("/dictations/{dictation_id}/polish")
async def repolish_dictation(dictation_id: str, sid: str = Query(...)) -> dict:
    found = await anyio.to_thread.run_sync(db.get_dictation, dictation_id, sid)
    if not found:
        raise HTTPException(status_code=404, detail="no such dictation")
    result = await stt_format.polish(found["raw_text"])
    email = result["email"] or {}
    await anyio.to_thread.run_sync(
        db.update_dictation_polish, dictation_id, sid, result["polished"],
        result["is_email"], email.get("to", ""), email.get("subject", ""), email.get("body", ""),
    )
    return await anyio.to_thread.run_sync(db.get_dictation, dictation_id, sid)


@app.post("/dictations/{dictation_id}/send")
async def send_dictation(dictation_id: str, sid: str = Query(...), body: dict = Body(...)) -> dict:
    to = (body.get("to") or "").strip()
    subject = (body.get("subject") or "").strip() or "(no subject)"
    text = (body.get("body") or "").strip()
    if not _EMAIL_RE.match(to):
        raise HTTPException(status_code=400, detail="that doesn't look like a valid email address")
    if not text:
        raise HTTPException(status_code=400, detail="the email body is empty")
    found = await anyio.to_thread.run_sync(db.get_dictation, dictation_id, sid)
    if not found:
        raise HTTPException(status_code=404, detail="no such dictation")
    try:
        await anyio.to_thread.run_sync(mailer.send_email, to, subject, text)
    except mailer.MailerNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("dictation email send failed")
        raise HTTPException(status_code=502, detail=f"couldn't send: {exc}")
    await anyio.to_thread.run_sync(db.mark_dictation_sent, dictation_id, sid, to)
    return {"ok": True}


@app.post("/dictate")
async def dictate(sid: str = Query(...), body: dict = Body(...)) -> dict:
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="nothing was dictated")
    if len(text) > 120_000:
        raise HTTPException(status_code=400, detail="that dictation is too long — try splitting it up")
    try:
        tz_offset_min = int(body.get("tz_offset_min") or 0)
    except (TypeError, ValueError):
        tz_offset_min = 0
    day_id = daily.start(text, sid, tz_offset_min)
    return {"day_id": day_id}

@app.post("/upload")
async def upload(sid: str = Query(...), file: UploadFile = ...) -> dict:
    raw = await file.read()
    try:
        text = extract.extract_text(file.filename or "", raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not text:
        raise HTTPException(status_code=400, detail="no readable text in that file")
    return await anyio.to_thread.run_sync(
        rag_store.add_document, sid, file.filename, text
    )

@app.websocket("/ws")
async def voice_socket(ws: WebSocket) -> None:
    await ws.accept()
    session_id = ws.query_params.get("sid") or uuid4().hex
    user_name = ws.query_params.get("name") or ""
    greet = ws.query_params.get("greet") != "0"
    assistant_name = ws.query_params.get("persona") or ""
    instructions = ws.query_params.get("instructions") or ""
    personas = ws.query_params.get("personas") or ""
    voice = ws.query_params.get("voice") or ""
    try:
        await bridge(ws, session_id, user_name, greet, assistant_name, instructions, personas, voice)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("voice bridge failed")
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "error", "message": "Voice connection failed. Please try again."})

_MAX_DICTATION_S = 3600


@app.websocket("/ws/dictation")
async def dictation_socket(ws: WebSocket) -> None:
    await ws.accept()
    session_id = ws.query_params.get("sid") or uuid4().hex
    language = ws.query_params.get("language") or None
    hints = [h.strip() for h in (ws.query_params.get("hints") or "").split(",") if h.strip()]

    segmenter = Segmenter()
    transcript: list[str] = []
    utterances: asyncio.Queue = asyncio.Queue()
    send_lock = asyncio.Lock()

    async def send(payload: dict) -> None:
        async with send_lock:
            await ws.send_json(payload)

    async def worker() -> None:
        while True:
            utterance = await utterances.get()
            if utterance is None:
                return
            try:
                context = " ".join(transcript)[-400:]
                raw_text = await anyio.to_thread.run_sync(
                    stt_engine.transcribe, utterance, context, hints, language
                )
            except Exception:
                logger.exception("dictation transcribe failed")
                with contextlib.suppress(Exception):
                    await send({"type": "error", "message": "Transcription hiccuped — keep going."})
                continue
            text = punctuation.finalize_segment(raw_text)
            if text:
                transcript.append(text)
                await send({"type": "segment", "text": text})
                await send({"type": "interim", "text": ""})

    ready_event = asyncio.Event()

    async def announce() -> None:
        try:
            if not stt_engine.is_loaded():
                await send({"type": "info", "message": "Warming up the transcription model — the first run can take a minute. Keep talking, nothing is lost."})
            await anyio.to_thread.run_sync(stt_engine.load)
            await send({"type": "ready", "model": stt_engine.MODEL_NAME})
            ready_event.set()
        except Exception:
            logger.exception("STT model failed to load")
            with contextlib.suppress(Exception):
                await send({"type": "error", "message": "Couldn't load the local transcription model.", "fatal": True})
            with contextlib.suppress(Exception):
                await ws.close()

    _INTERIM_EVERY_S = 0.2
    _INTERIM_WINDOW_S = 3.0

    async def interim_loop() -> None:
        await ready_event.wait()
        last_sent = ""
        while True:
            await asyncio.sleep(_INTERIM_EVERY_S)
            tail = await anyio.to_thread.run_sync(segmenter.peek_tail, _INTERIM_WINDOW_S)
            if tail is None or len(tail) < 0.2 * stt_engine.SAMPLE_RATE:
                continue
            try:
                raw_text = await anyio.to_thread.run_sync(stt_engine.transcribe_interim, tail, language)
            except Exception:
                continue
            text = punctuation.apply_voice_commands(raw_text)
            if text != last_sent:
                last_sent = text
                with contextlib.suppress(Exception):
                    await send({"type": "interim", "text": text})

    announce_task = asyncio.create_task(announce())
    interim_task = asyncio.create_task(interim_loop())
    worker_task = asyncio.create_task(worker())
    total_samples = 0
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes"):
                total_samples += len(msg["bytes"]) // 2
                if total_samples > _MAX_DICTATION_S * stt_engine.SAMPLE_RATE:
                    await send({"type": "error", "message": "That's an hour of audio — wrapping up.", "fatal": True})
                    break
                for utterance in await anyio.to_thread.run_sync(segmenter.feed, msg["bytes"]):
                    utterances.put_nowait(utterance)
            elif msg.get("text"):
                try:
                    event = json.loads(msg["text"])
                except ValueError:
                    continue
                if event.get("type") == "stop":
                    break
        interim_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await interim_task
        for utterance in await anyio.to_thread.run_sync(segmenter.flush):
            utterances.put_nowait(utterance)
        utterances.put_nowait(None)
        await worker_task
        worker_task = None

        raw_text = punctuation.join_segments(transcript)
        dictation_id = db.new_id()
        if raw_text:
            await anyio.to_thread.run_sync(db.save_dictation, dictation_id, session_id, raw_text)

        result = {"polished": raw_text, "is_email": False, "email": None}
        if raw_text:
            with contextlib.suppress(Exception):
                await send({"type": "polishing"})
            try:
                result = await stt_format.polish(raw_text)
            except Exception:
                logger.exception("dictation polish failed")
            else:
                email = result["email"] or {}
                await anyio.to_thread.run_sync(
                    db.update_dictation_polish, dictation_id, session_id, result["polished"],
                    result["is_email"], email.get("to", ""), email.get("subject", ""), email.get("body", ""),
                )

        with contextlib.suppress(Exception):
            await send({
                "type": "final",
                "id": dictation_id if raw_text else None,
                "text": raw_text,
                "polished": result["polished"],
                "is_email": result["is_email"],
                "email": result["email"],
            })
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("dictation socket failed")
        with contextlib.suppress(Exception):
            await send({"type": "error", "message": "Dictation connection failed.", "fatal": True})
    finally:
        announce_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await announce_task
        interim_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await interim_task
        if worker_task is not None:
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task


app.include_router(calendar_auth_router)
app.mount("/mcp", mcp.streamable_http_app())

if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
