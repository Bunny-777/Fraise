import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Literal

from mcp.server.fastmcp import FastMCP

from app.host import bus
from app.servers.research import llm, search
from app.storage import db

logger = logging.getLogger(__name__)

mcp = FastMCP("research")

MIN_AGENTS = 2
MAX_AGENTS = 4
_AGENT_BUDGET = 60

_running: set[asyncio.Task] = set()

def _today_line() -> str:
    return datetime.now(timezone.utc).strftime("Today's date is %B %-d, %Y.")

_PLANNER_SYSTEM = (
    "You plan a parallel research team. Given one question, design the agents that "
    "should go and look — each on a genuinely different angle, all at the same time.\n\n"
    f"Return JSON: {{\"agents\": [...]}} with {MIN_AGENTS} to {MAX_AGENTS} agents. Each agent is:\n"
    '  "label": 1-2 words naming what this agent IS — either the site it searches, or '
    "the angle it covers.\n"
    '  "query": the search query THIS agent runs. Specific to its angle, and different '
    "from every other agent's — never repeat a query. For anything time-sensitive "
    "(jobs, internships, prices, rankings, versions, current events), include the "
    "current year in the query text so search engines don't hand back old results.\n"
    '  "domains": bare hostnames to restrict this agent to, like "example.com". Use them '
    "only when a site is genuinely the natural home for that information, and only for "
    "sites you are confident exist and cover this topic. Use [] to search the open web.\n"
    '  "angle": one line telling the agent what to extract.\n'
    '  "recency": how fresh results must be — one of "day", "week", "month", "year", '
    '"none". Use a tight window ("week"/"month") for things that change constantly '
    "(job postings, prices, news), \"year\" for things that update annually or "
    "seasonally (rankings, buying guides), and \"none\" only for genuinely evergreen "
    "topics (how something works, historical facts) where restricting by date would "
    "throw away the best source.\n\n"
    "Hard rules:\n"
    "- EVERY agent must be relevant to THIS question. Derive the sources from the "
    "question itself. Never reach for a site because it is famous or because it fits "
    "some other kind of question — a job board has no place in a question about cars.\n"
    "- At least one agent must search the open web (domains: []).\n"
    "- Prefer open web over a narrow domain list you are unsure about: over-scoping "
    "returns nothing at all, which is worse than a broad search.\n"
    "- If the user names sources, those must be among the agents."
)

def _slug(text: str, i: int) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or f"agent-{i}"

_DOMAIN_RE = re.compile(r"^(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)/?$", re.I)

def _clean_domains(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = []
    for d in raw:
        m = _DOMAIN_RE.match(str(d).strip())
        if m:
            out.append(m.group(1).lower())
        elif str(d).strip():
            logger.info("planner emitted a non-domain, dropping: %r", str(d)[:60])
    return out[:4]

def _fallback_plan(query: str) -> list[dict]:
    return [
        {"id": "web", "label": "Web", "query": query, "domains": [],
         "angle": "anything that directly answers the question", "recency": "year"},
        {"id": "background", "label": "Background", "query": f"{query} guide overview",
         "domains": [], "angle": "context, comparisons, and things worth knowing",
         "recency": "year"},
    ]

async def _plan(query: str, hint: str) -> list[dict]:
    ask = f"{_today_line()}\n\nQuestion: {query}"
    if hint:
        ask += f"\nThe user specifically asked for these sources: {hint}"

    try:
        data = await llm.complete_json(_PLANNER_SYSTEM, ask, model=llm.SYNTH_MODEL)
    except llm.LLMUnavailable as exc:
        logger.warning("planner unavailable (%s); falling back", exc)
        return _fallback_plan(query)

    raw = data.get("agents")
    if not isinstance(raw, list) or not raw:
        return _fallback_plan(query)

    agents: list[dict] = []
    seen: set[str] = set()
    for i, a in enumerate(raw[:MAX_AGENTS]):
        if not isinstance(a, dict):
            continue
        label = str(a.get("label") or "").strip()[:24]
        q = str(a.get("query") or "").strip() or query
        if not label:
            continue

        base = _slug(label, i)
        agent_id = base
        n = 2
        while agent_id in seen:
            agent_id = f"{base}-{n}"
            n += 1
        seen.add(agent_id)

        recency = str(a.get("recency") or "").strip().lower()
        if recency not in ("day", "week", "month", "year", "none"):
            recency = "year"

        agents.append({
            "id": agent_id,
            "label": label,
            "query": q,
            "domains": _clean_domains(a.get("domains")),
            "angle": str(a.get("angle") or "what answers the question")[:200],
            "recency": recency,
        })

    if len(agents) < MIN_AGENTS:
        return _fallback_plan(query)
    return agents

def _agent_system(label: str, angle: str) -> str:
    return (
        f"You are the {label} research agent. You are given search results. "
        f"Focus on: {angle}. Extract only what the results actually say — never invent "
        "a fact, company, price, salary, or link. Reply with 3-6 tight bullet points, "
        "each concrete and specific. If the results are thin or irrelevant, say so. If "
        "a result is visibly outdated (an old year, a past cycle or season) and fresher "
        "results are also present, prefer the fresher ones and skip the stale one rather "
        f"than reporting both as current.\n\n{_today_line()}\n\n"
        + llm.PLAIN_TEXT_RULE
    )

async def _run_agent(spec: dict, run_id: str, session_id: str) -> dict:
    name, label = spec["id"], spec["label"]
    started = time.monotonic()

    def step(status: str, note: str, **extra) -> None:
        bus.publish(session_id, {
            "type": "agent", "run_id": run_id, "agent": name, "label": label,
            "status": status, "note": note, **extra,
        })

    scope = ", ".join(spec["domains"]) if spec["domains"] else "the open web"
    step("searching", f"Searching {scope} for “{spec['query']}”")

    recency = spec.get("recency", "year")

    try:
        results = await asyncio.wait_for(
            search.search(spec["query"], spec["domains"], time_range=recency), _AGENT_BUDGET
        )
        if not results and spec["domains"]:
            step("searching", "Nothing there — widening to the open web")
            results = await asyncio.wait_for(
                search.search(spec["query"], [], time_range=recency), _AGENT_BUDGET
            )

        if not results and recency != "none":
            step("searching", "Nothing that recent — widening the date range")
            results = await asyncio.wait_for(
                search.search(spec["query"], spec["domains"], time_range=None), _AGENT_BUDGET
            )

        if not results:
            step("done", f"Nothing matched {label}.", found=0, summary="")
            return {"agent": name, "label": label, "findings": "", "sources": []}

        titles = [r["title"] for r in results if r["title"]][:5]
        step("reading", f"Found {len(results)} results — reading them", found=len(results), titles=titles)

        corpus = "\n\n".join(
            f"[{i + 1}] {r['title']}\n{r['url']}\n{r['content']}"
            for i, r in enumerate(results)
        )
        step("thinking", "Pulling out what actually answers the question",
             found=len(results), titles=titles)

        raw = await asyncio.wait_for(
            llm.complete(
                _agent_system(label, spec["angle"]),
                f"Query: {spec['query']}\n\nResults:\n{corpus}",
            ),
            _AGENT_BUDGET,
        )
        findings = "\n".join(line for line in llm.strip_markdown(raw).splitlines() if line)
        elapsed = round(time.monotonic() - started, 1)
        step("done", f"Done in {elapsed}s",
             found=len(results), titles=titles, summary=findings, elapsed=elapsed)
        return {"agent": name, "label": label, "findings": findings, "sources": results}

    except Exception as exc:
        logger.warning("agent %s failed: %s", name, exc)
        step("failed", "Couldn't finish", error=str(exc)[:160])
        return {"agent": name, "label": label, "findings": "", "sources": [], "error": str(exc)}

_SYNTH_SYSTEM = (
    "You merge findings from several research agents into one artifact.\n"
    "Rules: use ONLY what the agents reported — never invent a fact, company, "
    "number, or link. Where agents agree, say it once. Where they conflict or a "
    "source found nothing, note it honestly. You are told today's date below — if "
    "the agents' findings mix old and current information, lead with what's current "
    "and only mention outdated figures if there's nothing fresher to replace them.\n\n"
    "Return JSON with exactly these keys:\n"
    '  "title": a short headline (max 8 words)\n'
    '  "spoken": ONE or TWO sentences a voice assistant reads aloud. Natural English, '
    "no markdown, no lists, no URLs — the headline finding plus a nudge to look at "
    "the write-up.\n"
    '  "sections": an array of 3-6 objects, each {"heading": str, "bullets": [str, ...]}\n'
    "Each bullet is one concrete, specific sentence.\n\n"
    + llm.PLAIN_TEXT_RULE
)

async def _synthesize(query: str, agent_results: list[dict]) -> dict:
    usable = [a for a in agent_results if a.get("findings")]
    if not usable:
        return {
            "title": "No results",
            "spoken": "I couldn't find anything solid on that — the sources came back "
                      "empty. Want me to try different wording?",
            "sections": [],
        }

    report = "\n\n".join(f"### {a['label']} agent\n{a['findings']}" for a in usable)
    data = await llm.complete_json(
        _SYNTH_SYSTEM,
        f"{_today_line()}\n\nOriginal request: {query}\n\n{report}",
        model=llm.SYNTH_MODEL
    )

    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        sections = [
            {"heading": a["label"],
             "bullets": [l for l in a["findings"].splitlines() if l.strip()]}
            for a in usable
        ]

    clean: list[dict] = []
    for s in sections:
        if not isinstance(s, dict):
            continue
        bullets = [
            b for b in (llm.strip_markdown(str(x)) for x in (s.get("bullets") or []))
            if b
        ]
        heading = llm.strip_markdown(str(s.get("heading") or ""))
        if heading or bullets:
            clean.append({"heading": heading, "bullets": bullets})

    return {
        "title": llm.strip_markdown(str(data.get("title") or "")) or query[:60],
        "spoken": llm.strip_markdown(str(data.get("spoken") or ""))
                  or "I've pulled the findings together — take a look.",
        "sections": clean,
    }

async def _run(run_id: str, query: str, hint: str, fmt: str, session_id: str) -> None:
    try:
        await _run_impl(run_id, query, hint, fmt, session_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("research run %s crashed", run_id)
        bus.publish(session_id, {
            "type": "run", "run_id": run_id, "status": "failed", "error": str(exc)[:160],
        })

async def _run_impl(run_id: str, query: str, hint: str, fmt: str, session_id: str) -> None:
    bus.publish(session_id, {
        "type": "run", "run_id": run_id, "status": "planning", "query": query,
        "format": fmt, "agents": [],
        "note": "Working out who should go looking",
    })

    plan = await _plan(query, hint)

    bus.publish(session_id, {
        "type": "run", "run_id": run_id, "status": "running", "query": query, "format": fmt,
        "agents": [
            {"agent": a["id"], "label": a["label"], "status": "queued",
             "note": f"Will search {', '.join(a['domains']) if a['domains'] else 'the open web'}"}
            for a in plan
        ],
    })

    results = await asyncio.gather(*(_run_agent(a, run_id, session_id) for a in plan))

    if not any(result.get("findings") for result in results):
        errors = [str(result["error"]) for result in results if result.get("error")]
        error = errors[0] if errors else "No sources returned results. Try a different query."
        bus.publish(session_id, {
            "type": "run", "run_id": run_id, "status": "failed", "error": error[:160],
        })
        return

    bus.publish(session_id, {
        "type": "run", "run_id": run_id, "status": "synthesizing",
        "note": "Merging what the agents found into one answer",
    })
    try:
        artifact = await _synthesize(query, list(results))
    except llm.LLMUnavailable as exc:
        logger.warning("synthesis failed: %s", exc)
        bus.publish(session_id, {
            "type": "run", "run_id": run_id, "status": "failed", "error": str(exc)[:160],
        })
        return

    citations: list[dict] = []
    seen_urls: set[str] = set()
    for a in results:
        for s in a.get("sources", [])[:4]:
            if s["url"] in seen_urls:
                continue
            seen_urls.add(s["url"])
            citations.append({"label": a["label"], "title": s["title"], "url": s["url"]})
    body = {
        "title": artifact["title"],
        "query": query,
        "format": fmt,
        "sections": artifact["sections"],
        "citations": citations,
        "agents": [
            {"agent": a["agent"], "label": a["label"], "ok": bool(a.get("findings"))}
            for a in results
        ],
    }
    await asyncio.to_thread(
        db.save_artifact, run_id, session_id, artifact["title"], fmt, json.dumps(body)
    )

    bus.publish(session_id, {
        "type": "run", "run_id": run_id, "status": "done",
        "artifact": body, "spoken": artifact["spoken"],
    })

@mcp.tool(description=(
    "Put a team of research agents to work IN PARALLEL on one question, each on a "
    "different source or angle, then collect their findings into a document or slide "
    "deck the user can read on screen.\n\n"
    "The agents are chosen to fit the question — job boards for a job search, review "
    "sites and forums for a product comparison, and so on. You do not pick them.\n\n"
    "This is the right tool whenever the answer deserves more than a sentence: "
    "finding jobs or internships, comparing options, researching a company, topic, or "
    "market, or any request for a write-up, report, summary, document, or slides. "
    "Prefer it over tavily_search or tavily_research for those — those return raw "
    "results, while this runs several agents and produces a written artifact.\n\n"
    "query: the user's actual question, in their own words — do not paraphrase or "
    "narrow it, e.g. \"best SDE internships for 2026 grads\".\n"
    "sources: only if the user names specific places to look (\"check LinkedIn and "
    "Reddit\"). Pass their words through. Leave empty otherwise.\n"
    "format: \"slides\" if they ask for a deck or presentation, otherwise \"doc\"."
))
async def deep_research(
    query: str,
    sources: str = "",
    format: Literal["doc", "slides"] = "doc",
    session_id: str = "",
) -> str:
    run_id = db.new_id()

    task = asyncio.create_task(_run(run_id, query, sources, format, session_id))
    _running.add(task)
    task.add_done_callback(_running.discard)

    return json.dumps({
        "spoken": "On it — I'm putting a team of agents on that now. I'll pull their "
                  f"findings into a {'deck' if format == 'slides' else 'write-up'} "
                  "for you in a moment.",
        "run_id": run_id,
        "note": "Agents are being planned and run in the background. Tell the user "
                "they're working; do not invent findings. The write-up appears in "
                "their UI when ready.",
    })
