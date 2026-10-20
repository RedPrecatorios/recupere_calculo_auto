-- Classificação de erro nas coletas + suporte a falhas auditáveis

SET NAMES utf8mb4;
SET time_zone = '+00:00';

SET @col_exists := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ar_comprovante_coletas'
      AND COLUMN_NAME = 'error_class'
);
SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE ar_comprovante_coletas ADD COLUMN error_class VARCHAR(40) NULL AFTER error_message',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ar_calculos_ia'
      AND COLUMN_NAME = 'error_class'
);
SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE ar_calculos_ia ADD COLUMN error_class VARCHAR(40) NULL AFTER error_message',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

CREATE TABLE IF NOT EXISTS ar_falhas_operacionais (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_uuid CHAR(36) NULL,
    stage_name VARCHAR(80) NOT NULL,
    processo_pasta VARCHAR(50) NULL,
    processo_requisicao VARCHAR(50) NULL,
    banco VARCHAR(20) NULL,
    conta_deposito VARCHAR(40) NULL,
    registro_id VARCHAR(255) NULL,
    error_class VARCHAR(40) NOT NULL DEFAULT 'unknown',
    error_message TEXT NULL,
    context_json JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_ar_falhas_stage_class (stage_name, error_class),
    KEY idx_ar_falhas_processo (processo_pasta, processo_requisicao),
    KEY idx_ar_falhas_run (run_uuid),
    KEY idx_ar_falhas_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE OR REPLACE VIEW vw_ar_falhas_recentes AS
SELECT
    id,
    run_uuid,
    stage_name,
    processo_pasta,
    processo_requisicao,
    banco,
    conta_deposito,
    registro_id,
    error_class,
    error_message,
    created_at
FROM ar_falhas_operacionais
ORDER BY created_at DESC;

INSERT INTO ar_schema_migrations (version, description)
VALUES ('003', 'error_class em coletas/cálculos + tabela ar_falhas_operacionais')
ON DUPLICATE KEY UPDATE applied_at = applied_at;
