# Pacote: cálculo a partir de demonstrativo + comprovante

Recorte do Auto Recupere com **apenas** a lógica que roda depois que
já existem demonstrativo e comprovante no disco.

## Fluxo (ordem)

```
1. Extrair IR do comprovante     → extract_comprovantes_ir.py
2. Montar payload do requerente  → build_requerente_payloads.py
3. Cálculo IA + RRA + Selic      → process_calculos_ia.py
   └─ Monday.com (quando elegível)
4. Export Excel                  → export_calculos_excel.py
```

No projeto original, as flags equivalentes são:

```powershell
python -m recupere_monday.main --extract-ir-comprovantes
python -m recupere_monday.main --build-requerente-payloads
python -m recupere_monday.main --processar-calculos-ia
# Excel já roda junto do --processar-calculos-ia; opcional:
python -m recupere_monday.main --exportar-resultados-excel
```

Neste pacote use `cli_calculos.py` (entrada enxuta, sem Drive/Caixa/BB).

## Pré-requisitos de entrada (já existentes)

| Artefato | Onde o código espera |
|----------|----------------------|
| JSON de campos do demonstrativo | `data/extracoes/demonstrativos_campos/{processo}/...json` |
| PDF do demonstrativo extraído | `pdfs_extraidos/demonstrativos/{processo}/...pdf` |
| Status da coleta CEF/BB | `data/extracoes/comprovantes_caixa|bb/...json` (`success` + `pdf_path`) |
| PDF do comprovante | path apontado por `pdf_path` no status |

Após o passo 2, a pasta canônica do caso fica em:

```
data/requerentes/{cpf} - {processo}/
  payload_calculo.json
  demonstrativo.pdf
  comprovante.pdf
  ...
```

(Casos inaptos vão para `data/requerentes_inaptos/{motivo}/...`.)

## Estrutura

```
src/recupere_monday/
  cli_calculos.py              # entrada slim
  config.py / logging_config.py / http_proxy.py / proxy_pool.py
  pipeline/
    extract_comprovantes_ir.py
    build_requerente_payloads.py
    process_calculos_ia.py
    export_calculos_excel.py
  extraction/                  # IR do comprovante + campos do demonstrativo
  detection/demonstrativo_patterns.py
  llm/ + openai/ + gemini/     # chamada à IA com PDFs
  receita/rra.py + selic.py    # Receita Federal
  monday/client.py
  storage/operational_db.py    # MySQL opcional (anti-duplicidade)
  drive/client.py              # só por causa do dataclass DriveFile no DB

prompts/                       # prompts de cálculo IR
modelos/planilha_juros/        # planilha injetada no prompt
database/mysql/                # schemas se for usar MySQL
Webshare 500 proxies.txt       # proxies RRA/Selic
```

## O que NÃO está neste pacote

- Download Google Drive
- Detecção/extração de páginas do demonstrativo
- Coleta Caixa/BB (Selenium/HTTP)
- Fluxo genérico legado / reprocessamentos de comprovante

## Dependências Python (mínimas)

`openai`, `google-genai` (se Gemini), `httpx`, `requests`, `tenacity`,
`structlog`, `pymupdf`, `openpyxl`, `pydantic`, `pydantic-settings`,
`python-dotenv`, `mysql-connector-python` (opcional).

## Notas para portar a outro projeto

1. `config.py` ainda exige alguns campos de Drive no boot (`google_drive_folder_id`,
   `google_service_account_file`) — preencha dummies no `.env` ou torne-os opcionais.
2. `storage/operational_db.py` importa `DriveFile` de `drive/client.py`; se não
   quiser a dependência Google, extraia só o dataclass.
3. Monday, MySQL e proxy são opcionais no sentido operacional: sem DB o cálculo
   ainda roda; Monday só encaminha casos elegíveis com token/board configurados.
