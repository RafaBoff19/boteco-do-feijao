-- =====================================================================
-- Dados FICTÍCIOS para desenvolvimento e demonstração.
-- Os valores marcados com [ASSUMIDO] ainda precisam ser confirmados
-- com o dono do bar (ver lista de perguntas pendentes).
-- =====================================================================

-- Setores
INSERT INTO setor (nome) VALUES ('Estoque'), ('Bar');

-- Perfis
INSERT INTO perfil (nome) VALUES ('Dono'), ('Gerente'), ('Caixa'), ('Bartender'), ('Garçom');

-- Permissões
INSERT INTO permissao (codigo, descricao) VALUES
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
    ('caixa.operar',       'Operar o caixa e fechar comandas');

-- Dono: tudo
INSERT INTO perfil_permissao (perfil_id, permissao_id)
SELECT p.id, m.id FROM perfil p CROSS JOIN permissao m WHERE p.nome = 'Dono';

-- Demais perfis
INSERT INTO perfil_permissao (perfil_id, permissao_id)
SELECT p.id, m.id
FROM perfil p CROSS JOIN permissao m
WHERE (p.nome, m.codigo) IN (VALUES
    ('Gerente',   'estoque.ver'),
    ('Gerente',   'estoque.movimentar'),
    ('Gerente',   'estoque.perda'),
    ('Gerente',   'produto.gerir'),
    ('Gerente',   'fornecedor.gerir'),
    ('Gerente',   'compra.solicitar'),
    ('Gerente',   'pedido.registrar'),
    ('Gerente',   'pedido.entregar'),
    ('Gerente',   'caixa.operar'),
    ('Caixa',     'estoque.ver'),
    ('Caixa',     'pedido.registrar'),
    ('Caixa',     'caixa.operar'),
    ('Bartender', 'estoque.ver'),
    ('Bartender', 'estoque.perda'),
    ('Bartender', 'pedido.entregar'),
    ('Garçom',    'pedido.registrar'),
    ('Garçom',    'pedido.entregar'));

-- Usuário inicial. O hash real será gerado pelo programa (passo da regra de negócio).
INSERT INTO usuario (nome, login, senha_hash, perfil_id)
SELECT 'Administrador', 'admin', 'DEFINIR_NO_PRIMEIRO_ACESSO', id FROM perfil WHERE nome = 'Dono';

-- Categorias com mínimo padrão (na unidade de consumo)
INSERT INTO categoria (nome, estoque_minimo_padrao) VALUES
    ('Destilados',   10000),   -- 10 litros
    ('Cervejas',     240),     -- 20 fardos x 12 un [ASSUMIDO: fardo = 12 unidades]
    ('Chopp',        NULL),
    ('Frutas',       NULL),
    ('Gelo',         NULL),
    ('Descartáveis', NULL);

-- Fornecedores fictícios
INSERT INTO fornecedor (nome, tipo) VALUES
    ('Distribuidora Exemplo', 'distribuidora'),
    ('Fruteira Exemplo',      'fruteira');

-- Insumos
INSERT INTO insumo (nome, categoria_id, unidade_consumo, unidade_compra, fator_compra, estoque_minimo)
SELECT v.nome, c.id, v.uc, v.ucompra, v.fator, v.minimo
FROM (SELECT 'Cachaça' AS nome, 'Destilados' AS cat, 'ml' AS uc, 'litro' AS ucompra, 1000.0 AS fator, NULL AS minimo
      UNION ALL SELECT 'Vodka',                  'Destilados',   'ml', 'litro',   1000.0, NULL
      UNION ALL SELECT 'Cerveja long neck',      'Cervejas',     'un', 'fardo',     12.0, NULL
      UNION ALL SELECT 'Chopp',                  'Chopp',        'ml', 'barril', 30000.0, NULL
      UNION ALL SELECT 'Morango',                'Frutas',       'un', 'bandeja',   20.0, NULL  -- [ASSUMIDO] 20 morangos por bandeja
      UNION ALL SELECT 'Gelo comum',             'Gelo',         'g',  'saco',    5000.0, NULL
      UNION ALL SELECT 'Gelo saborizado coco',   'Gelo',         'g',  'saco',    5000.0, NULL
      UNION ALL SELECT 'Copo descartável 400 ml','Descartáveis', 'un', 'pacote',    50.0, 100.0) v
JOIN categoria c ON c.nome = v.cat;

-- Quem vende o quê
INSERT INTO insumo_fornecedor (insumo_id, fornecedor_id)
SELECT i.id, f.id FROM insumo i JOIN fornecedor f
WHERE (i.nome, f.nome) IN (VALUES
    ('Cachaça',                 'Distribuidora Exemplo'),
    ('Vodka',                   'Distribuidora Exemplo'),
    ('Cerveja long neck',       'Distribuidora Exemplo'),
    ('Chopp',                   'Distribuidora Exemplo'),
    ('Gelo comum',              'Distribuidora Exemplo'),
    ('Gelo saborizado coco',    'Distribuidora Exemplo'),
    ('Morango',                 'Fruteira Exemplo'));

-- Estoque inicial fictício (entra como movimentação; o trigger monta o saldo)
-- A vodka começa ABAIXO do mínimo de 10 litros para demonstrar o alerta.
WITH v(nome, qtd) AS (VALUES
    ('Cachaça',                  12000),
    ('Vodka',                     8000),
    ('Cerveja long neck',          300),
    ('Chopp',                    30000),
    ('Morango',                     40),
    ('Gelo comum',               20000),
    ('Gelo saborizado coco',      5000),
    ('Copo descartável 400 ml',    200))
INSERT INTO movimentacao (insumo_id, setor_id, tipo, quantidade, motivo, usuario_id)
SELECT i.id, (SELECT id FROM setor WHERE nome = 'Estoque'), 'entrada', v.qtd,
       'Estoque inicial fictício', (SELECT id FROM usuario WHERE login = 'admin')
FROM v JOIN insumo i ON i.nome = v.nome;

-- Produtos (preços fictícios)
INSERT INTO produto (nome, categoria, preco_padrao) VALUES
    ('Cerveja long neck',     'Cerveja', 8.00),
    ('Chopp 300 ml',          'Chopp',   9.00),
    ('Caipirinha de morango', 'Drink',  18.00);

-- Receitas
-- Drink com 100 ml de destilado [ASSUMIDO: o documento traz 100 ml e 150 ml em pontos diferentes]
-- Quantidades de gelo e copo também são [ASSUMIDAS].
WITH r(produto, insumo, qtd, grupo) AS (VALUES
    ('Cerveja long neck',     'Cerveja long neck',        1, NULL),
    ('Chopp 300 ml',          'Chopp',                  300, NULL),
    ('Caipirinha de morango', 'Cachaça',                100, 'destilado'),
    ('Caipirinha de morango', 'Vodka',                  100, 'destilado'),
    ('Caipirinha de morango', 'Morango',                  4, NULL),
    ('Caipirinha de morango', 'Gelo comum',             200, NULL),
    ('Caipirinha de morango', 'Copo descartável 400 ml',  1, NULL))
INSERT INTO receita_item (produto_id, insumo_id, quantidade, grupo_escolha)
SELECT p.id, i.id, r.qtd, r.grupo
FROM r JOIN produto p ON p.nome = r.produto JOIN insumo i ON i.nome = r.insumo;

-- Promoção fictícia: caipirinha mais barata às quartas (3 = quarta-feira)
INSERT INTO preco_promocao (produto_id, preco, dias_semana)
SELECT id, 14.00, '3' FROM produto WHERE nome = 'Caipirinha de morango';
