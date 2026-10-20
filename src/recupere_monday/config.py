from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Google Drive
    google_drive_folder_id: str = Field(..., description="ID da pasta no Google Drive")
    google_service_account_file: Path = Field(
        ..., description="Caminho para o JSON da service account"
    )
    google_drive_recursive: bool = Field(
        default=True,
        description="Buscar PDFs em subpastas (ex: dd-mm-aa/subpasta/arquivo.pdf)",
    )
    google_drive_only_date_folders: bool = Field(
        default=False,
        description="Na raiz, entrar apenas em pastas com nome dd-mm-aa ou dd-mm-aaaa",
    )
    google_drive_max_depth: int = Field(
        default=10,
        ge=1,
        le=20,
        description="Profundidade máxima de subpastas",
    )
    pdf_download_path: Path = Field(
        default=Path("pdfs"),
        description="Pasta local onde os PDFs baixados são salvos",
    )
    pdf_remove_after_processing: bool = Field(
        default=False,
        description="No pipeline completo, remove o PDF após processar",
    )
    models_demonstrativos_path: Path = Field(
        default=Path("modelos/demonstrativos"),
        description="PDFs de referência para Demonstrativo de Pagamento",
    )
    extracted_demonstrativos_path: Path = Field(
        default=Path("pdfs_extraidos/demonstrativos"),
        description="Saída dos demonstrativos extraídos",
    )
    models_requisitorios_path: Path = Field(
        default=Path("modelos/requisitórios"),
        description="PDFs de referência para Ofício Requisitório",
    )
    extracted_requisitorios_path: Path = Field(
        default=Path("pdfs_extraidos/requisitorios"),
        description="Saída dos requisitórios extraídos",
    )

    # LLM (openai | gemini)
    llm_provider: Literal["openai", "gemini"] = Field(
        default="openai",
        description="Provedor de IA para parse de PDFs: openai (ChatGPT) ou gemini",
    )
    openai_api_key: str = Field(
        default="",
        description="Obrigatória quando LLM_PROVIDER=openai",
    )
    openai_model: str = Field(default="gpt-4o")
    gemini_api_key: str = Field(
        default="",
        description="Obrigatória quando LLM_PROVIDER=gemini (Google AI Studio)",
    )
    gemini_model: str = Field(
        default="gemini-3.5-flash",
        description="Modelo Gemini (ex.: gemini-3.5-flash)",
    )
    openai_extraction_prompt: str = Field(
        default=(
            "Analise o documento PDF anexado e extraia as informações relevantes. "
            "Retorne APENAS um objeto JSON válido com os campos que conseguir identificar. "
            "Use chaves em snake_case em português. "
            "Se um campo não existir no documento, omita-o do JSON."
        )
    )
    openai_demonstrativo_prompt: str = Field(
        default="",
        description="Prompt inline para demonstrativos; se vazio, usa OPENAI_DEMONSTRATIVO_PROMPT_FILE",
    )
    openai_demonstrativo_prompt_file: Path = Field(
        default=Path("prompts/demonstrativo_restituicao_ir.txt"),
        description="Arquivo de prompt para demonstrativos (restituição IR)",
    )
    openai_comprovante_prompt_file: Path = Field(
        default=Path("prompts/comprovante_levantamento.txt"),
        description="Prompt para comprovante de levantamento (etapa 2, uso futuro)",
    )
    openai_calculo_prompt_file: Path = Field(
        default=Path("prompts/calculo_restituicao_ir.txt"),
        description="Prompt para cálculo usando demonstrativo e comprovante anexados",
    )
    openai_calculo_juros_zerado_prompt_file: Path = Field(
        default=Path("prompts/calculo_restituicao_ir_juros_zerado.txt"),
        description="Prompt quando Juros de Mora/SELIC estiver zerado",
    )
    openai_calculo_rra_direto_prompt_file: Path = Field(
        default=Path("prompts/calculo_restituicao_ir_rra_direto.txt"),
        description="Prompt quando Juros de Mora/SELIC não estiver zerado",
    )
    openai_validacao_leitura_pdf_prompt_file: Path = Field(
        default=Path("prompts/validacao_leitura_pdf.txt"),
        description="Prompt para validar se a IA leu os PDFs anexados",
    )
    planilha_juros_modelo_path: Path = Field(
        default=Path("modelos/planilha_juros/Planilha Juros TRF4 Demonstrativo 27062025.xlsx"),
        description="Planilha modelo usada como referência para atualização dos juros",
    )
    demonstrativo_extractions_output_path: Path = Field(
        default=Path("data/extracoes/demonstrativos"),
        description="JSONs retornados pela IA para cada demonstrativo",
    )
    demonstrativo_campos_output_path: Path = Field(
        default=Path("data/extracoes/demonstrativos_campos"),
        description="JSONs com extração automática (regex) dos demonstrativos",
    )

    # Comprovantes de levantamento (working copy; cópia canônica vai para a pasta do caso)
    comprovantes_caixa_output_path: Path = Field(
        default=Path("data/extracoes/comprovantes_caixa"),
        description=(
            "PDFs intermediários de comprovantes da Caixa "
            "(cópia final em data/requerentes[_inaptos]/)"
        ),
    )
    comprovantes_caixa_status_output_path: Path = Field(
        default=Path("data/extracoes/comprovantes_caixa"),
        description="JSONs de status da coleta de comprovantes da Caixa",
    )
    caixa_deposito_judicial_base_url: str = Field(
        default="https://depositojudicial.caixa.gov.br",
        description="URL base do portal de depósito judicial da Caixa",
    )
    comprovantes_bb_output_path: Path = Field(
        default=Path("data/extracoes/comprovantes_bb"),
        description=(
            "PDFs intermediários de comprovantes do Banco do Brasil "
            "(cópia final em data/requerentes[_inaptos]/)"
        ),
    )
    comprovantes_bb_status_output_path: Path = Field(
        default=Path("data/extracoes/comprovantes_bb"),
        description="JSONs de status da coleta de comprovantes do Banco do Brasil",
    )
    bb_resgate_base_url: str = Field(
        default=(
            "https://www63.bb.com.br/portalbb/djo/id/resgate/"
            "dadosResgate,802,4647,500828,0,1,1.bbx"
        ),
        description="URL do formulário de resgate do Banco do Brasil",
    )
    comprovantes_ir_retido_output_path: Path = Field(
        default=Path("data/extracoes/comprovantes_ir_retido.json"),
        description="Relatório consolidado de IR retido nos comprovantes coletados",
    )
    requerentes_output_path: Path = Field(
        default=Path("data/requerentes"),
        description="Pasta individualizada por requerente para cálculo e auditoria",
    )
    requerentes_inaptos_output_path: Path = Field(
        default=Path("data/requerentes_inaptos"),
        description="Pasta para casos inaptos ao cálculo, separados por motivo",
    )
    requerentes_controle_output_path: Path = Field(
        default=Path("data/controle_requerentes.xlsx"),
        description="Planilha de controle das etapas por requerente/registro",
    )
    calculos_excel_output_path: Path = Field(
        default=Path("data/relatorio_calculos_ir.xlsx"),
        description="Planilha executiva consolidada com os resultados finais dos cálculos",
    )
    fluxo_checkpoint_path: Path = Field(
        default=Path("data/fluxo_checkpoint.json"),
        description=(
            "Checkpoint local para --continuar em "
            "--rodar-fluxo-todas-datas / --rodar-fluxo-por-processo"
        ),
    )

    # Monday.com
    monday_api_token: str = Field(..., min_length=1)
    monday_board_id: int = Field(..., gt=0)
    monday_group_id: str = Field(
        default="topics",
        description="ID do grupo no board Monday (ex: topics, group_title)",
    )
    monday_item_name_column: str = Field(
        default="name",
        description="Coluna usada como nome do item (geralmente 'name')",
    )

    # Mapeamento opcional: coluna_monday=campo_json (separados por vírgula)
    # Ex: "texto0=cliente,texto1=valor,texto2=cpf"
    monday_column_mapping: str = Field(default="")
    monday_ultimo_envio_id: int = Field(
        default=0,
        ge=0,
        description=(
            "Último ID sequencial já usado para envios à Monday. "
            "O próximo cálculo encaminhado usará este valor + 1."
        ),
    )
    monday_envio_state_path: Path = Field(
        default=Path("data/monday_envios.json"),
        description="Arquivo local de controle para evitar reenvio/duplicidade na Monday",
    )
    monday_attach_pdfs: bool = Field(
        default=True,
        description=(
            "Se true, anexa demonstrativo e comprovante na coluna/aba Arquivos do item Monday"
        ),
    )
    monday_files_column_id: str = Field(
        default="",
        description=(
            "ID da coluna tipo Arquivos no board Monday "
            "(ex.: file_mm5rjmz0). Se vazio, detecta/cria automaticamente."
        ),
    )
    monday_dedupe_enabled: bool = Field(
        default=True,
        description=(
            "Se true, consulta o board Monday antes de criar o item e ignora "
            "registros que já existem lá"
        ),
    )
    monday_dedupe_fields: str = Field(
        default="cpf_cnpj,processo_requisicao",
        description=(
            "Campos do payload (separados por vírgula) usados como chave de "
            "duplicidade na Monday. Precisam estar em MONDAY_COLUMN_MAPPING."
        ),
    )

    # Aplicação
    log_level: str = Field(default="INFO")
    log_style: str = Field(
        default="simple",
        description="simple = terminal limpo em português; verbose = logs técnicos completos",
    )
    database_path: Path = Field(default=Path("data/processamentos.db"))
    max_retries: int = Field(default=3, ge=1, le=10)
    request_timeout_seconds: int = Field(default=120, ge=30)
    openai_request_delay_seconds: int = Field(
        default=2,
        ge=0,
        description="Pausa entre chamadas à OpenAI (segundos)",
    )

    # Proxy rotativo (Webshare) — usado por Caixa, BB, RRA e Selic
    http_use_proxy: bool = Field(
        default=True,
        description="Se true, usa pool Webshare (ou HTTP_PROXY_URL) nas requisições HTTP",
    )
    webshare_proxies_list: Path = Field(
        default=Path("Webshare 500 proxies.txt"),
        description="Lista ip:porta:user:senha (um por linha)",
    )
    http_proxy_max_attempts: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Quantos proxies tentar antes do fallback direto (RRA/Selic)",
    )
    http_proxy_url: str = Field(
        default="",
        description="Proxy único explícito (sobrescreve o pool se preenchido)",
    )

    # Banco operacional Auto Recupere (MySQL)
    auto_recupere_db_host: str = Field(default="")
    auto_recupere_db_port: int = Field(default=3306, ge=1)
    auto_recupere_db_name: str = Field(default="auto_recupere")
    auto_recupere_db_user: str = Field(default="")
    auto_recupere_db_password: str = Field(default="")

    @property
    def auto_recupere_db_enabled(self) -> bool:
        return all(
            (
                self.auto_recupere_db_host,
                self.auto_recupere_db_name,
                self.auto_recupere_db_user,
            )
        )

    @field_validator("llm_provider", mode="before")
    @classmethod
    def normalize_llm_provider(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator(
        "google_service_account_file",
        "database_path",
        "pdf_download_path",
        "models_demonstrativos_path",
        "extracted_demonstrativos_path",
        "models_requisitorios_path",
        "extracted_requisitorios_path",
        "demonstrativo_extractions_output_path",
        "demonstrativo_campos_output_path",
        "comprovantes_caixa_output_path",
        "comprovantes_caixa_status_output_path",
        "comprovantes_bb_output_path",
        "comprovantes_bb_status_output_path",
        "comprovantes_ir_retido_output_path",
        "requerentes_output_path",
        "requerentes_inaptos_output_path",
        "requerentes_controle_output_path",
        "calculos_excel_output_path",
        "fluxo_checkpoint_path",
        "monday_envio_state_path",
        "openai_demonstrativo_prompt_file",
        "openai_comprovante_prompt_file",
        "openai_calculo_prompt_file",
        "openai_calculo_juros_zerado_prompt_file",
        "openai_calculo_rra_direto_prompt_file",
        "openai_validacao_leitura_pdf_prompt_file",
        "planilha_juros_modelo_path",
        "webshare_proxies_list",
        mode="before",
    )
    @classmethod
    def expand_path(cls, value: str | Path) -> Path:
        return Path(value).expanduser().resolve()

    @model_validator(mode="after")
    def validate_llm_credentials(self) -> "Settings":
        provider = str(self.llm_provider).strip().lower()
        if provider == "openai" and not self.openai_api_key.strip():
            raise ValueError(
                "OPENAI_API_KEY é obrigatória quando LLM_PROVIDER=openai"
            )
        if provider == "gemini" and not self.gemini_api_key.strip():
            raise ValueError(
                "GEMINI_API_KEY é obrigatória quando LLM_PROVIDER=gemini"
            )
        return self

    @property
    def active_llm_model(self) -> str:
        if str(self.llm_provider).strip().lower() == "gemini":
            return self.gemini_model
        return self.openai_model

    def resolved_demonstrativo_prompt(self) -> str:
        """Prompt para --process-demonstrativos: inline > arquivo > genérico."""
        if self.openai_demonstrativo_prompt.strip():
            return self.openai_demonstrativo_prompt.strip()
        if self.openai_demonstrativo_prompt_file.exists():
            return self.openai_demonstrativo_prompt_file.read_text(encoding="utf-8").strip()
        return self.openai_extraction_prompt

    def resolved_calculo_prompt(self) -> str:
        if self.openai_calculo_prompt_file.exists():
            return self.openai_calculo_prompt_file.read_text(encoding="utf-8").strip()
        return self.openai_extraction_prompt

    def resolved_calculo_juros_zerado_prompt(self) -> str:
        if self.openai_calculo_juros_zerado_prompt_file.exists():
            return self.openai_calculo_juros_zerado_prompt_file.read_text(
                encoding="utf-8"
            ).strip()
        return self.resolved_calculo_prompt()

    def resolved_calculo_rra_direto_prompt(self) -> str:
        if self.openai_calculo_rra_direto_prompt_file.exists():
            return self.openai_calculo_rra_direto_prompt_file.read_text(
                encoding="utf-8"
            ).strip()
        return self.resolved_calculo_prompt()

    def resolved_validacao_leitura_pdf_prompt(self) -> str:
        if self.openai_validacao_leitura_pdf_prompt_file.exists():
            return self.openai_validacao_leitura_pdf_prompt_file.read_text(
                encoding="utf-8"
            ).strip()
        return self.openai_extraction_prompt

    def parse_monday_mapping(self) -> dict[str, str]:
        """Retorna {campo_json: coluna_monday}."""
        if not self.monday_column_mapping.strip():
            return {}
        mapping: dict[str, str] = {}
        for pair in self.monday_column_mapping.split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            monday_col, json_field = pair.split("=", 1)
            mapping[json_field.strip()] = monday_col.strip()
        return mapping

    def parse_monday_dedupe_fields(self) -> tuple[str, ...]:
        """Campos do payload que formam a chave de duplicidade na Monday."""
        fields: list[str] = []
        for raw in self.monday_dedupe_fields.split(","):
            field = raw.strip()
            if field and field not in fields:
                fields.append(field)
        return tuple(fields)


def get_settings() -> Settings:
    return Settings()
