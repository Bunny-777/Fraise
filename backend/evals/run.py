import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.storage import db

db.DB_PATH = Path(tempfile.mkdtemp()) / "evals.db"

from app.host.mcp_manager import manager
from app.host import voice_agent
from app.servers.calculator import mcp as calculator
from evals import cases, speakable

GROQ_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
ROUTER_MODEL = os.environ.get("EVAL_ROUTER_MODEL", "llama-3.3-70b-versatile")

class Report:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def check(self, ok: bool, label: str, detail: str = "") -> None:
        if ok:
            self.passed += 1
        else:
            self.failed.append(f"{label}{f' — {detail}' if detail else ''}")

    def section(self, name: str) -> None:
        print(f"\n{name}")

    def line(self, ok: bool, label: str) -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")

def speech_suite(report: Report) -> None:
    report.section("Speakable output")
    for label, text, expected in cases.SPEECH:
        found = speakable.problems(text)
        ok = (not found) == expected
        report.line(ok, f"{label}: {'clean' if not found else ', '.join(found)}")
        report.check(ok, f"speech/{label}", f"expected speakable={expected}")

def debrief_suite(report: Report) -> None:
    report.section("Recording debrief sanitizes model output")
    from app.servers.rag import insights

    sections = [
        {"heading": heading, "bullets": insights._lines(cases.MESSY_DEBRIEF.get(key))}
        for key, heading in insights._SECTIONS
    ]
    for section in sections:
        for bullet in section["bullets"]:
            found = speakable.problems(bullet)
            report.line(not found, f"{section['heading']}: {bullet[:52]}")
            report.check(not found, f"debrief/{section['heading']}", ", ".join(found))

    empties = [s["heading"] for s in sections if not s["bullets"]]
    report.line(True, f"empty sections dropped: {empties or 'none'}")
    dropped = len(cases.MESSY_DEBRIEF["follow_ups"]) - len(
        insights._lines(cases.MESSY_DEBRIEF["follow_ups"])
    )
    report.check(dropped == 2, "debrief/null-bullets", f"dropped {dropped}, expected 2")
    report.line(dropped == 2, f"null and empty bullets dropped: {dropped}")

def contract_suite(report: Report) -> None:
    report.section("Every skill declares its own contract")
    declared = manager.instructions_by_server()
    for name in manager.functions_by_server():
        has = bool(declared.get(name))
        report.line(has, f"{name}: {'declares instructions' if has else 'NO INSTRUCTIONS'}")
        report.check(has, f"contract/{name}", "server has no instructions")

async def prompt_suite(report: Report) -> None:
    report.section("Composed prompt carries every contract")
    settings = await voice_agent._build_settings(session_id="", user_name="Eval")
    prompt = settings["agent"]["think"]["prompt"]
    for name, text in manager.instructions_by_server().items():
        first = text.splitlines()[0][:60]
        ok = first in prompt
        report.line(ok, f"{name} contract present")
        report.check(ok, f"prompt/{name}", "contract missing from composed prompt")

    declared = {f["name"] for f in settings["agent"]["think"]["functions"]}
    ok = voice_agent.ANSWER_TOOL in declared
    report.line(ok, f"host tool {voice_agent.ANSWER_TOOL} declared")
    report.check(ok, "prompt/answer-tool", "elicitation answer tool missing")
    print(f"  ({len(prompt)} chars, {len(declared)} tools)")

async def _route(client, prompt: str, functions: list[dict], utterance: str) -> str | None:
    key = os.environ.get("GROQ_API_KEY")
    payload = {
        "model": ROUTER_MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": utterance},
        ],
        "tools": [{"type": "function", "function": f} for f in functions],
        "tool_choice": "auto",
        "temperature": 0,
    }
    for attempt in range(5):
        r = await client.post(
            f"{GROQ_URL}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
        )
        if r.status_code == 429:
            wait = float(r.headers.get("retry-after", 0)) or 2 ** attempt
            await asyncio.sleep(min(wait, 30))
            continue
        if r.status_code == 400 and "tool_use_failed" in r.text:
            return None
        if r.status_code >= 400:
            raise RuntimeError(f"router {r.status_code}: {r.text[:120]}")
        calls = r.json()["choices"][0]["message"].get("tool_calls") or []
        return calls[0]["function"]["name"] if calls else None
    raise RuntimeError("router rate limited — out of retries")

async def routing_suite(report: Report) -> None:
    report.section(f"Tool routing ({ROUTER_MODEL})")
    if not os.environ.get("GROQ_API_KEY"):
        print("  skipped — GROQ_API_KEY not set")
        return

    settings = await voice_agent._build_settings(session_id="", user_name="Eval")
    prompt = settings["agent"]["think"]["prompt"]
    functions = settings["agent"]["think"]["functions"]

    import httpx

    async with httpx.AsyncClient(timeout=90) as client:
        for utterance, expected in cases.ROUTING:
            try:
                got = await _route(client, prompt, functions, utterance)
            except Exception as exc:
                report.line(False, f"{utterance[:44]} — error: {exc}")
                report.check(False, f"routing/{utterance[:32]}", str(exc)[:80])
                continue
            ok = got == expected if expected else got != "deep_research"
            label = f"{utterance[:44]:<46} -> {got or 'no tool'}"
            if expected and got != expected:
                label += f"  (wanted {expected})"
            report.line(ok, label)
            report.check(ok, f"routing/{utterance[:32]}", f"got {got}, wanted {expected}")

async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--routing", action="store_true", help="include live LLM routing cases")
    args = parser.parse_args()

    report = Report()
    speech_suite(report)
    debrief_suite(report)

    calculator.streamable_http_app()
    async with calculator.session_manager.run():
        await manager.connect_all()
        try:
            contract_suite(report)
            await prompt_suite(report)
            if args.routing:
                await routing_suite(report)
        finally:
            await manager.aclose()

    total = report.passed + len(report.failed)
    print(f"\n{report.passed}/{total} passed")
    for failure in report.failed:
        print(f"  FAILED  {failure}")
    return 1 if report.failed else 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
