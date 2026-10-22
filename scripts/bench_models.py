"""模型选型对比：同一批题、同一套 prompt，比速度和执行正确率。

用法：
    uv run python scripts/bench_models.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from openai import OpenAI  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.executor import execute_sql  # noqa: E402
from app.services.nl2sql import (  # noqa: E402
    SYSTEM_PROMPT,
    build_user_prompt,
    clean_sql,
    render_schema_text,
)
from eval import load_questions, results_equal  # noqa: E402

# (模型名, extra_body) —— None 表示不传额外参数
MODELS = [
    ("qwen3.8-max", None),
    ("qwen3.8-max", {"enable_thinking": False}),
    ("qwen3.8-flash", None),
    ("qwen3.7-max", None),
    ("qwen3-coder-flash", None),
    ("qwen-max", None),
]

CASE_IDS = [1, 9, 21, 25]
SLEEP = 1.0


def label(model: str, extra: dict | None) -> str:
    if extra and extra.get("enable_thinking") is False:
        return f"{model}(无思考)"
    return model


def main() -> None:
    settings = get_settings()
    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=120)

    questions = {q["id"]: q for q in load_questions()}
    cases = [questions[i] for i in CASE_IDS]
    schema_text = render_schema_text()
    print(f"schema 文本长度 = {len(schema_text)} 字符 | 题目 = {CASE_IDS}\n")

    gold: dict[int, list[tuple]] = {}
    for q in cases:
        _, rows = execute_sql(q["gold_sql"])
        gold[q["id"]] = rows

    summary = []
    for model, extra in MODELS:
        name = label(model, extra)
        lats, tokens, correct, errors = [], [], 0, 0
        print(f"--- {name} ---")
        for q in cases:
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_prompt(q["question"], schema_text)},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.0,
                "max_tokens": 1500,
            }
            if extra:
                kwargs["extra_body"] = extra
            t0 = time.perf_counter()
            try:
                resp = client.chat.completions.create(**kwargs)
                dt = (time.perf_counter() - t0) * 1000
                content = resp.choices[0].message.content or ""
                usage = resp.usage
                data = json.loads(content) if content.strip().startswith("{") else {}
                sql = clean_sql(str(data.get("sql") or ""))
                if not sql:
                    status = "无SQL"
                else:
                    try:
                        _, pred = execute_sql(sql)
                        status = "对" if results_equal(gold[q["id"]], pred) else "错"
                    except Exception as exc:  # noqa: BLE001
                        status = f"执行失败:{type(exc).__name__}"
                lats.append(dt)
                tokens.append(usage.completion_tokens if usage else 0)
                if status == "对":
                    correct += 1
                print(f"  #{q['id']:<3} {status:<12} {dt/1000:5.1f}s  out={usage.completion_tokens if usage else '?'}")
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print(f"  #{q['id']:<3} 调用失败 {type(exc).__name__}: {str(exc)[:60]}")
            time.sleep(SLEEP)

        if lats:
            avg = sum(lats) / len(lats)
            summary.append(
                {
                    "model": name,
                    "correct": f"{correct}/{len(cases)}",
                    "avg_latency_s": round(avg / 1000, 1),
                    "max_latency_s": round(max(lats) / 1000, 1),
                    "avg_out_tokens": round(sum(tokens) / len(tokens)),
                    "errors": errors,
                }
            )
        print()

    print("=" * 78)
    print(f"{'模型':<26}{'正确':<8}{'平均耗时':<10}{'最慢':<8}{'平均输出token':<14}{'失败'}")
    for s in summary:
        print(
            f"{s['model']:<26}{s['correct']:<8}{str(s['avg_latency_s'])+'s':<10}"
            f"{str(s['max_latency_s'])+'s':<8}{s['avg_out_tokens']:<14}{s['errors']}"
        )


if __name__ == "__main__":
    main()