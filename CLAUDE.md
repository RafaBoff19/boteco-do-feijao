# Sistema de estoque — Boteco do Feijão

Projeto de TCC de um amigo do Rafa (pode virar produto real). Controle de estoque, comandas e baixa automática de ingredientes pela receita, para um bar. O Rafa tem conhecimento básico de Python (faculdade): explique o que fizer, em português, e desenvolva passo a passo.

## Como rodar
```
python -m pip install -r requirements.txt   # flask, segno (QR Code) e waitress (servidor)
python bd.py            # cria estoque.db com dados fictícios (python bd.py --recriar apaga e recria)
python app.py           # http://localhost:5000 (também acessível pelo IP do PC na rede local)
python teste_regras.py; python teste_cadastros.py; python teste_produtos.py; python teste_pedidos.py; python teste_login.py; python teste_compras.py; python teste_qrcode.py; python teste_reposicao.py
python -m pip install pyinstaller; python -m PyInstaller --noconfirm boteco.spec   # gera dist/BotecoDoFeijao.exe
```
Os testes usam banco em memória e não mexem no `estoque.db`. Todos devem terminar em "TUDO CERTO". Rode-os depois de qualquer mudança em regras.

## Arquitetura
- Servidor Flask local no PC do bar + SQLite. Tablets e celulares acessam pelo IP do PC no mesmo Wi-Fi, sem internet. **Não expor à internet antes de existir login.**
- `banco/schema.sql` (estrutura), `banco/seed.sql` (dados fictícios), `banco/migracoes/NNN_nome.sql` (alterações novas; `bd.aplicar_migracoes` aplica sozinho, controlado por `PRAGMA user_version`). Mudança de estrutura nova = nova migração, nunca editar o schema de um banco já em uso.
- `regras.py` (estoque, alertas), `auth.py` (login, senhas, usuários, permissões), `cadastros.py` (insumos, produtos, receitas, promoções), `pedidos.py` (comandas, itens, entrega), `compras.py` (compras com aprovação), `mesas.py` (mesas e QR Codes), `reposicao.py` (pedidos Bar↔Estoque), `app.py` + `templates/` + `static/style.css` (interface).
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
Pedidos por QR Code (Passo 10, migração 005): decidido **Wi-Fi do bar** (sem nuvem) e **garçom confirma**. Cada mesa (`mesa`) tem um código sorteado; o QR aponta para `http://<IP do PC>:5000/m/<codigo>` (página pública, sem login; "trocar código" invalida o QR antigo). O cliente abre a própria comanda (guardada no cookie assinado do celular dele) e pede; o item nasce com `origem='cliente'` e `confirmado_em` NULL ("aguardando"): não aparece em Entregas, não pode ser entregue e impede fechar a comanda até o garçom (`pedido.registrar`) CONFIRMAR ou Recusar. O que o cliente faz fica no nome do usuário de sistema `qrcode` (inativo, sem senha, escondido em Usuários). Limites: 10 unidades por pedido, 10 itens aguardando por comanda. `mesa.gerir` (Dono, Gerente) cadastra mesas e imprime os QR (SVG via `segno`).
Comunicação Bar↔Estoque (Passo 11, migração 006): o Bar pede reposição (`reposicao.pedir`: Bartender, Gerente) e o Estoque responde (`reposicao.atender`: Gerente). pendente → atendida/recusada/cancelada (recusa exige motivo; só quem pediu cancela). Pedir não mexe no estoque; **enviar** gera uma `transferencia` Estoque→Bar por item, na mesma transação da mudança de status (`movimentacao.reposicao_item_id`), sem envio duplo. Enviar mais do que há deixa o Estoque negativo e avisa. Urgentes vêm primeiro na fila.
Executável (Passo 12): `boteco.spec` gera um `.exe` de arquivo único com console. `bd.PASTA_RECURSOS` (telas, CSS, .sql; dentro do .exe) é separada de `bd.PASTA_DADOS` (`estoque.db` e `chave_secreta.txt`, ao lado do .exe, para não sumirem). O servidor é o `waitress`. Depois de mudar o código, gerar o .exe de novo.

## Próximo passo
Melhorias (a combinar). Fora do escopo inicial: maquininha de cartão, impressora, comanda compartilhada por mesa.

## Pontos em aberto (confirmar com o dono do bar)
- Dose do drink: 100 ml (spec) ou 150 ml (exemplo do documento)? Kit é 150 ml.
- "Cereja" nas categorias é provavelmente "Cerveja".
- Conteúdo dos kits; fardo = 12 garrafas é suposição (`[ASSUMIDO]` no seed).
- Baixa tirando primeiro do Bar e depois do Estoque é suposição minha.
- QR Code: fixar o IP do PC no roteador (senão os QR impressos param de funcionar) e, de preferência, uma rede Wi-Fi de clientes separada.
- Papel do Caixa sem maquininha; estabilidade da internet; estoque real inicial.
- Sugestão de compra repõe até 2× o mínimo (`FATOR_SUGESTAO` em compras.py) — confirmar. Recebimento parcial encerra a compra (o que faltou exige um novo pedido).
- Instalação real: o .exe numa pasta sem banco cria dados FICTÍCIOS; falta um jeito de começar com o estoque real (o seed também cria os setores). Backup automático do `estoque.db`.
- Ninguém é "estoquista" nos perfis: hoje só Gerente e Dono atendem a reposição. Envio menor que o pedido encerra o pedido; não há confirmação de recebimento pelo Bar.
- Senha mínima de 4 caracteres (permite PIN no tablet): o dono aceita? Permissões padrão de cada perfil (Caixa e Gerente estornam item entregue).

## Convenções
- Textos de tela e mensagens de erro em português. Erros de uso viram `ErroDeRegra` (a tela mostra a mensagem); nada é gravado parcialmente (transação).
- Validar antes de gravar; operações dentro de `with con:`.
- Não commitar `estoque.db` (está no `.gitignore`).
