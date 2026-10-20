-- Auto Recupere - Schema MySQL de controle e auditoria do pipeline
-- Requisitos: MySQL 8.0+ ou MariaDB compatível com JSON.
--
-- Como usar:
--   1. Crie o banco antes, se necessário:
--        CREATE DATABASE auto_recupere CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
--        USE auto_recupere;
--   2. Execute este arquivo com um usuário com permissão de DDL.
--
-- Objetivos:
--   - Evitar baixar/processar novamente arquivos do Drive já concluídos.
--   - Permitir retomada por processo e por etapa.
--   - Guardar trilha auditável de artefatos, inaptidões, IA, Receita/RRA e Selic.
--   - Dar base para relatórios executivos sem depender apenas dos JSONs em disco.
# create database calculo_recupere;
# use calculo_recupere;
SET NAMES utf8mb4;
SET time_zone = '+00:00';

CREATE TABLE IF NOT EXISTS ar_schema_migrations (
    version VARCHAR(50) PRIMARY KEY,
    description VARCHAR(255) NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ar_pipeline_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_uuid CHAR(36) NOT NULL,
    command_line TEXT NULL,
    drive_date_folder VARCHAR(20) NULL,
    limit_processes INT UNSIGNED NULL,
    processar_calculos_ia BOOLEAN NOT NULL DEFAULT FALSE,
    status ENUM('running', 'completed', 'completed_with_errors', 'failed', 'cancelled')
        NOT NULL DEFAULT 'running',
    started_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    finished_at DATETIME(6) NULL,
    summary_json JSON NULL,
    error_message TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ar_pipeline_runs_uuid (run_uuid),
    KEY idx_ar_pipeline_runs_status_started (status, started_at),
    KEY idx_ar_pipeline_runs_drive_date (drive_date_folder)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ar_drive_files (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    drive_file_id VARCHAR(255) NOT NULL,
    drive_parent_id VARCHAR(255) NULL,
    drive_name VARCHAR(512) NOT NULL,
    drive_mime_type VARCHAR(255) NULL,
    drive_modified_time DATETIME(6) NOT NULL,
    drive_created_time DATETIME(6) NULL,
    drive_size_bytes BIGINT UNSIGNED NULL,
    drive_md5_checksum VARCHAR(64) NULL,
    drive_web_view_link TEXT NULL,
    drive_date_folder VARCHAR(20) NULL,
    processo_pasta VARCHAR(50) NULL,
    relative_path TEXT NULL,
    local_download_path TEXT NULL,
    first_seen_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    last_seen_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    last_downloaded_at DATETIME(6) NULL,
    last_completed_at DATETIME(6) NULL,
    last_status ENUM('new', 'downloaded', 'processing', 'completed', 'failed', 'skipped')
        NOT NULL DEFAULT 'new',
    last_error_message TEXT NULL,
    metadata_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ar_drive_files_drive_id (drive_file_id),
    KEY idx_ar_drive_files_process_date (processo_pasta, drive_date_folder),
    KEY idx_ar_drive_files_modified (drive_modified_time),
    KEY idx_ar_drive_files_status (last_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ar_processos (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    processo_pasta VARCHAR(50) NOT NULL,
    drive_date_folder VARCHAR(20) NULL,
    status ENUM(
        'new',
        'downloaded',
        'processing',
        'completed',
        'completed_with_errors',
        'failed',
        'skipped_duplicate'
    ) NOT NULL DEFAULT 'new',
    first_drive_file_id BIGINT UNSIGNED NULL,
    last_run_id BIGINT UNSIGNED NULL,
    total_drive_files INT UNSIGNED NOT NULL DEFAULT 0,
    total_requerentes INT UNSIGNED NOT NULL DEFAULT 0,
    total_requisicoes INT UNSIGNED NOT NULL DEFAULT 0,
    total_aptos INT UNSIGNED NOT NULL DEFAULT 0,
    total_inaptos INT UNSIGNED NOT NULL DEFAULT 0,
    last_error_message TEXT NULL,
    completed_at DATETIME(6) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ar_processos_pasta (processo_pasta),
    KEY idx_ar_processos_status (status),
    KEY idx_ar_processos_drive_date (drive_date_folder),
    CONSTRAINT fk_ar_processos_first_drive_file
        FOREIGN KEY (first_drive_file_id) REFERENCES ar_drive_files(id)
        ON DELETE SET NULL,
    CONSTRAINT fk_ar_processos_last_run
        FOREIGN KEY (last_run_id) REFERENCES ar_pipeline_runs(id)
        ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ar_processo_drive_files (
    processo_id BIGINT UNSIGNED NOT NULL,
    drive_file_id BIGINT UNSIGNED NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (processo_id, drive_file_id),
    KEY idx_ar_processo_drive_files_drive (drive_file_id),
    CONSTRAINT fk_ar_pdf_processo
        FOREIGN KEY (processo_id) REFERENCES ar_processos(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_ar_pdf_drive
        FOREIGN KEY (drive_file_id) REFERENCES ar_drive_files(id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ar_processo_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    processo_id BIGINT UNSIGNED NOT NULL,
    drive_file_id BIGINT UNSIGNED NULL,
    status ENUM('queued', 'running', 'completed', 'completed_with_errors', 'failed', 'skipped')
        NOT NULL DEFAULT 'queued',
    current_stage VARCHAR(80) NULL,
    skip_reason VARCHAR(255) NULL,
    started_at DATETIME(6) NULL,
    finished_at DATETIME(6) NULL,
    error_message TEXT NULL,
    summary_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ar_processo_runs_run_processo (run_id, processo_id),
    KEY idx_ar_processo_runs_status_stage (status, current_stage),
    KEY idx_ar_processo_runs_processo (processo_id),
    CONSTRAINT fk_ar_processo_runs_run
        FOREIGN KEY (run_id) REFERENCES ar_pipeline_runs(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_ar_processo_runs_processo
        FOREIGN KEY (processo_id) REFERENCES ar_processos(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_ar_processo_runs_drive_file
        FOREIGN KEY (drive_file_id) REFERENCES ar_drive_files(id)
        ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ar_stage_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    processo_run_id BIGINT UNSIGNED NOT NULL,
    stage_name ENUM(
        'download',
        'detect_demonstrativos',
        'extract_demonstrativo_fields',
        'collect_caixa',
        'collect_bb',
        'extract_ir_comprovantes',
        'build_requerente_payloads',
        'processar_calculos_ia',
        'exportar_resultados_excel'
    ) NOT NULL,
    attempt_number SMALLINT UNSIGNED NOT NULL DEFAULT 1,
    status ENUM('pending', 'running', 'completed', 'completed_with_errors', 'failed', 'skipped')
        NOT NULL DEFAULT 'pending',
    started_at DATETIME(6) NULL,
    finished_at DATETIME(6) NULL,
    duration_ms BIGINT UNSIGNED NULL,
    input_json JSON NULL,
    output_json JSON NULL,
    error_message TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ar_stage_runs_attempt (processo_run_id, stage_name, attempt_number),
    KEY idx_ar_stage_runs_stage_status (stage_name, status),
    CONSTRAINT fk_ar_stage_runs_processo_run
        FOREIGN KEY (processo_run_id) REFERENCES ar_processo_runs(id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ar_requerentes (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    requerente_key VARCHAR(80) NOT NULL,
    nome VARCHAR(255) NULL,
    cpf_cnpj VARCHAR(32) NULL,
    documento_digits VARCHAR(20) NULL,
    tipo_pessoa ENUM('PF', 'PJ', 'INDETERMINADO') NOT NULL DEFAULT 'INDETERMINADO',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ar_requerentes_key (requerente_key),
    UNIQUE KEY uq_ar_requerentes_documento (documento_digits),
    KEY idx_ar_requerentes_tipo (tipo_pessoa),
    KEY idx_ar_requerentes_nome (nome)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ar_requisicoes (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    processo_id BIGINT UNSIGNED NOT NULL,
    requerente_id BIGINT UNSIGNED NULL,
    processo_requisicao VARCHAR(50) NOT NULL,
    banco ENUM('CEF', 'BB', 'OUTRO', 'INDETERMINADO') NOT NULL DEFAULT 'INDETERMINADO',
    agencia VARCHAR(20) NULL,
    conta_deposito VARCHAR(40) NULL,
    mes_pagamento VARCHAR(20) NULL,
    data_disponibilidade_saque DATE NULL,
    demonstrativo_json_path TEXT NULL,
    demonstrativo_pdf_path TEXT NULL,
    status ENUM('novo', 'apto', 'inapto', 'ignorado', 'falha') NOT NULL DEFAULT 'novo',
    classificacao VARCHAR(80) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ar_requisicoes_identity (
        processo_id,
        processo_requisicao,
        banco,
        conta_deposito
    ),
    KEY idx_ar_requisicoes_requerente (requerente_id),
    KEY idx_ar_requisicoes_status (status, classificacao),
    KEY idx_ar_requisicoes_processo_requisicao (processo_requisicao),
    CONSTRAINT fk_ar_requisicoes_processo
        FOREIGN KEY (processo_id) REFERENCES ar_processos(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_ar_requisicoes_requerente
        FOREIGN KEY (requerente_id) REFERENCES ar_requerentes(id)
        ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ar_artifacts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    processo_run_id BIGINT UNSIGNED NULL,
    requisicao_id BIGINT UNSIGNED NULL,
    artifact_type ENUM(
        'drive_pdf',
        'demonstrativo_pdf',
        'demonstrativo_campos_json',
        'comprovante_pdf',
        'comprovante_status_json',
        'payload_calculo_json',
        'calculo_ia_json',
        'calculo_ia_summary_json',
        'relatorio_excel',
        'controle_excel',
        'outro'
    ) NOT NULL,
    storage_path TEXT NOT NULL,
    storage_path_hash CHAR(64) NOT NULL,
    file_name VARCHAR(512) NULL,
    file_size_bytes BIGINT UNSIGNED NULL,
    sha256 CHAR(64) NULL,
    status ENUM('created', 'updated', 'missing', 'deleted', 'failed') NOT NULL DEFAULT 'created',
    metadata_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ar_artifacts_path_hash (storage_path_hash),
    KEY idx_ar_artifacts_type_status (artifact_type, status),
    KEY idx_ar_artifacts_requisicao (requisicao_id),
    CONSTRAINT fk_ar_artifacts_processo_run
        FOREIGN KEY (processo_run_id) REFERENCES ar_processo_runs(id)
        ON DELETE SET NULL,
    CONSTRAINT fk_ar_artifacts_requisicao
        FOREIGN KEY (requisicao_id) REFERENCES ar_requisicoes(id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ar_comprovante_coletas (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    requisicao_id BIGINT UNSIGNED NOT NULL,
    banco ENUM('CEF', 'BB') NOT NULL,
    status ENUM('coletado', 'nao_localizado', 'falha', 'ignorado_cnpj', 'pulado')
        NOT NULL,
    pdf_artifact_id BIGINT UNSIGNED NULL,
    attempts INT UNSIGNED NOT NULL DEFAULT 0,
    periodo_usado VARCHAR(80) NULL,
    periodo_estrategia VARCHAR(80) NULL,
    attempted_periods_json JSON NULL,
    account_json JSON NULL,
    error_message TEXT NULL,
    raw_status_json JSON NULL,
    collected_at DATETIME(6) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ar_comprovante_coletas_requisicao (requisicao_id),
    KEY idx_ar_comprovante_coletas_status (status),
    KEY idx_ar_comprovante_coletas_banco (banco),
    CONSTRAINT fk_ar_comprovante_coletas_requisicao
        FOREIGN KEY (requisicao_id) REFERENCES ar_requisicoes(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_ar_comprovante_coletas_artifact
        FOREIGN KEY (pdf_artifact_id) REFERENCES ar_artifacts(id)
        ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ar_ir_validacoes (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    requisicao_id BIGINT UNSIGNED NOT NULL,
    tem_ir_retido BOOLEAN NOT NULL DEFAULT FALSE,
    valor_ir_retido DECIMAL(18,2) NULL,
    campo_origem VARCHAR(120) NULL,
    deducao_previdenciaria DECIMAL(18,2) NULL,
    numero_meses_rra DECIMAL(10,2) NULL,
    data_levantamento DATE NULL,
    valor_bruto_resgate DECIMAL(18,2) NULL,
    valor_liquido_resgate DECIMAL(18,2) NULL,
    classificacao ENUM(
        'apto_com_ir',
        'inapto_sem_ir',
        'inapto_sem_numero_meses',
        'inapto_falha_comprovante',
        'ignorado_cnpj',
        'nao_classificado'
    ) NOT NULL DEFAULT 'nao_classificado',
    motivo_inaptidao TEXT NULL,
    validation_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ar_ir_validacoes_requisicao (requisicao_id),
    KEY idx_ar_ir_validacoes_classificacao (classificacao),
    KEY idx_ar_ir_validacoes_tem_ir (tem_ir_retido),
    CONSTRAINT fk_ar_ir_validacoes_requisicao
        FOREIGN KEY (requisicao_id) REFERENCES ar_requisicoes(id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ar_calculos_ia (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    requisicao_id BIGINT UNSIGNED NOT NULL,
    attempt_number SMALLINT UNSIGNED NOT NULL DEFAULT 1,
    status ENUM('processado', 'nao_calculado', 'falha', 'pulado') NOT NULL,
    tipo_fluxo VARCHAR(120) NULL,
    openai_model VARCHAR(120) NULL,
    prompt_path TEXT NULL,
    arquivos_otimizados_para_openai BOOLEAN NOT NULL DEFAULT FALSE,
    tamanho_total_arquivos_bytes BIGINT UNSIGNED NULL,
    output_artifact_id BIGINT UNSIGNED NULL,
    parsed_json JSON NULL,
    receita_rra_status VARCHAR(50) NULL,
    receita_rra_motivo TEXT NULL,
    imposto_atualizado_status VARCHAR(50) NULL,
    imposto_atualizado_motivo TEXT NULL,
    error_message TEXT NULL,
    processed_at DATETIME(6) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ar_calculos_ia_attempt (requisicao_id, attempt_number),
    KEY idx_ar_calculos_ia_status (status),
    KEY idx_ar_calculos_ia_rra_status (receita_rra_status),
    CONSTRAINT fk_ar_calculos_ia_requisicao
        FOREIGN KEY (requisicao_id) REFERENCES ar_requisicoes(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_ar_calculos_ia_artifact
        FOREIGN KEY (output_artifact_id) REFERENCES ar_artifacts(id)
        ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ar_resultados_calculo (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    calculo_ia_id BIGINT UNSIGNED NOT NULL,
    requisicao_id BIGINT UNSIGNED NOT NULL,
    status_final ENUM('calculado', 'nao_calculado', 'falha') NOT NULL,
    ano_calendario SMALLINT UNSIGNED NULL,
    mes_recebimento TINYINT UNSIGNED NULL,
    ano_recebimento SMALLINT UNSIGNED NULL,
    numero_meses_rra DECIMAL(10,2) NULL,
    rendimentos_recebidos DECIMAL(18,2) NULL,
    juros DECIMAL(18,2) NULL,
    previdencia_oficial DECIMAL(18,2) NULL,
    rendimentos_tributaveis DECIMAL(18,2) NULL,
    total_deducoes DECIMAL(18,2) NULL,
    base_calculo DECIMAL(18,2) NULL,
    aliquota_aplicada_percentual DECIMAL(8,4) NULL,
    parcela_deducao DECIMAL(18,2) NULL,
    irrf_calculado DECIMAL(18,2) NULL,
    imposto_retido DECIMAL(18,2) NULL,
    imposto_indevido DECIMAL(18,2) NULL,
    selic_vencimento VARCHAR(20) NULL,
    selic_pagamento VARCHAR(20) NULL,
    selic_percentual DECIMAL(10,6) NULL,
    selic_acumulado DECIMAL(18,2) NULL,
    imposto_retido_indevidamente_atualizado DECIMAL(18,2) NULL,
    fonte_tabela_receita TEXT NULL,
    fonte_selic TEXT NULL,
    alertas_json JSON NULL,
    resultado_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ar_resultados_calculo_calculo (calculo_ia_id),
    KEY idx_ar_resultados_calculo_requisicao (requisicao_id),
    KEY idx_ar_resultados_calculo_status (status_final),
    KEY idx_ar_resultados_calculo_valor_final (imposto_retido_indevidamente_atualizado),
    CONSTRAINT fk_ar_resultados_calculo_calculo
        FOREIGN KEY (calculo_ia_id) REFERENCES ar_calculos_ia(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_ar_resultados_calculo_requisicao
        FOREIGN KEY (requisicao_id) REFERENCES ar_requisicoes(id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ar_event_logs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NULL,
    processo_run_id BIGINT UNSIGNED NULL,
    requisicao_id BIGINT UNSIGNED NULL,
    level ENUM('debug', 'info', 'warning', 'error', 'critical') NOT NULL DEFAULT 'info',
    event_name VARCHAR(160) NOT NULL,
    message TEXT NULL,
    context_json JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_ar_event_logs_run_created (run_id, created_at),
    KEY idx_ar_event_logs_level_created (level, created_at),
    KEY idx_ar_event_logs_event (event_name),
    CONSTRAINT fk_ar_event_logs_run
        FOREIGN KEY (run_id) REFERENCES ar_pipeline_runs(id)
        ON DELETE SET NULL,
    CONSTRAINT fk_ar_event_logs_processo_run
        FOREIGN KEY (processo_run_id) REFERENCES ar_processo_runs(id)
        ON DELETE SET NULL,
    CONSTRAINT fk_ar_event_logs_requisicao
        FOREIGN KEY (requisicao_id) REFERENCES ar_requisicoes(id)
        ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- View para decidir se um arquivo do Drive pode ser pulado sem baixar novamente.
-- Regra: mesmo drive_file_id + mesmo drive_modified_time + último status completed.
CREATE OR REPLACE VIEW vw_ar_drive_files_prontos_para_pular AS
SELECT
    df.id AS drive_file_pk,
    df.drive_file_id,
    df.drive_name,
    df.processo_pasta,
    df.drive_date_folder,
    df.drive_modified_time,
    df.last_status,
    df.last_completed_at
FROM ar_drive_files df
WHERE df.last_status = 'completed'
  AND df.last_completed_at IS NOT NULL;

-- View executiva para diretoria.
CREATE OR REPLACE VIEW vw_ar_resultados_executivos AS
SELECT
    p.processo_pasta,
    req.processo_requisicao,
    r.nome AS requerente_nome,
    r.cpf_cnpj,
    req.banco,
    iv.classificacao,
    rc.status_final,
    rc.ano_calendario,
    rc.mes_recebimento,
    rc.ano_recebimento,
    rc.numero_meses_rra,
    rc.rendimentos_recebidos,
    rc.juros,
    rc.previdencia_oficial,
    rc.base_calculo,
    rc.irrf_calculado,
    rc.imposto_retido,
    rc.imposto_indevido,
    rc.selic_percentual,
    rc.selic_acumulado,
    rc.imposto_retido_indevidamente_atualizado,
    rc.created_at
FROM ar_resultados_calculo rc
JOIN ar_requisicoes req ON req.id = rc.requisicao_id
JOIN ar_processos p ON p.id = req.processo_id
LEFT JOIN ar_requerentes r ON r.id = req.requerente_id
LEFT JOIN ar_ir_validacoes iv ON iv.requisicao_id = req.id;

-- View de pendências operacionais.
CREATE OR REPLACE VIEW vw_ar_pendencias_operacionais AS
SELECT
    p.processo_pasta,
    req.processo_requisicao,
    r.nome AS requerente_nome,
    r.cpf_cnpj,
    req.banco,
    req.classificacao AS requisicao_classificacao,
    iv.classificacao AS ir_classificacao,
    iv.motivo_inaptidao,
    cc.status AS comprovante_status,
    cc.error_message AS comprovante_error,
    ci.status AS calculo_status,
    ci.error_message AS calculo_error,
    req.updated_at
FROM ar_requisicoes req
JOIN ar_processos p ON p.id = req.processo_id
LEFT JOIN ar_requerentes r ON r.id = req.requerente_id
LEFT JOIN ar_ir_validacoes iv ON iv.requisicao_id = req.id
LEFT JOIN ar_comprovante_coletas cc ON cc.requisicao_id = req.id
LEFT JOIN ar_calculos_ia ci ON ci.requisicao_id = req.id
WHERE req.status IN ('inapto', 'falha')
   OR iv.classificacao IN (
        'inapto_sem_ir',
        'inapto_sem_numero_meses',
        'inapto_falha_comprovante',
        'ignorado_cnpj'
   )
   OR cc.status IN ('nao_localizado', 'falha')
   OR ci.status IN ('nao_calculado', 'falha');

INSERT INTO ar_schema_migrations (version, description)
VALUES ('001', 'Schema inicial Auto Recupere para controle de Drive, etapas, inaptos e resultados')
ON DUPLICATE KEY UPDATE applied_at = applied_at;
