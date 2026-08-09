import logging
import threading

import numpy as np

from app.servers.rag import embeddings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_names: list[str] = []
_servers: list[str] = []
_matrix: np.ndarray | None = None

def _text(server: str, name: str, description: str) -> str:
    """Deliberately the tool's own words only. Folding the server's contract in
    here was measured and made things worse — it makes every tool in a skill read
    alike, and recall@6 fell from 100% to 94%."""
    spoken = name.replace("_", " ")
    first = (description or "").split("\n")[0]
    return f"{spoken}. {first} Part of the {server} skill."

def build(tools: list[dict]) -> None:
    """Embed every connected tool once, so selection later is a dot product."""
    global _matrix, _names, _servers
    if not tools:
        with _lock:
            _names, _servers, _matrix = [], [], None
        return
    try:
        vectors = [
            embeddings.encode_query(_text(t["server"], t["name"], t["description"]))
            for t in tools
        ]
    except Exception:
        logger.exception("tool index unavailable — falling back to the full tool list")
        with _lock:
            _names, _servers, _matrix = [], [], None
        return
    with _lock:
        _names = [t["name"] for t in tools]
        _servers = [t["server"] for t in tools]
        _matrix = np.vstack(vectors)

def ready() -> bool:
    return _matrix is not None

def _scores(query: str) -> np.ndarray | None:
    with _lock:
        matrix, names = _matrix, list(_names)
    if matrix is None or not names or not query.strip():
        return None
    try:
        return matrix @ embeddings.encode_query(query)
    except Exception:
        logger.exception("tool selection failed — falling back to the full tool list")
        return None

def select(query: str, k: int, pinned: set[str] | None = None) -> list[str] | None:
    """The k tools most relevant to this request, or None to send them all."""
    scores = _scores(query)
    if scores is None:
        return None
    with _lock:
        names = list(_names)
    if k >= len(names):
        return names
    order = np.argsort(-scores)
    chosen = [names[i] for i in order[:k]]
    for name in pinned or ():
        if name in names and name not in chosen:
            chosen.append(name)
    return chosen

def rank_servers(query: str) -> list[str] | None:
    scores = _scores(query)
    if scores is None:
        return None
    with _lock:
        servers = list(_servers)
    best: dict[str, float] = {}
    for server, score in zip(servers, scores):
        best[server] = max(best.get(server, -1.0), float(score))
    return sorted(best, key=best.get, reverse=True)
