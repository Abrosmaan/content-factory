You are a fact-checker. The script below was written by an LLM. Some of its
factual claims may be hallucinated, outdated, or imprecise.

Find anything that is:
* a specific date, version number, statistic, or quantity;
* a comparative claim ("X is the fastest", "first to do Y");
* a player count, sales figure, benchmark number;
* a quote attributed to a person;
* a hardware spec (CPU model, GPU model, refresh rate, response time).

For each such claim, decide if it is:
* `verified` — broadly accepted public information,
* `uncertain` — plausible but I can't confirm without web search,
* `wrong` — contradicts known facts as of the cutoff.

# Output

Return **only** a JSON object, no prose:

```
{{
  "facts_checked": true,
  "issues": [
    {{
      "section": "id-from-script",
      "claim": "the exact sentence containing the claim",
      "severity": "low | medium | high",
      "verdict": "verified | uncertain | wrong",
      "note": "one-sentence explanation"
    }}
  ]
}}
```

`severity = high` only when a `wrong` verdict would embarrass the channel
(misleading hardware spec, misattributed quote, wrong release year of a
flagship title).

`issues` may be empty — that means the script is clean.

# Script to check

```json
{script_json}
```
