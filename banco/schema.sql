-- =====================================================================
-- Sistema de estoque - Buteco do Feijão
-- Passo 1: modelo de dados da Fase 1 (SQLite)
--
-- Regras de ouro deste modelo:
--  1. Toda quantidade fica na UNIDADE DE CONSUMO do insumo (ml, g ou un).
--     A unidade de compra (caixa, fardo, saco...) só serve para converter.
--  2. O estoque só muda por MOVIMENTAÇÃO. O saldo é atualizado por trigger.
--  3. Movimentação é imutável: não se edita nem se apaga, corrige-se com
--     ajuste ou estorno.
--  4. Estoque negativo é permitido (só gera alerta, nunca bloqueia).
--
-- IMPORTANTE: em cada conexão do Python, rode  PRAGMA foreign_keys = ON;
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- Cadastros básicos
-- ---------------------------------------------------------------------

CREATE TABLE setor (
    id   INTEGER PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE            -- 'Estoque' e 'Bar'
);

CREATE TABLE categoria (
    id                    INTEGER PRIMARY KEY,
    nome                  TEXT NOT NULL UNIQUE,
    -- mínimo padrão da categoria, na unidade de consumo dos insumos dela
    -- (ex.: destilados 10 L = 10000 ml; cervejas 20 fardos = 240 un se fardo = 12)
    estoque_minimo_padrao REAL CHECK (estoque_minimo_padrao IS NULL OR estoque_minimo_padrao >= 0)
);

CREATE TABLE fornecedor (
    id       INTEGER PRIMARY KEY,
    nome     TEXT NOT NULL UNIQUE,
    tipo     TEXT,                       -- 'distribuidora', 'fruteira'...
    telefone TEXT,
    ativo    INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1))
);

CREATE TABLE insumo (
    id              INTEGER PRIMARY KEY,
    nome            TEXT NOT NULL UNIQUE,
    categoria_id    INTEGER REFERENCES categoria (id),
    unidade_consumo TEXT NOT NULL CHECK (unidade_consumo IN ('ml', 'g', 'un')),
    unidade_compra  TEXT NOT NULL,       -- 'caixa', 'fardo', 'saco', 'litro', 'kg', 'un'...
    -- quantas unidades de consumo cabem em 1 unidade de compra
    -- (ex.: fardo de 12 garrafas -> 12 | saco de gelo de 5 kg -> 5000)
    fator_compra    REAL NOT NULL DEFAULT 1 CHECK (fator_compra > 0),
    -- mínimo individual; se NULL vale o padrão da categoria
    estoque_minimo  REAL CHECK (estoque_minimo IS NULL OR estoque_minimo >= 0),
    ativo           INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1))
);

-- quais fornecedores vendem cada insumo
CREATE TABLE insumo_fornecedor (
    insumo_id     INTEGER NOT NULL REFERENCES insumo (id),
    fornecedor_id INTEGER NOT NULL REFERENCES fornecedor (id),
    ultimo_preco  REAL CHECK (ultimo_preco IS NULL OR ultimo_preco >= 0),  -- por unidade de compra
    PRIMARY KEY (insumo_id, fornecedor_id)
);

-- ---------------------------------------------------------------------
-- Usuários e permissões (5 perfis, permissões configuráveis)
-- ---------------------------------------------------------------------

CREATE TABLE perfil (
    id   INTEGER PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE            -- Dono, Gerente, Caixa, Bartender, Garçom
);

CREATE TABLE permissao (
    id        INTEGER PRIMARY KEY,
    codigo    TEXT NOT NULL UNIQUE,      -- ex.: 'estoque.movimentar'
    descricao TEXT NOT NULL
);

CREATE TABLE perfil_permissao (
    perfil_id    INTEGER NOT NULL REFERENCES perfil (id) ON DELETE CASCADE,
    permissao_id INTEGER NOT NULL REFERENCES permissao (id) ON DELETE CASCADE,
    PRIMARY KEY (perfil_id, permissao_id)
);

CREATE TABLE usuario (
    id         INTEGER PRIMARY KEY,
    nome       TEXT NOT NULL,
    login      TEXT NOT NULL UNIQUE,
    senha_hash TEXT NOT NULL,            -- NUNCA guardar a senha pura
    perfil_id  INTEGER NOT NULL REFERENCES perfil (id),
    ativo      INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1))
);

-- ---------------------------------------------------------------------
-- Estoque: movimentações (fonte da verdade) e saldo (calculado por trigger)
-- ---------------------------------------------------------------------

CREATE TABLE movimentacao (
    id               INTEGER PRIMARY KEY,
    insumo_id        INTEGER NOT NULL REFERENCES insumo (id),
    setor_id         INTEGER NOT NULL REFERENCES setor (id),
    tipo             TEXT NOT NULL CHECK (tipo IN
                       ('entrada', 'saida', 'perda', 'ajuste',
                        'transferencia', 'baixa_pedido', 'estorno')),
    -- COM SINAL: positivo entra no setor, negativo sai do setor
    quantidade       REAL NOT NULL CHECK (quantidade <> 0),
    motivo           TEXT,
    fornecedor_id    INTEGER REFERENCES fornecedor (id),   -- usado nas entradas
    usuario_id       INTEGER NOT NULL REFERENCES usuario (id),
    transferencia_id INTEGER,            -- amarra as duas pontas de uma transferência
    -- "dia operacional": o bar vai das 18h às 3h, então madrugada conta no dia anterior
    turno_data       TEXT NOT NULL DEFAULT (date('now', 'localtime', '-6 hours')),
    criado_em        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),

    -- entrada sempre positiva; saída, perda e baixa sempre negativas
    CHECK (   (tipo = 'entrada' AND quantidade > 0)
           OR (tipo IN ('saida', 'perda', 'baixa_pedido') AND quantidade < 0)
           OR  tipo IN ('ajuste', 'transferencia', 'estorno')),
    -- perda exige motivo
    CHECK (tipo <> 'perda' OR (motivo IS NOT NULL AND length(trim(motivo)) > 0))
);

CREATE INDEX idx_mov_insumo ON movimentacao (insumo_id, criado_em);
CREATE INDEX idx_mov_turno  ON movimentacao (turno_data);

-- saldo atual de cada insumo em cada setor (SEM CHECK >= 0: negativo é permitido)
CREATE TABLE saldo_estoque (
    insumo_id  INTEGER NOT NULL REFERENCES insumo (id),
    setor_id   INTEGER NOT NULL REFERENCES setor (id),
    quantidade REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (insumo_id, setor_id)
);

-- cada movimentação atualiza o saldo automaticamente
CREATE TRIGGER trg_mov_atualiza_saldo
AFTER INSERT ON movimentacao
BEGIN
    INSERT INTO saldo_estoque (insumo_id, setor_id, quantidade)
    VALUES (NEW.insumo_id, NEW.setor_id, NEW.quantidade)
    ON CONFLICT (insumo_id, setor_id)
    DO UPDATE SET quantidade = saldo_estoque.quantidade + excluded.quantidade;
END;

-- histórico imutável
CREATE TRIGGER trg_mov_sem_update
BEFORE UPDATE ON movimentacao
BEGIN
    SELECT RAISE(ABORT, 'Movimentação não pode ser alterada: registre um ajuste ou estorno.');
END;

CREATE TRIGGER trg_mov_sem_delete
BEFORE DELETE ON movimentacao
BEGIN
    SELECT RAISE(ABORT, 'Movimentação não pode ser apagada: registre um ajuste ou estorno.');
END;

-- notificações de estoque mínimo / negativo (preenchidas pelas regras em Python)
CREATE TABLE alerta (
    id               INTEGER PRIMARY KEY,
    insumo_id        INTEGER NOT NULL REFERENCES insumo (id),
    tipo             TEXT NOT NULL CHECK (tipo IN ('minimo', 'negativo')),
    saldo_no_momento REAL NOT NULL,
    criado_em        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    lido_em          TEXT,
    lido_por         INTEGER REFERENCES usuario (id)
);

-- ---------------------------------------------------------------------
-- Produtos vendáveis, receitas e preços
-- Todo produto tem receita: cerveja é uma receita de 1 insumo (1 un),
-- chopp é uma receita de X ml, drink e kit têm vários insumos.
-- ---------------------------------------------------------------------

CREATE TABLE produto (
    id           INTEGER PRIMARY KEY,
    nome         TEXT NOT NULL UNIQUE,
    categoria    TEXT NOT NULL,          -- 'Cerveja', 'Chopp', 'Kit', 'Drink'
    preco_padrao REAL NOT NULL CHECK (preco_padrao >= 0),
    ativo        INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1))
);

CREATE TABLE receita_item (
    id            INTEGER PRIMARY KEY,
    produto_id    INTEGER NOT NULL REFERENCES produto (id) ON DELETE CASCADE,
    insumo_id     INTEGER NOT NULL REFERENCES insumo (id),
    quantidade    REAL NOT NULL CHECK (quantidade > 0),  -- na unidade de consumo do insumo
    -- itens com o mesmo grupo_escolha são ALTERNATIVAS (o cliente escolhe um).
    -- ex.: 'destilado' agrupa vodka e cachaça. NULL = ingrediente fixo.
    grupo_escolha TEXT,
    UNIQUE (produto_id, insumo_id)
);

-- promoção temporária: não altera o preço padrão do produto
CREATE TABLE preco_promocao (
    id          INTEGER PRIMARY KEY,
    produto_id  INTEGER NOT NULL REFERENCES produto (id) ON DELETE CASCADE,
    preco       REAL NOT NULL CHECK (preco >= 0),
    dias_semana TEXT,                    -- ex.: '3,4' (0=domingo ... 6=sábado); NULL = todos
    data_inicio TEXT,                    -- 'AAAA-MM-DD' ou NULL
    data_fim    TEXT,
    ativo       INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1))
);

-- ---------------------------------------------------------------------
-- Consultas prontas
-- ---------------------------------------------------------------------

-- saldo somado dos setores + mínimo que vale para o insumo
CREATE VIEW v_saldo_total AS
SELECT i.id                                        AS insumo_id,
       i.nome                                      AS insumo,
       i.unidade_consumo                           AS unidade,
       COALESCE(SUM(s.quantidade), 0)              AS saldo_total,
       COALESCE(i.estoque_minimo, c.estoque_minimo_padrao) AS minimo_efetivo
FROM insumo i
LEFT JOIN categoria c      ON c.id = i.categoria_id
LEFT JOIN saldo_estoque s  ON s.insumo_id = i.id
WHERE i.ativo = 1
GROUP BY i.id;

-- chegou no mínimo (ou abaixo), mas ainda não está negativo
CREATE VIEW v_estoque_baixo AS
SELECT * FROM v_saldo_total
WHERE minimo_efetivo IS NOT NULL
  AND saldo_total <= minimo_efetivo
  AND saldo_total >= 0;

-- qualquer insumo negativo em algum setor
CREATE VIEW v_estoque_negativo AS
SELECT i.nome AS insumo, st.nome AS setor, s.quantidade AS saldo, i.unidade_consumo AS unidade
FROM saldo_estoque s
JOIN insumo i ON i.id = s.insumo_id
JOIN setor st ON st.id = s.setor_id
WHERE s.quantidade < 0;
