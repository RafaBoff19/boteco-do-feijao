# Sistema de estoque — Boteco do Feijão

Controle de estoque, comandas e baixa automática por receita para um bar.
Projeto de TCC. Roda localmente (Flask + SQLite), sem internet; tablets e celulares acessam pelo IP do PC na mesma rede Wi-Fi.

## Como rodar (desenvolvimento)
```
python -m pip install -r requirements.txt
python bd.py          # cria o banco com dados fictícios (python bd.py --recriar apaga e recria)
python app.py         # abre em http://localhost:5000
```
No primeiro acesso, o sistema pede para criar o usuário Dono.

## Como gerar o executável
```
python -m pip install pyinstaller
python -m PyInstaller --noconfirm boteco.spec
```
O programa sai em `dist/BotecoDoFeijao.exe`. Depois de qualquer mudança no código, gere de novo.

## Como usar o executável no PC do bar
1. Crie uma pasta só para o sistema (por exemplo, `Documentos\Boteco`; não use Arquivos de Programas) e coloque o `.exe` nela.
2. Abra com dois cliques. O navegador abre sozinho e a janela preta precisa ficar aberta enquanto o bar usa o sistema.
3. Na primeira vez, o Windows pode mostrar dois avisos: o SmartScreen ("Mais informações" → "Executar assim mesmo", porque o programa não tem assinatura digital) e o Firewall (permita em **redes privadas**, para os tablets e celulares conseguirem acessar).

O `estoque.db` (todos os dados do bar) e a `chave_secreta.txt` são criados ao lado do `.exe`. **Para fazer backup, copie o `estoque.db`** com o sistema fechado. Para atualizar o programa, troque só o `.exe` e mantenha esses dois arquivos: o banco é atualizado sozinho ao abrir.

Em uma pasta sem `estoque.db`, o programa cria um banco com **dados fictícios**, para demonstração.

## Testes
```
python teste_regras.py
python teste_cadastros.py
python teste_produtos.py
python teste_pedidos.py
python teste_login.py
python teste_compras.py
python teste_qrcode.py
python teste_reposicao.py
```

## O que já existe
- Estoque por setor (Bar/Estoque), entradas, saídas, perdas, transferências e ajuste de inventário
- Histórico imutável de movimentações e alertas de estoque mínimo/negativo
- Cadastros: insumos, fornecedores, categorias, produtos com receita e promoções
- Comandas, pedidos e ITEM ENTREGUE (baixa dos ingredientes pela receita, com estorno)
- Login e perfis com permissões configuráveis (Dono, Gerente, Caixa, Bartender, Garçom)
- Compras com aprovação e sugestão pelo estoque mínimo
- Pedidos do cliente pelo QR Code da mesa, com confirmação do garçom
- Pedidos de reposição do Bar para o Estoque
- Executável para Windows

## Estrutura
- `banco/schema.sql`, `banco/seed.sql`, `banco/migracoes/` — banco de dados
- `regras.py`, `auth.py`, `cadastros.py`, `pedidos.py`, `compras.py`, `mesas.py`, `reposicao.py` — regras de negócio
- `app.py`, `templates/`, `static/` — interface web
- `boteco.spec` — receita do executável
