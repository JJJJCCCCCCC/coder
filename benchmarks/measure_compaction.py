"""Measure Coder's oversized tool-result compaction with official DeepSeek usage."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import random
import re
import sys
import tempfile
import time
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MODEL = "deepseek-flash"
ENDPOINT = "https://api.deepseek.com/anthropic/v1/messages"
RAW_CHARS = 20_000
TARGET_REDUCTION = 0.85
SUITE = "synthetic-tool-results-v1"
CATEGORIES = (
    "jsonl_manifest", "test_table", "build_log", "markdown_notes",
    "csv_inventory", "unicode_report", "source_excerpt", "repeated_characters",
)
FIELDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
SYSTEM = "You are validating a synthetic tool output. Return only the requested code."
TOOLS = [{"name": "read_synthetic_fixture", "description": "Read a synthetic fixture.",
          "input_schema": {"type": "object", "properties": {"category": {"type": "string"}},
                           "required": ["category"]}}]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fixture(category, index):
    rng = random.Random(431_927 + index)
    visible = f"VISIBLE-{rng.randrange(100_000, 999_999)}"
    archive = f"ARCHIVE-{rng.randrange(100_000, 999_999)}"
    headline = f"Synthetic tool result\nVisible validation code: {visible}\n\n"
    footer = f"\nArchive-only verification code: {archive}\n"
    if category == "repeated_characters":
        body = "x" * RAW_CHARS
    else:
        rows = []
        for number in range(600):
            code = rng.randrange(1000, 99_999)
            details = f"fixture_{number:04d}_{code}"
            row = {
                "jsonl_manifest": json.dumps({"case": details, "name": "sample build", "state": "ok"}),
                "test_table": f"| {details} | {code % 71} ms | passed | synthetic scenario |",
                "build_log": f"phase {number:04d} [{details}] artifact approved in {code % 113}ms",
                "markdown_notes": f"- Demo item {number}: {details}. Notes on generated input and review.",
                "csv_inventory": f"{number},{details},module-{number % 37},{code % 229},generated,true",
                "unicode_report": f"示例批次 {number}：合成条目 {details}，已核对价格、单位与备注。",
                "source_excerpt": f"def sample_{number}(value): return value + {code}  # {details}",
            }[category]
            rows.append(row)
        body = "\n".join(rows)
    text = headline + body[:RAW_CHARS - len(headline) - len(footer)] + footer
    assert len(text) == RAW_CHARS
    return text, visible, archive


def messages(category, index, tool_result):
    call_id = f"toolu_fixture{index:02d}"
    return [
        {"role": "user", "content": "Return the visible validation code from the tool result."},
        {"role": "assistant", "content": [{"type": "tool_use", "id": call_id,
                                          "name": TOOLS[0]["name"], "input": {"category": category}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id,
                                      "content": tool_result}]},
    ]


def load_compactor(coder_root):
    sys.path.insert(0, str(coder_root))
    from coder_harness.context import Compactor
    return Compactor


def prepare_suite(compactor):
    suite = []
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="coder-compression-") as temp:
        try:
            os.chdir(temp)
            for index, category in enumerate(CATEGORIES, start=1):
                raw, visible, tail = fixture(category, index)
                state = Path("benchmark-archive") / category
                conversation = messages(category, index, raw)
                instance = compactor(SimpleNamespace(state=state))
                with redirect_stdout(io.StringIO()):
                    instance.prepare(conversation)
                block = conversation[-1]["content"][0]
                short = block["content"]
                archive = list((state / "outputs").glob("*.txt"))
                assert len(archive) == 1
                saved = archive[0].read_text(encoding="utf-8")
                assert saved == raw and tail in saved and tail not in short
                assert visible in short and block["tool_use_id"] == f"toolu_fixture{index:02d}"
                assert len(short) < 3000
                # Normalize separators only. The serialized archive path has the same characters.
                short = short.replace("\\", "/")
                suite.append({
                    "category": category, "index": index, "original": raw, "compressed": short,
                    "visible": visible, "raw_sha256": sha256(raw), "archive_sha256": sha256(saved),
                    "archive_tail_recovered": True,
                    "tool_use_id_retained": True,
                })
        finally:
            os.chdir(original_cwd)
    return suite


def configured_key(env_file):
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key and env_file:
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "DEEPSEEK_API_KEY":
                key = value.strip().strip("\"'")
                break
    if not key:
        raise SystemExit("No DEEPSEEK_API_KEY configured")
    return key


def call(key, case, arm, direct):
    result = "." if arm == "envelope" else case[arm]
    body = {
        "model": MODEL, "max_tokens": 32, "thinking": {"type": "disabled"},
        "temperature": 0, "messages": messages(case["category"], case["index"], result),
        "system": SYSTEM, "tools": TOOLS,
    }
    raw_request = json.dumps(body)
    request = Request(ENDPOINT, data=raw_request.encode("utf-8"),
                      headers={"Content-Type": "application/json", "x-api-key": key}, method="POST")
    opener = build_opener(NoRedirect(), *([ProxyHandler({})] if direct else []))
    for retry in range(3):
        try:
            with opener.open(request, timeout=30) as response:
                completion = json.load(response)
                status = response.status
            break
        except HTTPError as error:
            status = error.code
            error.close()
            if status not in (429, 500, 502, 503, 529) or retry == 2:
                raise RuntimeError(f"DeepSeek returned HTTP {status}") from None
        except (OSError, URLError) as error:
            if retry == 2:
                raise RuntimeError(f"DeepSeek transport failed: {type(error).__name__}") from None
        time.sleep(2 ** retry)
    usage = completion["usage"]
    total = sum(usage.get(field, 0) for field in FIELDS)
    assert status == 200 and total > 0
    answer = "".join(block["text"] for block in completion["content"] if block["type"] == "text")
    return {
        "case": case["category"], "arm": arm, "completion_id": completion["id"],
        "model": completion["model"], "status": status, "usage": usage,
        "total_input_tokens": total, "payload_sha256": sha256(raw_request),
        "result_sha256": sha256(result), "result_characters": len(result),
        "response_text": answer, "visible_code_correct": bool(re.fullmatch(
            re.escape(case["visible"]), answer.strip().strip(" \x60.").strip()
        )),
        "expected_visible_code": case["visible"],
    }


def summarize(rows, audit):
    by_case = {}
    ids = set()
    assert len(audit) == len(CATEGORIES)
    for row in rows:
        assert row["completion_id"] not in ids
        ids.add(row["completion_id"])
        assert row["status"] == 200
        assert row["model"] == MODEL
        assert row["total_input_tokens"] == sum(row["usage"].get(field, 0) for field in FIELDS)
        by_case.setdefault(row["case"], {})[row["arm"]] = row

    per_case = []
    totals = {arm: 0 for arm in ("original", "compressed", "envelope")}
    usable = {arm: 0 for arm in ("original", "compressed")}
    assert len(rows) == 3 * len(CATEGORIES)
    assert set(by_case) == set(CATEGORIES)
    for category in CATEGORIES:
        values = by_case[category]
        assert len(values) == 3
        for arm in totals:
            totals[arm] += values[arm]["total_input_tokens"]
        for arm in usable:
            usable[arm] += values[arm]["visible_code_correct"]
        baseline = values["envelope"]["total_input_tokens"]
        original = values["original"]["total_input_tokens"] - baseline
        compressed = values["compressed"]["total_input_tokens"] - baseline
        assert original > compressed >= 0
        per_case.append({"case": category, "original_incremental": original,
                         "compressed_incremental": compressed,
                         "reduction": 1 - compressed / original})
    raw = totals["original"] - totals["envelope"]
    packed = totals["compressed"] - totals["envelope"]
    return {
        "suite": SUITE, "synthetic_cases": len(by_case), "successful_receipts": len(rows),
        "input_tokens_including_envelope": totals, "original_incremental_input_tokens": raw,
        "compressed_incremental_input_tokens": packed,
        "incremental_reduction": 1 - packed / raw,
        "whole_request_input_reduction": 1 - totals["compressed"] / totals["original"],
        "target_reduction": TARGET_REDUCTION,
        "met_target": 1 - packed / raw >= TARGET_REDUCTION,
        "visible_code_correct": usable,
        "byte_exact_full_archive_recovery": sum(case["raw_sha256"] == case["archive_sha256"]
                                                 and case["archive_tail_recovered"] for case in audit),
        "tool_use_id_retained": sum(case["tool_use_id_retained"] for case in audit),
        "per_case": per_case,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coder-root", type=Path, default=Path.cwd())
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--direct", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        evidence = json.loads(args.out.read_text(encoding="utf-8"))
        summary = summarize(evidence["receipts"], evidence["archive_audit"])
        assert summary == evidence["summary"]
        print(json.dumps(summary, ensure_ascii=False))
        return
    suite = prepare_suite(load_compactor(args.coder_root.resolve()))
    audit = [{key: case[key] for key in ("category", "raw_sha256", "archive_sha256",
                                           "archive_tail_recovered", "tool_use_id_retained")}
             for case in suite]
    jobs = [(case, arm) for case in suite for arm in ("original", "compressed", "envelope")]
    random.Random(128_011).shuffle(jobs)
    key = configured_key(args.env_file)
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(lambda job: call(key, *job, args.direct), jobs))
    summary = summarize(rows, audit)
    evidence = {"summary": summary, "archive_audit": audit, "receipts": rows}
    args.out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
