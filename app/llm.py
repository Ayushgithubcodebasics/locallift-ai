"""LLM layer.

* complete(): LiteLLM router with automatic fallback across models
  (Gemini -> Gemini lite -> OpenRouter free -> Groq -> OpenJarvis). If the litellm package is not
  importable it falls back to plain OpenAI-compatible HTTP calls, so the agency never stops.
* council(): port of karpathy/llm-council: 3 models answer -> anonymous peer ranking -> chairman
  writes the final, best version. Used for everything a client sees.
"""
import json
import os
import re
import time

import requests

from . import config

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

_litellm = None
if config.USE_LITELLM:
    try:
        import litellm as _litellm  # noqa
        _litellm.drop_params = True
        _litellm.suppress_debug_info = True
    except Exception:  # pragma: no cover
        _litellm = None

STATS = {"calls": 0, "errors": 0, "by_model": {}}


class LLMError(Exception):
    pass


def _provider(model: str):
    prov, _, name = model.partition("/")
    if prov == "gemini":
        return config.GEMINI_API_KEY, "https://generativelanguage.googleapis.com/v1beta/openai", name
    if prov == "openrouter":
        return config.OPENROUTER_API_KEY, "https://openrouter.ai/api/v1", name
    if prov == "groq":
        return config.GROQ_API_KEY, "https://api.groq.com/openai/v1", name
    if prov == "jarvis":
        return ("local" if config.JARVIS_BASE_URL else ""), config.JARVIS_BASE_URL.rstrip("/"), name
    return "", "", name


def available(model: str) -> bool:
    return bool(_provider(model)[0])


def _call(model, messages, temperature, max_tokens):
    key, base, name = _provider(model)
    if _litellm is not None:
        kwargs = dict(messages=messages, temperature=temperature, max_tokens=max_tokens, timeout=90, num_retries=0)
        if model.startswith("jarvis/"):
            kwargs.update(model=f"openai/{name}", api_base=base, api_key="local")
        else:
            kwargs.update(model=model, api_key=key)
        r = _litellm.completion(**kwargs)
        return r.choices[0].message.content or ""
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if model.startswith("openrouter/"):
        headers.update({"HTTP-Referer": config.PUBLIC_BASE_URL, "X-Title": config.AGENCY_NAME})
    r = requests.post(f"{base}/chat/completions", headers=headers, timeout=90, json={
        "model": name, "messages": messages, "temperature": temperature, "max_tokens": max_tokens})
    if r.status_code >= 400:
        raise LLMError(f"{model} HTTP {r.status_code}: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"] or ""


def complete(messages, models=None, temperature=0.6, max_tokens=1200):
    """Returns (text, model_used). Tries each model in order; raises LLMError if all fail."""
    chain = [m for m in (models or config.LLM_MODELS) if available(m)]
    if not chain:
        raise LLMError("No LLM API key configured (set GEMINI_API_KEY or OPENROUTER_API_KEY).")
    errors = []
    for m in chain:
        for attempt in range(2):
            try:
                STATS["calls"] += 1
                text = _call(m, messages, temperature, max_tokens).strip()
                if not text:
                    raise LLMError("empty response")
                STATS["by_model"][m] = STATS["by_model"].get(m, 0) + 1
                return text, m
            except Exception as e:  # noqa
                STATS["errors"] += 1
                msg = str(e)
                errors.append(f"{m}: {msg[:160]}")
                if attempt == 0 and ("429" in msg or "rate" in msg.lower() or "quota" in msg.lower()):
                    time.sleep(6)
                    continue
                break
    raise LLMError("All models failed -> " + " | ".join(errors[-4:]))


def ask(system, user, **kw):
    return complete([{"role": "system", "content": system}, {"role": "user", "content": user}], **kw)[0]


def extract_json(text):
    """Pull the first JSON object/array out of an LLM reply."""
    if text is None:
        raise ValueError("no text")
    t = re.sub(r"```(?:json)?", "", text).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        if start == -1:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(t)):
            c = t[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except Exception:
                        break
    raise ValueError("no JSON found in model output")


def ask_json(system, user, **kw):
    text = ask(system, user + "\n\nReply with valid JSON only, no commentary.", temperature=0.3, **kw)
    return extract_json(text)


# ---------------- LLM Council (karpathy/llm-council) ----------------
def council(question: str, system: str = "", json_output: bool = False, max_tokens=1200):
    """Stage 1: each council model answers. Stage 2: each ranks the anonymised answers.
    Stage 3: chairman synthesises the final best version. Returns dict with all stages."""
    members = [m for m in config.COUNCIL_MODELS if available(m)]
    if not members:
        raise LLMError("No council model has an API key configured.")
    sys_msg = [{"role": "system", "content": system}] if system else []
    fmt_note = "\n\nReturn ONLY valid JSON." if json_output else ""

    stage1 = []
    for m in members:
        try:
            text, used = complete(sys_msg + [{"role": "user", "content": question + fmt_note}], models=[m],
                                  max_tokens=max_tokens)
            stage1.append({"model": used, "text": text})
        except Exception as e:  # noqa
            stage1.append({"model": m, "text": "", "error": str(e)[:200]})
    answers = [s for s in stage1 if s.get("text")]
    if not answers:
        # whole council down -> one last try with the default chain
        text, used = complete(sys_msg + [{"role": "user", "content": question + fmt_note}], max_tokens=max_tokens)
        return {"final": text, "chairman": used, "stage1": stage1, "stage2": [], "aggregate": [], "mode": "fallback"}

    labels = [chr(65 + i) for i in range(len(answers))]
    stage2, scores = [], {l: [] for l in labels}
    if len(answers) >= 2:
        block = "\n\n".join(f"Response {l}:\n{a['text']}" for l, a in zip(labels, answers))
        rank_prompt = (f"Question:\n{question}\n\nHere are anonymous responses.\n\n{block}\n\n"
                       f"Evaluate each briefly for accuracy, usefulness and quality, then end with a line exactly like:\n"
                       f"FINAL RANKING: {', '.join(labels)}\n(best first)")
        for a in answers:
            try:
                text, used = complete([{"role": "user", "content": rank_prompt}], models=[a["model"]], max_tokens=700,
                                      temperature=0.2)
                m = re.search(r"FINAL RANKING:\s*([A-Z ,>\-]+)", text)
                order = [x for x in re.findall(r"[A-Z]", m.group(1)) if x in labels] if m else []
                for pos, l in enumerate(dict.fromkeys(order)):
                    scores[l].append(pos + 1)
                stage2.append({"model": used, "ranking": order, "text": text[-600:]})
            except Exception as e:  # noqa
                stage2.append({"model": a["model"], "ranking": [], "error": str(e)[:200]})
    aggregate = sorted(
        [{"label": l, "model": a["model"], "avg_rank": (sum(scores[l]) / len(scores[l])) if scores[l] else 99}
         for l, a in zip(labels, answers)], key=lambda x: x["avg_rank"])
    best = answers[labels.index(aggregate[0]["label"])]["text"]

    try:
        chair_prompt = (f"You are the Chairman of an LLM council. Question:\n{question}\n\n"
                        + "\n\n".join(f"Response {l} (avg peer rank {next(x['avg_rank'] for x in aggregate if x['label']==l):.1f}):\n{a['text']}"
                                      for l, a in zip(labels, answers))
                        + "\n\nWrite the single best final answer, combining the strongest points. "
                          "Output only the final answer itself." + fmt_note)
        final, used = complete(sys_msg + [{"role": "user", "content": chair_prompt}],
                               models=[config.CHAIRMAN_MODEL] + config.LLM_MODELS, max_tokens=max_tokens)
    except Exception:
        final, used = best, aggregate[0]["model"] + " (top-ranked, chairman unavailable)"
    return {"final": final, "chairman": used, "stage1": stage1, "stage2": stage2, "aggregate": aggregate,
            "mode": "council"}


def health():
    out = {}
    for m in dict.fromkeys(config.LLM_MODELS + config.COUNCIL_MODELS + [config.CHAIRMAN_MODEL]):
        if not available(m):
            out[m] = "no API key"
            continue
        try:
            t, _ = complete([{"role": "user", "content": "Reply with the single word OK."}], models=[m], max_tokens=5)
            out[m] = "OK: " + t[:20]
        except Exception as e:  # noqa
            out[m] = "FAIL: " + str(e)[:150]
    out["_router"] = "litellm" if _litellm is not None else "direct-http"
    return out
