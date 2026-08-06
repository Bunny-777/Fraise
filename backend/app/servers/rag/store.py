import re
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import sqlite_vec

from app.storage.db import connect
from . import chunk, embeddings, reranker

_RRF_K = 60

def _match_query(text: str) -> str:
    terms = re.findall(r"\w+", text)
    return " OR ".join(f'"{t}"' for t in terms)

def add_document(session_id: str, filename: str, text: str, kind: str = "document") -> dict:
    token_vectors, spans = embeddings.encode_tokens(text)
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO documents (session_id, filename, uploaded_at, char_count, text, kind) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, filename, now, len(text), text, kind),
        )
        document_id = cur.lastrowid
        n_chunks = 0
        for ordinal, (start, end) in enumerate(chunk.windows(len(spans))):
            chunk_text = text[spans[start][0]:spans[end - 1][1]]
            vector = embeddings.pool_span(token_vectors[start:end])
            chunk_id = conn.execute(
                "INSERT INTO chunks (text, session_id, document_id, ordinal) VALUES (?, ?, ?, ?)",
                (chunk_text, session_id, document_id, ordinal),
            ).lastrowid
            conn.execute(
                "INSERT INTO vec_chunks (chunk_id, session_id, embedding) VALUES (?, ?, ?)",
                (chunk_id, session_id, sqlite_vec.serialize_float32(vector.tolist())),
            )
            n_chunks = ordinal + 1
    return {"filename": filename, "chunks": n_chunks, "kind": kind}

def search(session_id: str, query: str, k: int = 5, n: int = 30) -> list[str]:
    qvec = embeddings.encode_query(query)
    with closing(connect()) as conn:
        dense = [
            r["chunk_id"]
            for r in conn.execute(
                "SELECT chunk_id FROM vec_chunks "
                "WHERE embedding MATCH ? AND k = ? AND session_id = ?",
                (sqlite_vec.serialize_float32(qvec.tolist()), n, session_id),
            ).fetchall()
        ]
        lexical = []
        if match := _match_query(query):
            lexical = [
                r["chunk_id"]
                for r in conn.execute(
                    "SELECT rowid AS chunk_id FROM chunks "
                    "WHERE session_id = ? AND chunks MATCH ? ORDER BY rank LIMIT ?",
                    (session_id, match, n),
                ).fetchall()
            ]
        candidates = _rrf_fuse(dense, lexical)[:n]
        if not candidates:
            return []
        rows = conn.execute(
            f"SELECT rowid, text FROM chunks WHERE rowid IN ({','.join('?' * len(candidates))})",
            candidates,
        ).fetchall()

    texts_by_id = {r["rowid"]: r["text"] for r in rows}
    passages = [texts_by_id[c] for c in candidates if c in texts_by_id]
    order = reranker.rerank(query, passages)
    return [passages[i] for i in order[:k]]

def _rrf_fuse(*ranked_lists: list[int]) -> list[int]:
    scores: dict[int, float] = {}
    for ids in ranked_lists:
        for rank, chunk_id in enumerate(ids):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (_RRF_K + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)

def list_documents(session_id: str) -> list[dict]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT filename, kind FROM documents WHERE session_id = ? ORDER BY id DESC",
            (session_id,),
        ).fetchall()
    return [{"filename": r["filename"], "kind": r["kind"]} for r in rows]

def get_document_text(session_id: str, filename: str = "", budget: int = 6000) -> str:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT filename, text FROM documents WHERE session_id = ? ORDER BY id DESC",
            (session_id,),
        ).fetchall()
    if not rows:
        return ""
    if not filename:
        return rows[0]["text"][:budget]
    target = Path(filename).stem.lower()
    match = next(
        (r for r in rows if r["filename"] == filename or Path(r["filename"]).stem.lower() == target),
        None,
    )
    return match["text"][:budget] if match else ""
