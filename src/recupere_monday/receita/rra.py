from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any

import requests

from recupere_monday.http_proxy import prepare_session, request_with_proxy_rotation


@dataclass(frozen=True)
class RRAInput:
    ano_calendario: int
    mes_recebimento: int
    numero_meses: Decimal
    rendimentos_recebidos: Decimal
    juros: Decimal
    previdencia_oficial: Decimal = Decimal("0")
    pensao_alimenticia: Decimal = Decimal("0")
    despesas_acoes_judiciais: Decimal = Decimal("0")


@dataclass(frozen=True)
class RRAResult:
    ano_calendario: int
    mes_recebimento: int
    numero_meses: str
    rendimentos_recebidos: str
    juros: str
    previdencia_oficial: str
    pensao_alimenticia: str
    despesas_acoes_judiciais: str
    rendimentos_tributaveis: str
    total_deducoes: str
    base_calculo: str
    irrf: str
    aliquota_aplicada_percentual: str
    parcela_deducao: str
    fonte_tabela: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReceitaRRAClient:
    def __init__(self, *, timeout_seconds: int = 120) -> None:
        self._timeout_seconds = timeout_seconds
        self._session = prepare_session(requests.Session())

    def close(self) -> None:
        self._session.close()

    def calcular(self, data: RRAInput) -> RRAResult:
        tabela = self._get_tabela(data.ano_calendario)
        faixas = tabela["gpFaixasTblProg"]
        mes_index = data.mes_recebimento - 1

        limites = [
            _truncate(_decimal(faixa["vaAjLimiteMe"][mes_index]) * data.numero_meses, 2)
            for faixa in faixas[:4]
        ]
        parcelas = [
            None,
            *[
                _truncate(
                    _decimal(faixas[index]["vaAjDescontoRra"][mes_index])
                    * data.numero_meses,
                    5,
                )
                for index in range(1, 5)
            ],
        ]
        aliquotas = [
            _decimal(faixas[index]["peFaixa"]) / Decimal("100")
            for index in range(1, 5)
        ]

        rendimentos_tributaveis = data.rendimentos_recebidos - data.juros
        total_deducoes = (
            data.previdencia_oficial
            + data.pensao_alimenticia
            + data.despesas_acoes_judiciais
        )
        base_calculo = data.rendimentos_recebidos - data.juros - total_deducoes

        if base_calculo <= limites[0]:
            irrf = Decimal("0")
            aliquota = Decimal("0")
            parcela = Decimal("0")
        elif base_calculo <= limites[1]:
            aliquota = aliquotas[0]
            parcela = parcelas[1]
            irrf = base_calculo * aliquota - parcela
        elif base_calculo <= limites[2]:
            aliquota = aliquotas[1]
            parcela = parcelas[2]
            irrf = base_calculo * aliquota - parcela
        elif base_calculo <= limites[3]:
            aliquota = aliquotas[2]
            parcela = parcelas[3]
            irrf = base_calculo * aliquota - parcela
        else:
            aliquota = aliquotas[3]
            parcela = parcelas[4]
            irrf = base_calculo * aliquota - parcela

        return RRAResult(
            ano_calendario=data.ano_calendario,
            mes_recebimento=data.mes_recebimento,
            numero_meses=_money(data.numero_meses),
            rendimentos_recebidos=_money(data.rendimentos_recebidos),
            juros=_money(data.juros),
            previdencia_oficial=_money(data.previdencia_oficial),
            pensao_alimenticia=_money(data.pensao_alimenticia),
            despesas_acoes_judiciais=_money(data.despesas_acoes_judiciais),
            rendimentos_tributaveis=_money(rendimentos_tributaveis),
            total_deducoes=_money(total_deducoes),
            base_calculo=_money(base_calculo),
            irrf=_money(_truncate(irrf, 2)),
            aliquota_aplicada_percentual=_money(aliquota * Decimal("100")),
            parcela_deducao=_money(parcela),
            fonte_tabela=(
                "https://www27.receita.fazenda.gov.br/api/simulador/tabela/"
                f"{data.ano_calendario}"
            ),
        )

    def _get_tabela(self, ano_calendario: int) -> dict[str, Any]:
        url = f"https://www27.receita.fazenda.gov.br/api/simulador/tabela/{ano_calendario}"
        response = request_with_proxy_rotation(
            self._session,
            "GET",
            url,
            timeout=self._timeout_seconds,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www27.receita.fazenda.gov.br/simulador-irpf-rra/",
            },
        )
        response.raise_for_status()
        data = response.json()
        if data.get("codigoRetorno") != "00":
            raise ValueError(data.get("mensagemRetorno") or "Tabela RRA indisponível")
        return data


def _decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    return Decimal(text)


def _truncate(value: Decimal, places: int) -> Decimal:
    quantizer = Decimal("1").scaleb(-places)
    return value.quantize(quantizer, rounding=ROUND_DOWN)


def _money(value: Decimal) -> str:
    return f"{_truncate(value, 2):.2f}"
