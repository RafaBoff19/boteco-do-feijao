-- =====================================================================
-- Migração 006: comunicação Bar <-> Estoque (Passo 11)
-- Aplicada automaticamente pelo bd.py, uma única vez, sem apagar dados.
--
-- O Bar pede reposição e quem cuida do Estoque responde:
--   pendente --enviar--> atendida   (gera TRANSFERÊNCIAS Estoque -> Bar)
--   pendente --recusar-> recusada   (motivo obrigatório)
--   pendente --cancelar-> cancelada (quem pediu desistiu)
-- =====================================================================

CREATE TABLE reposicao (
    id             INTEGER PRIMARY KEY,
    status         TEXT NOT NULL DEFAULT 'pendente'
                   CHECK (status IN ('pendente', 'atendida', 'recusada', 'cancelada')),
    urgente        INTEGER NOT NULL DEFAULT 0 CHECK (urgente IN (0, 1)),
    observacao     TEXT,
    solicitada_por INTEGER NOT NULL REFERENCES usuario (id),
    solicitada_em  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    respondida_por INTEGER REFERENCES usuario (id),
    respondida_em  TEXT,
    resposta       TEXT,      -- recado de quem atendeu, ou o motivo da recusa
    CHECK (status <> 'recusada' OR (resposta IS NOT NULL AND length(trim(resposta)) > 0))
);

CREATE TABLE reposicao_item (
    id                 INTEGER PRIMARY KEY,
    reposicao_id       INTEGER NOT NULL REFERENCES reposicao (id) ON DELETE CASCADE,
    insumo_id          INTEGER NOT NULL REFERENCES insumo (id),
    -- como no resto do sistema, guardado na UNIDADE DE CONSUMO
    quantidade         REAL NOT NULL CHECK (quantidade > 0),
    -- só para a tela mostrar do jeito que a pessoa pediu (fardo ou unidade, por exemplo)
    unidade            TEXT NOT NULL DEFAULT 'consumo' CHECK (unidade IN ('consumo', 'compra')),
    quantidade_enviada REAL CHECK (quantidade_enviada IS NULL OR quantidade_enviada >= 0),
    UNIQUE (reposicao_id, insumo_id)
);

-- liga cada transferência ao item da reposição que a originou
ALTER TABLE movimentacao ADD COLUMN reposicao_item_id INTEGER REFERENCES reposicao_item (id);

INSERT OR IGNORE INTO permissao (codigo, descricao) VALUES
    ('reposicao.pedir',   'Pedir reposição do Estoque para o Bar'),
    ('reposicao.atender', 'Atender ou recusar pedidos de reposição');

INSERT OR IGNORE INTO perfil_permissao (perfil_id, permissao_id)
SELECT p.id, m.id
FROM perfil p CROSS JOIN permissao m
WHERE (p.nome, m.codigo) IN (VALUES ('Bartender', 'reposicao.pedir'),
                                    ('Gerente',   'reposicao.pedir'),
                                    ('Gerente',   'reposicao.atender'));

CREATE INDEX idx_reposicao_status ON reposicao (status, urgente, solicitada_em);
CREATE INDEX idx_reposicao_item   ON reposicao_item (reposicao_id);
CREATE INDEX idx_mov_reposicao    ON movimentacao (reposicao_item_id);
