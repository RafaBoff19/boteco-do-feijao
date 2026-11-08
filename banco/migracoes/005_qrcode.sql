-- =====================================================================
-- Migração 005: pedidos pelo QR Code da mesa (Passo 10)
-- Aplicada automaticamente pelo bd.py, uma única vez, sem apagar dados.
--
-- O cliente entra no Wi-Fi do bar, lê o QR Code da mesa, abre a própria
-- comanda e pede. O pedido chega "aguardando confirmação": só depois que
-- um garçom confirma ele vai para a fila de Entregas.
-- =====================================================================

-- Um QR Code por mesa. O código é sorteado (não dá para adivinhar o link de
-- outra mesa) e pode ser trocado, o que invalida o QR antigo.
CREATE TABLE mesa (
    numero INTEGER PRIMARY KEY CHECK (numero >= 1),
    codigo TEXT NOT NULL UNIQUE,
    ativa  INTEGER NOT NULL DEFAULT 1 CHECK (ativa IN (0, 1))
);

-- De onde veio o item e quem confirmou.
--   origem 'equipe'  -> lançado por alguém da equipe (já nasce confirmado)
--   origem 'cliente' -> pedido pelo QR Code; confirmado_em NULL = aguardando o garçom
-- (O status continua 'pedido'; não dá para mudar o CHECK de status sem recriar a tabela.)
ALTER TABLE pedido_item ADD COLUMN origem TEXT NOT NULL DEFAULT 'equipe' CHECK (origem IN ('equipe', 'cliente'));
ALTER TABLE pedido_item ADD COLUMN confirmado_por INTEGER REFERENCES usuario (id);
ALTER TABLE pedido_item ADD COLUMN confirmado_em TEXT;

-- O banco exige um usuário em "aberta_por" e "criado_por". Para o que o cliente
-- faz pelo QR Code, usamos este usuário de sistema: INATIVO e sem senha, então
-- ninguém consegue entrar com ele.
INSERT OR IGNORE INTO usuario (nome, login, senha_hash, perfil_id, ativo)
SELECT 'Cliente (QR Code)', 'qrcode', 'DEFINIR_NO_PRIMEIRO_ACESSO', id, 0 FROM perfil WHERE nome = 'Garçom';

-- Nova permissão: cadastrar mesas e imprimir os QR Codes
INSERT OR IGNORE INTO permissao (codigo, descricao) VALUES
    ('mesa.gerir', 'Cadastrar mesas e imprimir os QR Codes');
INSERT OR IGNORE INTO perfil_permissao (perfil_id, permissao_id)
SELECT p.id, m.id FROM perfil p CROSS JOIN permissao m
WHERE p.nome = 'Gerente' AND m.codigo = 'mesa.gerir';

CREATE INDEX idx_item_aguardando ON pedido_item (origem, confirmado_em, status);
