"""Classify news headlines into model shock signals: keyword baseline and LLM classifier.

Labels per article: relevant (Y/N), shock_type (P/C/E/R/none), severity (low/medium/high/none),
affects_china (Y/N), as defined in news/labeling_guide.md (fixed 2026-11-21).

INTEGRITY GATE: no machine labels are written until hand_labels.csv has a complete label for every
article in news_articles.csv, so the author's labels cannot be influenced by machine output.
`--dry-run` prints the exact LLM prompt for one article and makes no API call and writes nothing.

Run (from the workspace root):
  D:/DAB/Thesis/venv/Scripts/python.exe -I worked_example/nlp_classify.py keyword
  D:/DAB/Thesis/venv/Scripts/python.exe -I worked_example/nlp_classify.py nvidia [--dry-run]
  D:/DAB/Thesis/venv/Scripts/python.exe -I worked_example/nlp_classify.py laya [--dry-run]
  D:/DAB/Thesis/venv/Scripts/python.exe -I worked_example/nlp_classify.py llm [--dry-run]     (Claude, optional)
Writes news/<method>_labels.csv (ai_labels.csv for Claude) and news/<method>_run_info.json.

nvidia: open-weight LLM (NVIDIA_MODEL) on the NVIDIA API catalog, OpenAI-compatible endpoint; labels come
back through a forced tool call whose parameters are LABEL_SCHEMA; temperature 0. The key is read at run
time from code/sql/.env (NVIDIA_API_KEY), held in memory only, never printed or copied.
laya: Laya open-weights decision model (Apache 2.0), run locally, no key; one "choice" question per field.
Rule fixed before any run: if Laya answers relevant = N, shock_type and severity are set to "none".

LLM provider: Anthropic (Claude), via the official `anthropic` SDK (pip install anthropic). Credentials
are resolved by the SDK from ANTHROPIC_API_KEY or an `ant auth login` profile; this script never reads
or prints a key. No sampling parameters are sent (current Claude models reject them); reproducibility
comes from a fixed prompt (its SHA-256 is recorded), the recorded model ID and the stored raw output.
"""
import csv
import hashlib
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
# --news=DIR points at another folder (used only to test the code on invented data)
NEWS = next((Path(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--news=")), HERE / "news")
MODEL = "claude-opus-5-5"
NVIDIA_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"   # open weights; Llama 3.3 retired on the API 2026-08-26
NVIDIA_EXTRA = {"chat_template_kwargs": {"enable_thinking": False}}   # answer directly; reasoning mode used up the token budget
NVIDIA_URL = "https://integrate.api.nvidia.com/v1"
ENV_FILE = Path("D:/DAB/Thesis/code/sql/.env")
EFFORT = "low"            # classification of one headline is a simple task

LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "relevant": {"type": "string", "enum": ["Y", "N"]},
        "shock_type": {"type": "string", "enum": ["P", "C", "E", "R", "none"]},
        "severity": {"type": "string", "enum": ["low", "medium", "high", "none"]},
        "affects_china": {"type": "string", "enum": ["Y", "N"]},
    },
    "required": ["relevant", "shock_type", "severity", "affects_china"],
    "additionalProperties": False,
}

# The rules below restate news/labeling_guide.md Sections 2 and 3 word for word where possible.
SYSTEM_PROMPT = """You label news headlines for a study of rare earth element (REE) supply risk for an importer in Japan.
Return one label for the headline, using only these definitions.

relevant: Y if the headline reports an event that could change supply, price, licensing or shipping of REEs for an importer; otherwise N.

shock_type (the main one if two apply; "none" if not relevant):
- P (price surge): a sharp rise in REE prices, an export tax, or speculative buying.
- C (capacity restriction): production or export quotas, mine or plant closures, processing limits, an export ban.
- E (export licensing): new or tighter export licences, approvals or controls that delay shipments without banning them.
- R (route disruption): shipping, port or transit disruption on routes to Japan, including chokepoints and sanctions affecting transport.

severity ("none" if not relevant):
- high: in force now or announced with a date, covers dysprosium, terbium or magnet inputs, or a large share of supply, and has no stated end date.
- medium: in force or announced, but limited in scope, product coverage or duration.
- low: proposed, threatened, rumoured, or a minor change.

affects_china: Y if the event concerns supply from China; otherwise N.

Judge from the headline, date and outlet only. Do not use knowledge of what happened later."""


def load_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def gate(articles):
    """Stop unless every article has a complete hand label."""
    labels = {r["article_id"]: r for r in load_csv(NEWS / "hand_labels.csv")}
    missing = [a["article_id"] for a in articles
               if a["article_id"] not in labels
               or not all(labels[a["article_id"]].get(k, "").strip()
                          for k in ("relevant", "shock_type", "severity", "affects_china", "labeled_by"))]
    if missing:
        sys.exit(f"Gate closed: {len(missing)} of {len(articles)} articles have no complete hand label "
                 f"(first: {', '.join(missing[:5])}). Finish hand_labels.csv first.")


# ------------------------------------------------------------------ keyword baseline (rules fixed 2026-11-22)
REE = r"rare[- ]earth|\bree\b|\brees\b|neodymium|dysprosium|terbium|ndpr|praseodymium|magnet|lynas|molycorp|yttrium|samarium"
ROUTE = r"shipping|\bport\b|ports\b|red sea|houthi|suez|freight|vessel|container|chokepoint|lockdown"
CHINA = r"china|chinese|beijing"
TYPE_RULES = [  # first match wins: licensing before capacity, so "export controls" is E, "export ban" is C
    ("E", r"licen[cs]e|export control|controls on|approval|permit|curbs"),
    ("C", r"quota|\bban\b|bans\b|halt|closure|close|crackdown|suspend|block|cut|restrict|consolidat|tighten"),
    ("P", r"price|prices|surge|soar|jump|double|skyrocket|record high|decade-high|tax"),
    ("R", ROUTE),
]
LOW = r"\bmay\b|could|might|threat|consider|plan\b|plans\b|propose|warn|weigh|\?|rumou?r|signal|next element"
HIGH = r"\bban\b|bans\b|halt|block|stop|skyrocket|soar|double|record|expands|seven|dysprosium|terbium|magnet"


def keyword_label(headline):
    h = headline.lower()
    ree, route = re.search(REE, h), re.search(ROUTE, h)
    if not (ree or route):
        return {"relevant": "N", "shock_type": "none", "severity": "none", "affects_china": "N"}
    t = next((t for t, rx in TYPE_RULES if re.search(rx, h)), None)
    if t is None:
        return {"relevant": "N", "shock_type": "none", "severity": "none",
                "affects_china": "Y" if re.search(CHINA, h) else "N"}
    sev = "low" if re.search(LOW, h) else "high" if re.search(HIGH, h) else "medium"
    return {"relevant": "Y", "shock_type": t, "severity": sev, "affects_china": "Y" if re.search(CHINA, h) else "N"}


# ------------------------------------------------------------------ LLM classifier
def user_message(a):
    return f"Date: {a['date']}\nOutlet: {a['outlet']}\nHeadline: {a['headline']}"


def llm_label(client, a):
    import anthropic
    for attempt in range(3):
        try:
            resp = client.beta.messages.create(
                model=MODEL,
                max_tokens=1024,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SYSTEM_PROMPT,
                output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": LABEL_SCHEMA}},
                messages=[{"role": "user", "content": user_message(a)}],
            )
            break
        except anthropic.RateLimitError:
            time.sleep(20 * (attempt + 1))
    else:
        raise RuntimeError(f"rate limited three times on {a['article_id']}")
    answered_by = resp.model
    if resp.stop_reason == "refusal":
        return {"relevant": "", "shock_type": "", "severity": "", "affects_china": "",
                "status": f"refusal:{getattr(resp.stop_details, 'category', None)}", "answered_by": answered_by,
                "response_id": resp.id, "raw": ""}
    text = next(b.text for b in resp.content if b.type == "text")
    lab = json.loads(text)
    lab.update({"status": resp.stop_reason, "answered_by": answered_by, "response_id": resp.id, "raw": text})
    return lab


# ------------------------------------------------------------------ NVIDIA API (open-weight LLM)
def check_label(lab):
    """Return the label if every field is in its enum, else None."""
    ok = all(lab.get(k) in LABEL_SCHEMA["properties"][k]["enum"] for k in LABEL_SCHEMA["required"])
    return {k: lab[k] for k in LABEL_SCHEMA["required"]} if ok else None


def nvidia_label(client, a):
    import openai
    tool = {"type": "function", "function": {"name": "record_label", "description": "Record the label for the headline.",
                                             "parameters": LABEL_SCHEMA}}
    for attempt in range(4):
        try:
            resp = client.chat.completions.create(
                model=NVIDIA_MODEL, temperature=0, max_tokens=1024, extra_body=NVIDIA_EXTRA,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_message(a)}],
                tools=[tool], tool_choice={"type": "function", "function": {"name": "record_label"}})
            break
        except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError):
            time.sleep(15 * (attempt + 1))
    else:
        raise RuntimeError(f"NVIDIA API failed four times on {a['article_id']}")
    msg = resp.choices[0].message
    raw = msg.tool_calls[0].function.arguments if msg.tool_calls else (msg.content or "")
    try:
        lab = check_label(json.loads(raw))
    except json.JSONDecodeError:
        lab = None
    empty = {k: "" for k in LABEL_SCHEMA["required"]}
    return {**(lab or empty), "status": "ok" if lab else "invalid_output", "answered_by": resp.model,
            "response_id": resp.id, "raw": raw}


# ------------------------------------------------------------------ Laya (local, open weights)
LAYA_QUESTIONS = {
    "relevant": {"type": "choice", "instructions": "Does the headline report an event that could change supply, price, licensing or shipping of rare earth elements for an importer?",
                 "criteria": {"Y": "yes, it could change REE supply, price, licensing or shipping", "N": "no"}},
    "shock_type": {"type": "choice", "instructions": "Which kind of supply shock does the headline report? Choose the main one.",
                   "criteria": {"P": "price surge: a sharp rise in REE prices, an export tax, or speculative buying",
                                "C": "capacity restriction: production or export quotas, mine or plant closures, processing limits, an export ban",
                                "E": "export licensing: new or tighter export licences, approvals or controls that delay shipments without banning them",
                                "R": "route disruption: shipping, port or transit disruption on routes to Japan, including chokepoints and sanctions",
                                "none": "no supply shock"}},
    "severity": {"type": "choice", "instructions": "How severe is the reported event?",
                 "criteria": {"high": "in force now or announced with a date, covers dysprosium, terbium or magnet inputs, or a large share of supply, and has no stated end date",
                              "medium": "in force or announced, but limited in scope, product coverage or duration",
                              "low": "proposed, threatened, rumoured, or a minor change",
                              "none": "no supply shock"}},
    "affects_china": {"type": "choice", "instructions": "Does the event concern supply from China?",
                      "criteria": {"Y": "yes, it concerns supply from China", "N": "no"}},
}


def laya_label(router, a):
    res = router.predict(user_message(a), LAYA_QUESTIONS, model="english")
    ans = res["answers"]
    lab = {k: ans[k]["choice"] for k in LAYA_QUESTIONS}
    if lab["relevant"] == "N":                      # fixed consistency rule (see module docstring)
        lab["shock_type"] = lab["severity"] = "none"
    conf = {k: round(float(ans[k].get("confidence", ans[k].get("answer_confidence", float("nan")))), 4)
            for k in LAYA_QUESTIONS}
    return {**lab, "status": "ok", "answered_by": res.get("routing", {}).get("model", "laya"), "response_id": "",
            "raw": json.dumps({"choices": {k: ans[k]["choice"] for k in LAYA_QUESTIONS}, "confidence": conf})}


def run_model(method, articles, fields, prompt_sha):
    """Label every article with the NVIDIA-hosted LLM or local Laya; resumable."""
    if method == "nvidia":
        import os
        import openai
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE)                       # key held in memory only; never printed or copied
        if not os.environ.get("NVIDIA_API_KEY"):
            sys.exit("NVIDIA_API_KEY is not set in code/sql/.env.")
        client = openai.OpenAI(base_url=NVIDIA_URL, api_key=os.environ["NVIDIA_API_KEY"])
        label, version, model = (lambda a: nvidia_label(client, a)), openai.__version__, NVIDIA_MODEL
    else:
        import laya
        router = laya.Router(default="english")
        label, version, model = (lambda a: laya_label(router, a)), laya.__version__, "laya english (convaiinnovations/laya)"
    out = NEWS / f"{method}_labels.csv"
    done = {r["article_id"] for r in load_csv(out)} if out.exists() else set()
    new_file = not out.exists()
    with open(out, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields + ["status", "answered_by", "response_id", "raw"])
        if new_file:
            w.writeheader()
        for a in articles:
            if a["article_id"] in done:
                continue
            w.writerow({"article_id": a["article_id"], **label(a)})
            f.flush()
            print(a["article_id"], flush=True)
    (NEWS / f"{method}_run_info.json").write_text(json.dumps({
        "method": method, "model": model, "prompt_sha256": prompt_sha, "package_version": version,
        "temperature": 0 if method == "nvidia" else None, "extra_body": NVIDIA_EXTRA if method == "nvidia" else None, "run_date": date.today().isoformat(),
        "n_articles": len(articles)}, indent=2), encoding="utf-8")
    print(f"wrote {out}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("keyword", "llm", "nvidia", "laya"):
        sys.exit(__doc__)
    method, dry = sys.argv[1], "--dry-run" in sys.argv
    articles = load_csv(NEWS / "news_articles.csv")
    if not articles:
        sys.exit("news_articles.csv is empty.")
    prompt_sha = hashlib.sha256((SYSTEM_PROMPT + json.dumps(LABEL_SCHEMA, sort_keys=True)).encode()).hexdigest()

    if method == "laya":
        prompt_sha = hashlib.sha256(json.dumps(LAYA_QUESTIONS, sort_keys=True).encode()).hexdigest()
    if dry:
        if method == "laya":
            print(f"laya (local, english checkpoint) questions_sha256={prompt_sha}\n--- state (first article) ---\n"
                  f"{user_message(articles[0])}\n--- questions ---\n{json.dumps(LAYA_QUESTIONS, indent=1)}")
        else:
            model = NVIDIA_MODEL if method == "nvidia" else MODEL
            print(f"method={method} model={model} prompt_sha256={prompt_sha}\n--- system ---\n{SYSTEM_PROMPT}\n"
                  f"--- user (first article) ---\n{user_message(articles[0])}\n--- schema ---\n{json.dumps(LABEL_SCHEMA, indent=1)}")
        return
    gate(articles)
    fields = ["article_id", "relevant", "shock_type", "severity", "affects_china"]
    if method == "keyword":
        out = NEWS / "keyword_labels.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for a in articles:
                w.writerow({"article_id": a["article_id"], **keyword_label(a["headline"])})
        print(f"wrote {out} ({len(articles)} rows)")
        return

    if method in ("nvidia", "laya"):
        run_model(method, articles, fields, prompt_sha)
        return
    import anthropic
    client = anthropic.Anthropic()
    out = NEWS / "ai_labels.csv"
    done = {r["article_id"] for r in load_csv(out)} if out.exists() else set()
    new_file = not out.exists()
    with open(out, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields + ["status", "answered_by", "response_id", "raw"])
        if new_file:
            w.writeheader()
        for a in articles:
            if a["article_id"] in done:
                continue
            w.writerow({"article_id": a["article_id"], **llm_label(client, a)})
            f.flush()
            print(a["article_id"], flush=True)
    (NEWS / "llm_run_info.json").write_text(json.dumps({
        "provider": "anthropic", "requested_model": MODEL, "effort": EFFORT, "prompt_sha256": prompt_sha,
        "fallbacks": "default (server-side; answered_by column records the model that answered)",
        "run_date": date.today().isoformat(), "sdk_version": anthropic.__version__,
        "n_articles": len(articles)}, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
