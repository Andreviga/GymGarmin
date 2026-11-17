"""Relógio do app no fuso do usuário.

A Vercel roda em UTC: depois das 21h no Brasil, `date.today()` do servidor já é
amanhã — o treino de hoje viraria "perdido" e a prontidão cairia no dia errado.
Todo "hoje/agora" do app passa por aqui (fuso em APP_TZ, padrão America/Sao_Paulo).
`freeze()` fixa o relógio (testes e simulações).
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo(os.environ.get("APP_TZ") or "America/Sao_Paulo")
except Exception:  # noqa: BLE001 - sem base de fusos: Brasília não tem horário de verão desde 2019
    TZ = timezone(timedelta(hours=-3))

_frozen: datetime | None = None


def now() -> datetime:
    """Agora (hora local, sem tzinfo)."""
    if _frozen is not None:
        return _frozen
    return datetime.now(TZ).replace(tzinfo=None)


def today() -> date:
    return now().date()


def freeze(dt: datetime | None) -> None:
    global _frozen
    _frozen = dt
