-- =====================================================================
-- Migração 003: login, perfis e permissões (Passo 8)
-- Aplicada automaticamente pelo bd.py, uma única vez, sem apagar dados.
--
-- Perfis e permissões não são "dados fictícios": o sistema precisa deles
-- para funcionar. Por isso são garantidos aqui com INSERT OR IGNORE
-- (se já existirem, vindos do seed, nada muda).
-- =====================================================================

-- 1 = a pessoa precisa trocar a senha no próximo login
--     (usado quando o Dono cria o usuário ou redefine a senha dele)
ALTER TABLE usuario ADD COLUMN trocar_senha INTEGER NOT NULL DEFAULT 0 CHECK (trocar_senha IN (0, 1));
ALTER TABLE usuario ADD COLUMN ultimo_acesso TEXT;

INSERT OR IGNORE INTO perfil (nome) VALUES ('Dono'), ('Gerente'), ('Caixa'), ('Bartender'), ('Garçom');

INSERT OR IGNORE INTO permissao (codigo, descricao) VALUES
    ('estoque.ver',        'Consultar saldo e histórico de movimentações'),
    ('estoque.movimentar', 'Registrar entradas, saídas, ajustes e transferências'),
    ('estoque.perda',      'Registrar perdas e desperdícios'),
    ('produto.gerir',      'Cadastrar produtos, insumos, receitas e preços'),
    ('fornecedor.gerir',   'Cadastrar fornecedores'),
    ('usuario.gerir',      'Gerenciar usuários e permissões'),
    ('compra.solicitar',   'Solicitar compras'),
    ('compra.aprovar',     'Aprovar compras'),
    ('pedido.registrar',   'Abrir comandas e registrar pedidos'),
    ('pedido.entregar',    'Marcar item como entregue'),
    ('caixa.operar',       'Operar o caixa e fechar comandas'),
    -- nova: cancelar um item JÁ ENTREGUE devolve ingredientes ao estoque (estorno),
    -- por isso fica separada do cancelamento comum
    ('pedido.estornar',    'Cancelar item já entregue (devolve ao estoque)');

-- Quem pode estornar item entregue: Gerente e Caixa (o Dono sempre pode tudo).
INSERT OR IGNORE INTO perfil_permissao (perfil_id, permissao_id)
SELECT p.id, m.id
FROM perfil p CROSS JOIN permissao m
WHERE (p.nome, m.codigo) IN (VALUES ('Gerente', 'pedido.estornar'),
                                    ('Caixa',   'pedido.estornar'));
