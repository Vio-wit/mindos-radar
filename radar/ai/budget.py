"""预算与费用估算（L5-04）：发送前估算，发送后按真实 usage 记账。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

TOKENS_PER_CHAR = {
    "cjk": 0.75,   # 中文约 1 字 ≈ 0.75 token（保守）
    "latin": 0.28,  # 英文约 4 字符 ≈ 1 token
}


def estimate_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = max(0, len(text) - cjk)
    tokens = cjk * TOKENS_PER_CHAR["cjk"] + other * TOKENS_PER_CHAR["latin"]
    return max(1, int(tokens) + 1)


def estimate_cost_cny(prompt_tokens: int, completion_tokens: int, settings: dict[str, Any]) -> Decimal:
    ai = settings.get("ai") or {}
    price_in = Decimal(str(ai.get("price_input_cny_per_mtok") or "0"))
    price_out = Decimal(str(ai.get("price_output_cny_per_mtok") or "0"))
    cost = (Decimal(prompt_tokens) * price_in + Decimal(completion_tokens) * price_out) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def cost_of_usage(prompt_tokens: int, completion_tokens: int, settings: dict[str, Any]) -> Decimal:
    return estimate_cost_cny(prompt_tokens, completion_tokens, settings)


def today_spend_cny(conn) -> Decimal:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = Decimal("0")
    for row in conn.execute(
        "select cost_estimate from radar_ai_usage where substr(created_at,1,10)=? and reconciliation_status != 'failed'",
        (today,),
    ):
        try:
            total += Decimal(str(row["cost_estimate"] or "0"))
        except Exception:  # noqa: BLE001
            continue
    return total


def today_request_count(conn) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute(
        "select count(*) as n from radar_ai_usage where substr(created_at,1,10)=?", (today,)
    ).fetchone()
    return int(row["n"] or 0)


def budget_status(conn, settings: dict[str, Any], *, estimated_cost: Decimal = Decimal("0")) -> dict[str, Any]:
    ai = settings.get("ai") or {}
    limit = Decimal(str(ai.get("daily_budget_cny") or "0"))
    request_limit = int(ai.get("daily_request_limit") or 0)
    spent = today_spend_cny(conn)
    requests = today_request_count(conn)
    reasons = []
    if limit <= 0:
        reasons.append("未设置每日预算上限")
    elif spent + estimated_cost > limit:
        reasons.append(f"预计超出每日预算（已花 {spent} + 本次 {estimated_cost} > {limit} CNY）")
    if request_limit and requests >= request_limit:
        reasons.append(f"已达每日请求上限（{requests}/{request_limit}）")
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "spent_today_cny": str(spent),
        "estimated_cost_cny": str(estimated_cost),
        "daily_budget_cny": str(limit),
        "requests_today": requests,
        "daily_request_limit": request_limit,
        "price_checked_at": ai.get("price_checked_at"),
        "model": ai.get("model"),
    }
