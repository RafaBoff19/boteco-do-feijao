# Sistema de estoque — Boteco do Feijão

Projeto de TCC de um amigo do Rafa (pode virar produto real). Controle de estoque, comandas e baixa automática de ingredientes pela receita, para um bar. O Rafa tem conhecimento básico de Python (faculdade): explique o que fizer, em português, e desenvolva passo a passo.

## Como rodar
```
python -m pip install flask reportlab
python bd.py            # cria estoque.db com dados fictícios (python bd.py --recriar apaga e recria)
python app.py           # http://localhost:5000 (também acessível pelo IP do PC na rede local)
python teste_regras.py; python teste_cadastros.py; python teste_produtos.py; python teste_pedidos.py
```
Os testes usam banco em memória e não mexem no `estoque.db`. Todos devem terminar em "TUDO CERTO". Rode-os depois de qualquer mudança em regras.

## Arquitetura
- Servidor Flask local no PC do bar + SQLite. Tablets e celulares acessam pelo IP do PC no mesmo Wi-Fi, sem internet. **Não expor à internet antes de existir login.**
- `banco/schema.sql` (estrutura), `banco/seed.sql` (dados fictícios), `banco/migracoes/NNN_nome.sql` (alterações novas; `bd.aplicar_migracoes` aplica sozinho, controlado por `PRAGMA user_version`). Mudança de estrutura nova = nova migração, nunca editar o schema de um banco já em uso.
- `regras.py` (estoque, alertas), `cadastros.py` (insumos, produtos, receitas, promoções), `pedidos.py` (comandas, itens, entrega), `app.py` + `templates/` + `static/style.css` (interface).
- Interface responsiva (PC, tablet, Galaxy A11 ~390 px). Sem lateral scroll; botões grandes para toque.

## Regras de negócio (não quebrar)
- Tudo é guardado na **unidade de consumo** (ml, g, un). A unidade de compra (fardo, litro) só converte via `fator_compra`.
- Todo movimento de estoque é uma linha **imutável** em `movimentacao` (triggers bloqueiam UPDATE/DELETE). Correção = ajuste ou estorno. `saldo_estoque` é mantido por trigger e deve sempre bater com a soma das movimentações.
- Tipos: entrada, saida, perda (exige motivo), ajuste, transferencia, baixa_pedido, estorno.
- **Estoque negativo é permitido**: grava, avisa e gera alerta; nunca bloqueia. Mínimos: destilados 10 L, cervejas 20 fardos (configurável por produto/categoria).
- Dia operacional: o bar funciona qua–dom, 18h–03h; `turno_data = date('now','localtime','-6 hours')` (madrugada conta no dia anterior). Promoções seguem a mesma lógica.
- Todo produto tem receita (`receita_item`), inclusive cerveja (1 item). `grupo_escolha` marca alternativas (vodka OU cachaça; mínimo 2 itens no grupo).
- Pedido não mexe no estoque. A baixa só ocorre em **ITEM ENTREGUE**: tira do Bar primeiro, completa com o Estoque, e o que faltar deixa o Bar negativo. Receita e preço são "fotografados" no pedido. Cancelar item entregue exige motivo e gera estorno nos mesmos setores. Sem baixa dupla.
- Números em pt-BR nos formulários: "1,5" = 1.5; "1.500" = 1500.

## Estado atual
Pronto: estoque por setor (Bar/Estoque), movimentações, histórico, alertas, cadastros (insumos, fornecedores, categorias, produtos com receita, promoções), comandas, pedidos e ITEM ENTREGUE.
**Sem login ainda**: tudo roda como `USUARIO_PROVISORIO = 1` (Administrador). As tabelas perfil, permissao, perfil_permissao e usuario já existem.

## Próximo passo: login e perfis (Passo 8)
Perfis: Dono, Gerente, Caixa, Bartender, Garçom, com permissões configuráveis. Depois: pedidos por QR Code, comunicação Bar↔Estoque, compras com aprovação (gerente pede, dono aprova), executável (.exe com PyInstaller). Fora do escopo inicial: maquininha de cartão, impressora, comanda compartilhada por mesa.

## Pontos em aberto (confirmar com o dono do bar)
- Dose do drink: 100 ml (spec) ou 150 ml (exemplo do documento)? Kit é 150 ml.
- "Cereja" nas categorias é provavelmente "Cerveja".
- Conteúdo dos kits; fardo = 12 garrafas é suposição (`[ASSUMIDO]` no seed).
- Baixa tirando primeiro do Bar e depois do Estoque é suposição minha.
- QR Code do cliente exigiria Wi-Fi do bar ou um serviço na nuvem.
- Papel do Caixa sem maquininha; estabilidade da internet; estoque real inicial.

## Convenções
- Textos de tela e mensagens de erro em português. Erros de uso viram `ErroDeRegra` (a tela mostra a mensagem); nada é gravado parcialmente (transação).
- Validar antes de gravar; operações dentro de `with con:`.
- Não commitar `estoque.db` (está no `.gitignore`).
