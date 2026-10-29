"""yomiyasu: polish LLM-written Japanese (daily report / blog bodies).

Bundles the yomiyasu linter (app/vendor/yomiyasu, MIT, (c) 2026 nanaism,
rev 41e5aec). lint() runs the linter as a subprocess and returns warn/error
candidates. polish() asks the report LLM (same transport/function as the
daily report and blog generation) for one rewrite and keeps it only when
the acceptance checks pass. Everything here is fail-soft: on lint or LLM
failure/timeout the original text is returned (one log line) and report /
blog generation itself never fails because of polishing.
"""
import json
import logging
import re
import subprocess
import sys
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

_VENDOR = Path(__file__).resolve().parent / "vendor" / "yomiyasu"
_LINT_SCRIPT = _VENDOR / "scripts" / "yomiyasu_lint.py"
_RULES_FILE = _VENDOR / "rules.md"
_LINT_TIMEOUT_SEC = 20.0
# Evidence times like (14:05) / （14:05） must survive a rewrite. The report
# prompts write (HH:MM); the acceptance rule names the full-width form.
_TIME_RE = re.compile(r"[（(]\d{1,2}:\d{2}[)）]")
# A rewrite outside this length ratio vs. the original is rejected.
_MIN_LEN_RATIO = 0.7
_MAX_LEN_RATIO = 1.3

_KIND_LABELS = {
    "daily_report": "日報",
    "blog_summary": "1日のまとめのブログ記事",
    "blog_learning": "技術的な学びをまとめたブログ記事",
}


def lint(text: str) -> list[dict]:
    """Warn/error candidates from the bundled yomiyasu linter (info dropped).

    Returns [] on any failure (script missing, timeout, bad output) - the
    caller keeps the text as-is; fail-soft.
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(_LINT_SCRIPT), "--json", "--no-words", "-"],
            input=str(text),
            capture_output=True,
            text=True,
            timeout=_LINT_TIMEOUT_SEC,
        )
        # 1 only happens with --strict/--fail-on (unused here); 2/3 are errors.
        if proc.returncode not in (0, 1):
            log.warning(
                "yomiyasu lint exited %s: %s",
                proc.returncode,
                (proc.stderr or "").strip()[:200],
            )
            return []
        data = json.loads(proc.stdout)
        return [
            f for f in data.get("findings") or []
            if f.get("severity") in ("warn", "error")
        ]
    except Exception as e:  # noqa: BLE001 - polish must never break generation
        log.warning("yomiyasu lint failed: %s: %s", type(e).__name__, e)
        return []


def _strip_fences(md: str) -> str:
    """Drop accidental code fences around a rewrite (no title guarantee)."""
    md = md.strip()
    lines = md.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _accepted(original: str, rewritten: str, old_count: int) -> bool:
    """Rewrite is adopted only when all of these hold:
    - fewer warn/error candidates than before,
    - length within 70%-130% of the original,
    - every (HH:MM) time of the original still present.
    """
    new_count = len(lint(rewritten))
    if not new_count < old_count:
        return False
    ratio = len(rewritten) / max(len(original), 1)
    if not _MIN_LEN_RATIO <= ratio <= _MAX_LEN_RATIO:
        return False
    return all(t in rewritten for t in _TIME_RE.findall(original))


def _rewrite_messages(text: str, kind: str, findings: list[dict]) -> list[dict]:
    rules = _RULES_FILE.read_text(encoding="utf-8")
    label = _KIND_LABELS.get(kind, "ブログ記事")
    cand_lines = "\n".join(
        f"- {f.get('line')}行目 [{f.get('rule')}] "
        f"{(f.get('snippet') or '').strip()}"
        for f in findings
    )
    system = (
        "あなたは日本語の推敲役です。AIが書いた文章を、与えられた規則に従って書き直します。"
        "余計な前置きや説明を書かず、書き直した本文だけを返します。"
    )
    user = (
        f"次の{label}の本文を、下の規則に従って書き直してください。\n"
        "守ること:\n"
        "- 意味・事実・数字・時刻・固有名詞・Markdownの構造（見出し・リンク・"
        "コードブロック・時刻の根拠「（14:05）」）を変えない。\n"
        "- 直すのは下の候補の箇所と、その前後の言い回しだけ。\n"
        "- 出力は書き直した本文のみ（前置き・後書き・コードフェンスなし）。\n\n"
        f"## 点検で見つかった見直し候補（行・規則名・該当箇所）\n{cand_lines}\n\n"
        f"## 規則\n{rules}\n\n## 本文\n{text}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def polish(text: str, kind: str) -> str:
    """Lint `text` and, when candidates exist, rewrite it once via the report
    LLM. Returns the polished text only when the acceptance checks pass;
    otherwise (and on any failure, or YOMIYASU_POLISH=0, or the fake
    provider which cannot rewrite) the original text unchanged.
    """
    if not config.YOMIYASU_POLISH:
        return text
    text = str(text)
    candidates = lint(text)
    if not candidates:
        return text
    if config.LLM_PROVIDER == "fake":
        log.info(
            "yomiyasu: %d candidates in %s kept as-is (LLM_PROVIDER=fake)",
            len(candidates), kind,
        )
        return text
    # Same transport/function as the daily report and blog generation
    # (lazy import: daily_report imports this module).
    from .daily_report import _llm_chat

    try:
        rewritten = _strip_fences(
            _llm_chat(config.REPORT_LLM_MODEL, _rewrite_messages(text, kind, candidates))
        )
    except Exception as e:  # noqa: BLE001 - fail-soft: keep the original
        log.warning("yomiyasu polish (%s) LLM call failed: %s", kind, e)
        return text
    if not rewritten or not _accepted(text, rewritten, len(candidates)):
        log.info("yomiyasu polish (%s): rewrite rejected, keeping original", kind)
        return text
    log.info(
        "yomiyasu polish (%s): adopted rewrite (%d -> fewer candidates)", kind,
        len(candidates),
    )
    return rewritten
