# Sistema de estoque — Boteco do Feijão

Projeto de TCC de um amigo do Rafa (pode virar produto real). Controle de estoque, comandas e baixa automática de ingredientes pela receita, para um bar. O Rafa tem conhecimento básico de Python (faculdade): explique o que fizer, em português, e desenvolva passo a passo.

## Como rodar
```
python -m pip install flask reportlab
python bd.py            # cria estoque.db com dados fictícios (python bd.py --recriar apaga e recria)
python app.py           # http://localhost:5000 (também acessível pelo IP do PC na rede local)
python teste_regras.py; python teste_cadastros.py; python teste_produtos.py; python teste_pedidos.py; python teste_login.py; python teste_compras.py
```
Os testes usam banco em memória e não mexem no `estoque.db`. Todos devem terminar em "TUDO CERTO". Rode-os depois de qualquer mudança em regras.

## Arquitetura
- Servidor Flask local no PC do bar + SQLite. Tablets e celulares acessam pelo IP do PC no mesmo Wi-Fi, sem internet. **Não expor à internet antes de existir login.**
- `banco/schema.sql` (estrutura), `banco/seed.sql` (dados fictícios), `banco/migracoes/NNN_nome.sql` (alterações novas; `bd.aplicar_migracoes` aplica sozinho, controlado por `PRAGMA user_version`). Mudança de estrutura nova = nova migração, nunca editar o schema de um banco já em uso.
- `regras.py` (estoque, alertas), `auth.py` (login, senhas, usuários, permissões), `cadastros.py` (insumos, produtos, receitas, promoções), `pedidos.py` (comandas, itens, entrega), `compras.py` (compras com aprovação), `app.py` + `templates/` + `static/style.css` (interface).
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
Login e perfis (Passo 8, migração 003): senha com hash (werkzeug), primeiro acesso cria o Dono (reaproveita o `admin` do seed), usuário novo entra com senha provisória e troca, bloqueio após 5 erros (em memória), sessão de 16 h. Cada rota usa `@requer("permissao", ...)` (basta uma); o menu e os botões usam `pode(...)`. O Dono sempre tem todas as permissões; só o Dono edita permissões de perfil e mexe em usuários Dono; sempre resta um Dono ativo. `pedido.estornar` (novo) controla cancelar item já entregue. `chave_secreta.txt` (gerada sozinha, fora do git) assina o cookie.
Compras (Passo 9, migração 004): pendente → aprovada/recusada → recebida (ou cancelada; recusa e cancelamento exigem motivo). Pedir e aprovar não mexem no estoque; **receber** gera uma `entrada` por item no setor Estoque (ligada por `movimentacao.compra_item_id`, com o fornecedor da compra) e grava o último preço pago em `insumo_fornecedor`. Quantidades guardadas em unidade de consumo, mostradas em unidade de compra. Quem pediu não aprova o próprio pedido (exceto o Dono). Permissões: `compra.solicitar` pede, `compra.aprovar` decide, `estoque.movimentar` recebe. Sugestão de compra: itens no mínimo ou abaixo, repondo até 2× o mínimo.

## Próximo passo
Pedidos por QR Code, comunicação Bar↔Estoque, executável (.exe com PyInstaller). Fora do escopo inicial: maquininha de cartão, impressora, comanda compartilhada por mesa.

## Pontos em aberto (confirmar com o dono do bar)
- Dose do drink: 100 ml (spec) ou 150 ml (exemplo do documento)? Kit é 150 ml.
- "Cereja" nas categorias é provavelmente "Cerveja".
- Conteúdo dos kits; fardo = 12 garrafas é suposição (`[ASSUMIDO]` no seed).
- Baixa tirando primeiro do Bar e depois do Estoque é suposição minha.
- QR Code do cliente exigiria Wi-Fi do bar ou um serviço na nuvem.
- Papel do Caixa sem maquininha; estabilidade da internet; estoque real inicial.
- Sugestão de compra repõe até 2× o mínimo (`FATOR_SUGESTAO` em compras.py) — confirmar. Recebimento parcial encerra a compra (o que faltou exige um novo pedido).
- Senha mínima de 4 caracteres (permite PIN no tablet): o dono aceita? Permissões padrão de cada perfil (Caixa e Gerente estornam item entregue).

## Convenções
- Textos de tela e mensagens de erro em português. Erros de uso viram `ErroDeRegra` (a tela mostra a mensagem); nada é gravado parcialmente (transação).
- Validar antes de gravar; operações dentro de `with con:`.
- Não commitar `estoque.db` (está no `.gitignore`).
