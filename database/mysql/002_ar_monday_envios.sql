-- Auto Recupere - Controle de envios Monday.com
-- Evita duplicidade de itens no board.

SET NAMES utf8mb4;
SET time_zone = '+00:00';

CREATE TABLE IF NOT EXISTS ar_monday_envios (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    registro_id VARCHAR(255) NOT NULL,
    requisicao_id BIGINT UNSIGNED NULL,
    processo_pasta VARCHAR(50) NULL,
    processo_requisicao VARCHAR(50) NULL,
    banco VARCHAR(20) NULL,
    conta_deposito VARCHAR(40) NULL,
    monday_board_id BIGINT NOT NULL,
    monday_group_id VARCHAR(120) NULL,
    monday_item_id VARCHAR(64) NULL,
    monday_envio_id INT UNSIGNED NULL,
    item_name VARCHAR(512) NULL,
    status ENUM('pending', 'enviado', 'falha') NOT NULL DEFAULT 'pending',
    motivo TEXT NULL,
    error_message TEXT NULL,
    campos_json JSON NULL,
    enviado_at DATETIME(6) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ar_monday_envios_registro_board (registro_id, monday_board_id),
    UNIQUE KEY uq_ar_monday_envios_item (monday_board_id, monday_item_id),
    KEY idx_ar_monday_envios_status (status),
    KEY idx_ar_monday_envios_requisicao (requisicao_id),
    KEY idx_ar_monday_envios_processo (processo_pasta, processo_requisicao),
    CONSTRAINT fk_ar_monday_envios_requisicao
        FOREIGN KEY (requisicao_id) REFERENCES ar_requisicoes(id)
        ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO ar_schema_migrations (version, description)
VALUES ('002', 'Tabela ar_monday_envios para controle anti-duplicidade Monday')
ON DUPLICATE KEY UPDATE applied_at = applied_at;
