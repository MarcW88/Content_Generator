"""
cost_tracker.py
───────────────
Calcul du coût réel à partir des tokens retournés par l'API Anthropic,
plus une estimation forfaitaire DataForSEO.

Tarifs USD (MTok = million tokens) :
  claude-sonnet-4-5 : $3.00 input / $15.00 output
  claude-opus-4-5   : $15.00 input / $75.00 output
  DataForSEO SERP   : ~$0.0025 / tâche
"""

from dataclasses import dataclass, field

# ── Pricing table (USD per million tokens) ────────────────────────────────────
PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5": {"input": 3.00,  "output": 15.00},
    "claude-opus-4-5":   {"input": 15.00, "output": 75.00},
}

DATAFORSEO_COST_PER_TASK = 0.0025   # USD — SERP + PAA + clustering ≈ 3 tasks


@dataclass
class PassCost:
    model: str
    input_tokens: int  = 0
    output_tokens: int = 0

    @property
    def usd(self) -> float:
        p = PRICING.get(self.model, {"input": 3.0, "output": 15.0})
        return (
            self.input_tokens  / 1_000_000 * p["input"] +
            self.output_tokens / 1_000_000 * p["output"]
        )


@dataclass
class RequestCost:
    passes: list[PassCost] = field(default_factory=list)
    dataforseo_tasks: int  = 3   # SERP + PAA + keyword clustering

    @property
    def llm_usd(self) -> float:
        return sum(p.usd for p in self.passes)

    @property
    def seo_usd(self) -> float:
        return self.dataforseo_tasks * DATAFORSEO_COST_PER_TASK

    @property
    def total_usd(self) -> float:
        return self.llm_usd + self.seo_usd

    @property
    def total_input_tokens(self) -> int:
        return sum(p.input_tokens for p in self.passes)

    @property
    def total_output_tokens(self) -> int:
        return sum(p.output_tokens for p in self.passes)

    def to_dict(self) -> dict:
        return {
            "total_usd":      round(self.total_usd, 5),
            "llm_usd":        round(self.llm_usd, 5),
            "seo_usd":        round(self.seo_usd, 5),
            "input_tokens":   self.total_input_tokens,
            "output_tokens":  self.total_output_tokens,
            "passes": [
                {
                    "model":         p.model,
                    "input_tokens":  p.input_tokens,
                    "output_tokens": p.output_tokens,
                    "usd":           round(p.usd, 5),
                }
                for p in self.passes
            ],
        }


# ── Estimation avant génération ───────────────────────────────────────────────
# Basée sur des moyennes observées (utile pour afficher avant de lancer)
ESTIMATE = {
    "tone_analyzer": PassCost("claude-opus-4-5",   input_tokens=12_000, output_tokens=400),
    "pass1":         PassCost("claude-sonnet-4-5", input_tokens=800,    output_tokens=200),
    "pass2":         PassCost("claude-sonnet-4-5", input_tokens=1_200,  output_tokens=500),
    "pass3":         PassCost("claude-sonnet-4-5", input_tokens=2_500,  output_tokens=2_000),
    "pass4":         PassCost("claude-sonnet-4-5", input_tokens=4_000,  output_tokens=2_500),
}


def estimate_request_cost(style_profile_cached: bool = True) -> RequestCost:
    """Rough pre-flight cost estimate shown in the UI before launching."""
    cost = RequestCost()
    if not style_profile_cached:
        cost.passes.append(ESTIMATE["tone_analyzer"])
    cost.passes += [
        ESTIMATE["pass1"],
        ESTIMATE["pass2"],
        ESTIMATE["pass3"],
        ESTIMATE["pass4"],
    ]
    return cost


def format_usd(amount: float) -> str:
    if amount < 0.01:
        return f"${amount * 100:.3f}¢"
    return f"${amount:.4f}"
