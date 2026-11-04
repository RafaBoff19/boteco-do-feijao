# Sistema de estoque — Boteco do Feijão

Controle de estoque, comandas e baixa automática por receita para um bar.
Projeto de TCC. Roda localmente (Flask + SQLite), sem internet; tablets e celulares acessam pelo IP do PC na mesma rede Wi-Fi.

## Como rodar
```
python -m pip install flask reportlab
python bd.py          # cria o banco com dados fictícios (python bd.py --recriar apaga e recria)
python app.py         # abre em http://localhost:5000
```

## Testes
```
python teste_regras.py
python teste_cadastros.py
python teste_produtos.py
python teste_pedidos.py
```

## O que já existe
- Estoque por setor (Bar/Estoque), entradas, saídas, perdas, transferências e ajuste de inventário
- Histórico imutável de movimentações e alertas de estoque mínimo/negativo
- Cadastros: insumos, fornecedores, categorias, produtos com receita e promoções
- Comandas, pedidos e ITEM ENTREGUE (baixa dos ingredientes pela receita, com estorno)

## Próximos passos
Login e perfis (Dono, Gerente, Caixa, Bartender, Garçom), pedidos por QR Code, compras com aprovação, executável (.exe).

## Estrutura
- `banco/schema.sql`, `banco/seed.sql`, `banco/migracoes/` — banco de dados
- `regras.py`, `cadastros.py`, `pedidos.py` — regras de negócio
- `app.py`, `templates/`, `static/` — interface web
