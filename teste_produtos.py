"""Testes de produtos, receitas e promoções. Usa um banco TEMPORÁRIO em memória: não mexe no estoque.db.

Uso:  python teste_produtos.py
"""
import datetime as dt
import sqlite3
import sys
from pathlib import Path

import cadastros
import regras
from regras import ErroDeRegra

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PASTA = Path(__file__).resolve().parent


def banco_de_teste():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((PASTA / "banco" / "schema.sql").read_text(encoding="utf-8"))
    con.executescript((PASTA / "banco" / "seed.sql").read_text(encoding="utf-8"))
    return con


def ok(msg):
    print("OK  ", msg)


def deve_falhar(funcao, *args, **kwargs):
    try:
        funcao(*args, **kwargs)
    except ErroDeRegra as e:
        return str(e)
    raise AssertionError("Era para dar erro, mas passou.")


con = banco_de_teste()
ADMIN = 1
ins = lambda nome: con.execute("SELECT id FROM insumo WHERE nome = ?", (nome,)).fetchone()[0]
prod = lambda nome: con.execute("SELECT id FROM produto WHERE nome = ?", (nome,)).fetchone()[0]
item = lambda nome, q, g=None: {"insumo_id": ins(nome), "quantidade": q, "grupo_escolha": g}

# ---- Criação com grupo de escolha
kit = cadastros.criar_produto(con, "Kit Vodka ou Cachaça", "Kit", 80, [
    item("Vodka", 150, " Destilado "), item("Cachaça", 150, "destilado"),
    item("Gelo saborizado coco", 500), item("Copo descartável 400 ml", 4)])
linhas = cadastros.receita_resumida(cadastros.receita_do_produto(con, kit))
assert linhas[-1] == "Escolha (destilado): 150 ml de Cachaça ou 150 ml de Vodka", linhas
assert "4 un de Copo descartável 400 ml" in linhas and len(linhas) == 3
ok(f"kit criado; grupo normalizado ('Destilado ' = 'destilado'): {linhas[-1]}")

# ---- Validações
base = dict(nome="Teste", categoria="Drink", preco_padrao=10, itens=[item("Morango", 4)])
n_prod = con.execute("SELECT COUNT(*) FROM produto").fetchone()[0]
n_itens = con.execute("SELECT COUNT(*) FROM receita_item").fetchone()[0]
for alteracao in [
    {"nome": "  "}, {"nome": "CAIPIRINHA DE MORANGO"}, {"categoria": ""}, {"preco_padrao": -1}, {"preco_padrao": "10"},
    {"itens": []},
    {"itens": [{"insumo_id": 999, "quantidade": 1}]},
    {"itens": [item("Morango", 0)]},
    {"itens": [item("Morango", -2)]},
    {"itens": [item("Morango", 1), item("Morango", 2)]},
    {"itens": [item("Vodka", 100, "destilado"), item("Morango", 4)]},          # grupo com um só ingrediente
]:
    deve_falhar(cadastros.criar_produto, con, **{**base, **alteracao})
assert con.execute("SELECT COUNT(*) FROM produto").fetchone()[0] == n_prod
assert con.execute("SELECT COUNT(*) FROM receita_item").fetchone()[0] == n_itens
ok("11 casos inválidos recusados (nome vazio/repetido, preço, receita vazia, insumo inexistente, qtd 0/negativa, "
   "insumo repetido, grupo de um só ingrediente) e nada foi gravado")

novo = cadastros.criar_insumo(con, "Abacaxi", None, "un", "un", 1)
cadastros.definir_ativo_insumo(con, novo, False)
msg = deve_falhar(cadastros.criar_produto, con, "Suco", "Drink", 8, [{"insumo_id": novo, "quantidade": 1}])
assert "desativado" in msg
ok("insumo desativado não entra em receita nova")

# ---- Edição: troca a receita inteira
cp = prod("Caipirinha de morango")
cadastros.editar_produto(con, cp, "Caipirinha de morango", "Drink", 19.5, [
    item("Cachaça", 100, "destilado"), item("Vodka", 80, "destilado"), item("Morango", 6), item("Gelo comum", 150)])
rec = {r["insumo"]: r["quantidade"] for r in cadastros.receita_do_produto(con, cp)}
assert rec == {"Cachaça": 100, "Vodka": 80, "Morango": 6, "Gelo comum": 150}
assert con.execute("SELECT preco_padrao FROM produto WHERE id=?", (cp,)).fetchone()[0] == 19.5
ok("edição trocou preço e receita (o copo saiu, o gelo mudou de 200 para 150)")
deve_falhar(cadastros.editar_produto, con, cp, "Kit Vodka ou Cachaça", "Drink", 19.5, [item("Morango", 6)])
cadastros.editar_produto(con, cp, "Caipirinha de morango", "Drink", 19.5, [item("Morango", 6)])
cadastros.editar_produto(con, cp, "Caipirinha de morango", "Drink", 18, [
    item("Cachaça", 100, "destilado"), item("Vodka", 100, "destilado"), item("Morango", 4),
    item("Gelo comum", 200), item("Copo descartável 400 ml", 1)])
ok("nome de outro produto é recusado; manter o próprio nome é aceito; receita original restaurada")
deve_falhar(cadastros.editar_produto, con, 999, "X", "Drink", 1, [item("Morango", 1)])

# ---- Integração com desativação de insumo
cadastros.definir_ativo_produto(con, kit, False)
assert kit not in [p["id"] for p in cadastros.listar_produtos(con)]
assert kit in [p["id"] for p in cadastros.listar_produtos(con, incluir_inativos=True)]
ok("produto desativado sai da lista de venda")
regras.ajustar_inventario(con, "Gelo saborizado coco", 0, ADMIN)
cadastros.definir_ativo_insumo(con, ins("Gelo saborizado coco"), False)
ok("insumo que só estava em receita de produto desativado agora pode ser desativado")
cadastros.definir_ativo_produto(con, kit, True)

# ---- Promoções: validações
deve_falhar(cadastros.criar_promocao, con, cp, 10)                                   # sem dias nem período
deve_falhar(cadastros.criar_promocao, con, cp, -1, dias_semana=[3])
deve_falhar(cadastros.criar_promocao, con, cp, 10, dias_semana=[7])
deve_falhar(cadastros.criar_promocao, con, cp, 10, data_inicio="05/11/2026")
deve_falhar(cadastros.criar_promocao, con, cp, 10, data_inicio="2026-12-10", data_fim="2026-12-01")
deve_falhar(cadastros.criar_promocao, con, 999, 10, dias_semana=[3])
ok("promoção sem dias/período, preço negativo, dia 7, data inválida, período invertido e produto inexistente: recusados")

# ---- Preço vigente (seed: caipirinha R$18 padrão, promoção R$14 às quartas; 04/11/2026 é quarta)
v = lambda quando: cadastros.preco_vigente(con, cp, quando)
assert dt.date(2026, 11, 4).weekday() == 2                                            # confere: quarta-feira
assert v(dt.datetime(2026, 11, 4, 20, 0)) == {"preco": 14.0, "origem": "promocao"}
assert v(dt.datetime(2026, 11, 3, 20, 0)) == {"preco": 18.0, "origem": "padrao"}        # terça
assert v(dt.datetime(2026, 11, 5, 20, 0)) == {"preco": 18.0, "origem": "padrao"}        # quinta à noite
ok("preço vigente: quarta à noite R$14 (promoção); terça e quinta à noite R$18")
assert v(dt.datetime(2026, 11, 5, 1, 30)) == {"preco": 14.0, "origem": "promocao"}
assert v(dt.datetime(2026, 11, 5, 5, 59)) == {"preco": 14.0, "origem": "promocao"}
assert v(dt.datetime(2026, 11, 5, 6, 0)) == {"preco": 18.0, "origem": "padrao"}
assert v(dt.datetime(2026, 11, 4, 1, 30)) == {"preco": 18.0, "origem": "padrao"}        # madrugada de quarta = noite de terça
ok("madrugada conta no dia anterior: 01h30 de quinta ainda tem a promoção de quarta; 01h30 de quarta não")

# período + dias; várias promoções valendo: vence a menor
cadastros.criar_promocao(con, cp, 25, data_inicio="2026-12-24", data_fim="2026-12-26")
assert v(dt.datetime(2026, 12, 25, 21, 0)) == {"preco": 25.0, "origem": "promocao"}
assert v(dt.datetime(2026, 12, 27, 21, 0))["origem"] == "padrao"
p3 = cadastros.criar_promocao(con, cp, 12, dias_semana=[3, 4], data_inicio="2026-11-01", data_fim="2026-11-30")
assert v(dt.datetime(2026, 11, 4, 21, 0))["preco"] == 12.0                              # R$14 (qua) e R$12 (qua+qui): vale a menor
assert v(dt.datetime(2026, 11, 5, 21, 0))["preco"] == 12.0                              # quinta só na promoção nova
assert v(dt.datetime(2026, 12, 2, 21, 0))["preco"] == 14.0                              # fora do período da R$12
ok("promoção por período; com várias valendo, vence a menor; fora do período volta ao normal")

# ativar/desativar/excluir
cadastros.definir_ativo_promocao(con, p3, False)
assert v(dt.datetime(2026, 11, 5, 21, 0))["origem"] == "padrao"
lista = cadastros.listar_promocoes(con, cp, dt.datetime(2026, 11, 4, 21, 0))
assert [p["dias_texto"] for p in lista][:2] == ["qua", "todos os dias"] and lista[0]["vale_hoje"] is True
assert [p["vale_hoje"] for p in lista if p["id"] == p3] == [False]
cadastros.definir_ativo_promocao(con, p3, True)
cadastros.excluir_promocao(con, p3)
assert p3 not in [p["id"] for p in cadastros.listar_promocoes(con, cp)]
deve_falhar(cadastros.excluir_promocao, con, p3)
ok("desativar promoção a tira do cálculo; listagem mostra os dias e se vale hoje; excluir remove de vez")

# ---- Listagem final
tudo = {p["nome"]: p for p in cadastros.listar_produtos(con, em=dt.datetime(2026, 11, 4, 21, 0))}
assert tudo["Caipirinha de morango"]["preco_vigente"] == 14.0 and tudo["Caipirinha de morango"]["origem_preco"] == "promocao"
assert tudo["Cerveja long neck"]["preco_vigente"] == 8.0 and tudo["Cerveja long neck"]["receita"] == ["1 un de Cerveja long neck"]
ok("listar_produtos traz preço vigente, origem do preço e receita resumida")

print("\nTUDO CERTO")
