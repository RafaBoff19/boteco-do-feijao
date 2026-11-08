-- =====================================================================
-- Migração 004: compras com aprovação (gerente pede, dono aprova)
-- Aplicada automaticamente pelo bd.py, uma única vez, sem apagar dados.
--
-- Caminho de uma compra:
--   pendente --aprovar--> aprovada --receber--> recebida (gera ENTRADAS no Estoque)
--   pendente --recusar--> recusada            (motivo obrigatório)
--   pendente/aprovada --cancelar--> cancelada (motivo obrigatório)
-- =====================================================================

CREATE TABLE compra (
    id              INTEGER PRIMARY KEY,
    fornecedor_id   INTEGER REFERENCES fornecedor (id),     -- opcional: pode decidir onde comprar depois
    status          TEXT NOT NULL DEFAULT 'pendente'
                    CHECK (status IN ('pendente', 'aprovada', 'recusada', 'recebida', 'cancelada')),
    observacao      TEXT,
    solicitada_por  INTEGER NOT NULL REFERENCES usuario (id),
    solicitada_em   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    decidida_por    INTEGER REFERENCES usuario (id),         -- quem aprovou, recusou ou cancelou
    decidida_em     TEXT,
    motivo          TEXT,                                    -- da recusa ou do cancelamento
    recebida_por    INTEGER REFERENCES usuario (id),
    recebida_em     TEXT,
    CHECK (status NOT IN ('recusada', 'cancelada') OR (motivo IS NOT NULL AND length(trim(motivo)) > 0))
);

CREATE TABLE compra_item (
    id                  INTEGER PRIMARY KEY,
    compra_id           INTEGER NOT NULL REFERENCES compra (id) ON DELETE CASCADE,
    insumo_id           INTEGER NOT NULL REFERENCES insumo (id),
    -- como no resto do sistema, guardado na UNIDADE DE CONSUMO (a tela mostra em fardos, garrafas...)
    quantidade          REAL NOT NULL CHECK (quantidade > 0),
    preco_estimado      REAL CHECK (preco_estimado IS NULL OR preco_estimado >= 0),   -- por unidade de COMPRA
    quantidade_recebida REAL CHECK (quantidade_recebida IS NULL OR quantidade_recebida >= 0),  -- consumo
    preco_pago          REAL CHECK (preco_pago IS NULL OR preco_pago >= 0),           -- por unidade de COMPRA
    UNIQUE (compra_id, insumo_id)
);

-- liga cada entrada de estoque ao item da compra que a originou
ALTER TABLE movimentacao ADD COLUMN compra_item_id INTEGER REFERENCES compra_item (id);

CREATE INDEX idx_compra_status ON compra (status, solicitada_em);
CREATE INDEX idx_compra_item   ON compra_item (compra_id);
CREATE INDEX idx_mov_compra    ON movimentacao (compra_item_id);
