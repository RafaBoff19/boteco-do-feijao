-- =====================================================================
-- Migração 002: comandas, pedidos e baixa por entrega (Fase 2)
-- Aplicada automaticamente pelo bd.py, uma única vez, sem apagar dados.
-- =====================================================================

CREATE TABLE comanda (
    id            INTEGER PRIMARY KEY,
    identificacao TEXT NOT NULL,                   -- nome do cliente ou identificação
    mesa          INTEGER CHECK (mesa IS NULL OR mesa >= 1),
    status        TEXT NOT NULL DEFAULT 'aberta' CHECK (status IN ('aberta', 'fechada', 'cancelada')),
    aberta_por    INTEGER NOT NULL REFERENCES usuario (id),
    aberta_em     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    fechada_em    TEXT,
    turno_data    TEXT NOT NULL DEFAULT (date('now', 'localtime', '-6 hours'))   -- dia operacional
);

CREATE TABLE pedido_item (
    id                    INTEGER PRIMARY KEY,
    comanda_id            INTEGER NOT NULL REFERENCES comanda (id),
    produto_id            INTEGER NOT NULL REFERENCES produto (id),
    quantidade            INTEGER NOT NULL CHECK (quantidade > 0),
    preco_unitario        REAL NOT NULL CHECK (preco_unitario >= 0),   -- preço vigente no momento do pedido
    escolhas              TEXT,                                        -- ex.: 'Vodka' (texto para exibir)
    observacao            TEXT,
    status                TEXT NOT NULL DEFAULT 'pedido' CHECK (status IN ('pedido', 'entregue', 'cancelado')),
    criado_por            INTEGER NOT NULL REFERENCES usuario (id),
    criado_em             TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    entregue_por          INTEGER REFERENCES usuario (id),
    entregue_em           TEXT,
    cancelado_por         INTEGER REFERENCES usuario (id),
    cancelado_em          TEXT,
    motivo_cancelamento   TEXT
);

-- "Foto" do que o item consome, tirada na hora do pedido. Assim mudar a receita depois
-- não altera o que já foi pedido.
CREATE TABLE pedido_item_consumo (
    pedido_item_id INTEGER NOT NULL REFERENCES pedido_item (id) ON DELETE CASCADE,
    insumo_id      INTEGER NOT NULL REFERENCES insumo (id),
    quantidade     REAL NOT NULL CHECK (quantidade > 0),     -- já multiplicada pela quantidade do item
    PRIMARY KEY (pedido_item_id, insumo_id)
);

-- liga cada baixa/estorno ao item que a causou
ALTER TABLE movimentacao ADD COLUMN pedido_item_id INTEGER REFERENCES pedido_item (id);

CREATE INDEX idx_mov_pedido_item ON movimentacao (pedido_item_id);
CREATE INDEX idx_item_status     ON pedido_item (status, criado_em);
CREATE INDEX idx_item_comanda    ON pedido_item (comanda_id);
