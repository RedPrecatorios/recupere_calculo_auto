from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import requests

from recupere_monday.http_proxy import prepare_session, request_with_proxy_rotation


@dataclass(frozen=True)
class SelicAcumulada:
    vencimento: str
    pagamento: str
    percentual: str
    fator: str
    fonte: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SicalcSelicClient:
    def __init__(self, *, timeout_seconds: int = 120) -> None:
        self._timeout_seconds = timeout_seconds
        self._session = prepare_session(requests.Session())

    def close(self) -> None:
        self._session.close()

    def consultar_acumulada(
        self,
        *,
        vencimento_mes: int,
        vencimento_ano: int,
        pagamento_mes: int | None = None,
        pagamento_ano: int | None = None,
    ) -> SelicAcumulada:
        today = date.today()
        pagamento_mes = pagamento_mes or today.month
        pagamento_ano = pagamento_ano or today.year
        vencimento = f"{vencimento_ano:04d}-{vencimento_mes:02d}-01"
        pagamento = f"{pagamento_ano:04d}-{pagamento_mes:02d}-01"
        url = (
            "https://sicalc.receita.fazenda.gov.br/sicalc/api/selic/acumulada/"
            f"{vencimento}/{pagamento}"
        )
        response = request_with_proxy_rotation(
            self._session,
            "GET",
            url,
            timeout=self._timeout_seconds,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://sicalc.receita.fazenda.gov.br/sicalc/selic/consulta",
            },
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            raise ValueError("Sicalc não retornou taxa Selic acumulada")

        percentual = _decimal(data[0]["valor"])
        return SelicAcumulada(
            vencimento=f"{vencimento_mes:02d}/{vencimento_ano:04d}",
            pagamento=f"{pagamento_mes:02d}/{pagamento_ano:04d}",
            percentual=_money(percentual),
            fator=_factor(percentual),
            fonte=url,
        )


def calcular_imposto_indevido_atualizado(
    *,
    imposto_retido: Decimal,
    irrf_calculado: Decimal,
    selic: SelicAcumulada,
) -> dict[str, Any]:
    imposto_indevido = _round_money(imposto_retido - irrf_calculado)
    fator_selic = _decimal(selic.fator)
    selic_acumulado = _round_money(imposto_indevido * fator_selic)
    total_atualizado = _round_money(imposto_indevido + selic_acumulado)
    return {
        "status": "calculado",
        "imposto_retido": _money(imposto_retido),
        "irrf_calculado": _money(irrf_calculado),
        "imposto_indevido": _money(imposto_indevido),
        "selic": selic.to_dict(),
        "selic_acumulado": _money(selic_acumulado),
        "imposto_retido_indevidamente_atualizado": _money(total_atualizado),
    }


def _decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    return Decimal(text)


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _money(value: Decimal) -> str:
    return f"{_round_money(value):.2f}"


def _factor(percentual: Decimal) -> str:
    return f"{(percentual / Decimal('100')).quantize(Decimal('0.000001'))}"
