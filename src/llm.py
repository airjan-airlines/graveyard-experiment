"""Provider wrapper (Anthropic / OpenAI / fake) for the analysis steps, with a disk cache keyed by
input hash so re-running analysis is free and deterministic, plus a global budget guard."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import random
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from src import db
from src.config import cfg, est_cost, model_cfg, path

log = logging.getLogger(__name__)


class BudgetExceeded(RuntimeError):
    pass


class LLMRefusal(RuntimeError):
    pass


def check_budget() -> None:
    limit = float(cfg()["budget"]["max_budget_usd"])
    spent = db.total_spend_usd()
    if spent >= limit:
        raise BudgetExceeded(f"Spent ${spent:.2f} >= max_budget_usd ${limit:.2f}; aborting.")


# --------------------------------------------------------------------------- images


def image_jpeg_b64(p: str | Path, max_width: int = 800) -> str:
    im = Image.open(p).convert("RGB")
    if im.width > max_width:
        im = im.resize((max_width, int(im.height * max_width / im.width)))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=70)
    return base64.standard_b64encode(buf.getvalue()).decode()


def _file_sha(p: str | Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# --------------------------------------------------------------------------- JSON completion


def complete_json(
    role: str,
    purpose: str,
    system: str,
    text: str,
    schema: dict[str, Any],
    images: list[str | Path] | None = None,
    max_tokens: int = 16000,
) -> dict[str, Any]:
    """One structured-output call. Returns parsed JSON matching `schema`. Cached by input hash."""
    mc = model_cfg(role)
    images = [p for p in (images or []) if p and Path(p).exists()]
    key_material = {
        "provider": mc["provider"],
        "model": mc["name"],
        "effort": mc.get("effort"),
        "system": system,
        "text": text,
        "images": [_file_sha(p) for p in images],
        "schema": schema,
    }
    key = hashlib.sha256(json.dumps(key_material, sort_keys=True).encode()).hexdigest()
    cache_file = path("cache") / f"{key}.json"
    if cache_file.exists():
        hit = json.loads(cache_file.read_text())
        db.upsert("llm_calls", {"purpose": purpose, "model": mc["name"], "input_tokens": 0, "output_tokens": 0,
                                "est_cost_usd": 0.0, "cached": 1})
        return hit["output"]

    check_budget()
    if mc["provider"] == "anthropic":
        out, usage = _anthropic(mc, system, text, schema, images, max_tokens)
    elif mc["provider"] == "openai":
        out, usage = _openai(mc, system, text, schema, images, max_tokens)
    elif mc["provider"] == "fake":
        out, usage = _fake(schema, key), (0, 0)
    else:
        raise ValueError(f"unknown provider {mc['provider']}")

    cost = est_cost(mc["name"], *usage)
    db.upsert("llm_calls", {"purpose": purpose, "model": mc["name"], "input_tokens": usage[0],
                            "output_tokens": usage[1], "est_cost_usd": cost, "cached": 0})
    cache_file.write_text(json.dumps({"key": key_material | {"images": key_material["images"]}, "output": out,
                                      "usage": usage}, indent=1))
    return out


_anthropic_client = None
_openai_client = None


def _anthropic(mc, system, text, schema, images, max_tokens):
    import anthropic

    global _anthropic_client
    _anthropic_client = _anthropic_client or anthropic.Anthropic(max_retries=5)
    content: list[dict[str, Any]] = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_jpeg_b64(p)}}
        for p in images
    ]
    content.append({"type": "text", "text": text})
    output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
    if mc.get("effort"):
        output_config["effort"] = mc["effort"]
    resp = _anthropic_client.messages.create(
        model=mc["name"],
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config=output_config,
    )
    if resp.stop_reason == "refusal":
        raise LLMRefusal(f"refusal: {getattr(resp, 'stop_details', None)}")
    txt = next(b.text for b in resp.content if b.type == "text")
    return json.loads(txt), (resp.usage.input_tokens, resp.usage.output_tokens)


def _openai(mc, system, text, schema, images, max_tokens):
    import openai

    global _openai_client
    _openai_client = _openai_client or openai.OpenAI(max_retries=5)
    content: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_jpeg_b64(p)}"}} for p in images
    ]
    content.append({"type": "text", "text": text})
    resp = _openai_client.chat.completions.create(
        model=mc["name"],
        messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
        response_format={"type": "json_schema", "json_schema": {"name": "output", "schema": schema, "strict": True}},
        max_completion_tokens=max_tokens,
    )
    return json.loads(resp.choices[0].message.content), (resp.usage.prompt_tokens, resp.usage.completion_tokens)


def _fake(schema: dict[str, Any], key: str) -> Any:
    """Deterministic schema-valid output for offline pipeline tests. Produces SYNTHETIC data only."""
    rng = random.Random(key)

    def gen(s: dict[str, Any], name: str = "") -> Any:
        if "anyOf" in s:
            non_null = [x for x in s["anyOf"] if x.get("type") != "null"]
            return gen(non_null[0], name) if non_null and rng.random() < 0.85 else None
        if "enum" in s:
            return rng.choice(s["enum"])
        t = s.get("type")
        if t == "object":
            return {k: gen(v, k) for k, v in s["properties"].items()}
        if t == "array":
            return [gen(s["items"], name) for _ in range(rng.randint(0, 2))]
        if t == "boolean":
            return rng.random() < 0.4
        if t == "integer":
            return rng.randint(0, 10)
        if t == "number":
            return rng.random()
        return f"[fake {name}] " + rng.choice(
            ["Click the calendar day instead of typing.", "Use the site search box, then the facet filters.",
             "Select the suggestion from the autocomplete list.", "Dismiss the cookie banner first.",
             "The answer lives on the fees page under Plan Your Visit."])

    return gen(schema)


# --------------------------------------------------------------------------- embeddings

_st_model = None


def embed(texts: list[str]) -> np.ndarray:
    """L2-normalised embeddings (rows). Provider from config.embeddings."""
    ec = cfg()["embeddings"]
    if not texts:
        return np.zeros((0, 1))
    if ec["provider"] == "openai":
        import openai

        resp = openai.OpenAI().embeddings.create(model=ec.get("name", "text-embedding-3-small"), input=texts)
        m = np.array([d.embedding for d in resp.data])
    elif ec["provider"] == "local":
        global _st_model
        from sentence_transformers import SentenceTransformer

        _st_model = _st_model or SentenceTransformer(ec.get("name", "all-MiniLM-L6-v2"))
        m = np.asarray(_st_model.encode(texts, show_progress_bar=False))
    else:  # tfidf: dependency-free fallback, lexical not semantic
        from sklearn.feature_extraction.text import TfidfVectorizer

        m = TfidfVectorizer().fit_transform(texts).toarray()
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(norms == 0, 1, norms)


def nullable(t: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [t, {"type": "null"}]}


def obj(**props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def pmap(fn, items: list, workers: int = 4) -> list:
    """Run blocking LLM calls concurrently, preserving order."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))
