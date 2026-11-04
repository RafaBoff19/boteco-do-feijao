"""Testes de comandas, pedidos e baixa por entrega. Usa banco TEMPORÁRIO em memória.

Uso:  python teste_pedidos.py
"""
import sqlite3
import sys
from pathlib import Path

import bd
import pedidos
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
    bd.aplicar_migracoes(con)
    return con


def ok(msg):
    print("OK  ", msg)


def deve_falhar(f, *a, **k):
    try:
        f(*a, **k)
    except ErroDeRegra as e:
        return str(e)
    raise AssertionError("Era para dar erro, mas passou.")


con = banco_de_teste()
ADMIN = 1
ins = lambda n: con.execute("SELECT id FROM insumo WHERE nome=?", (n,)).fetchone()[0]
prod = lambda n: con.execute("SELECT id FROM produto WHERE nome=?", (n,)).fetchone()[0]
sal = lambda n: regras.saldo(con, n)["por_setor"]
CAIP, CERV = prod("Caipirinha de morango"), prod("Cerveja long neck")

assert con.execute("PRAGMA user_version").fetchone()[0] == 2
bd.aplicar_migracoes(con)
assert con.execute("PRAGMA user_version").fetchone()[0] == 2
ok("migração 002 aplicada (user_version = 2) e rodar de novo não faz nada")

# ---- comanda e item
cid = pedidos.abrir_comanda(con, "  João  ", ADMIN, mesa=3)
assert con.execute("SELECT identificacao FROM comanda WHERE id=?", (cid,)).fetchone()[0] == "João"
for args in [("", ADMIN), ("   ", ADMIN), ("X", 999)]:
    deve_falhar(pedidos.abrir_comanda, con, *args)
deve_falhar(pedidos.abrir_comanda, con, "X", ADMIN, mesa=0)
ok("comanda aberta; identificação vazia, usuário inexistente e mesa 0 recusados")

msg = deve_falhar(pedidos.adicionar_item, con, cid, CAIP, 1, ADMIN)
assert "destilado" in msg
deve_falhar(pedidos.adicionar_item, con, cid, CAIP, 1, ADMIN, escolhas={"destilado": ins("Morango")})
for q in (0, -1, 1.5, "2", True):
    deve_falhar(pedidos.adicionar_item, con, cid, CERV, q, ADMIN)
deve_falhar(pedidos.adicionar_item, con, cid, 999, 1, ADMIN)
deve_falhar(pedidos.adicionar_item, con, 999, CERV, 1, ADMIN)
assert con.execute("SELECT COUNT(*) FROM pedido_item").fetchone()[0] == 0
ok("sem escolha do destilado, escolha de insumo fora do grupo, quantidade inválida, produto/comanda inexistentes: recusados")

it = pedidos.adicionar_item(con, cid, CAIP, 2, ADMIN, escolhas={"Destilado": ins("Vodka")}, observacao=" sem açúcar ")
row = con.execute("SELECT * FROM pedido_item WHERE id=?", (it,)).fetchone()
assert row["escolhas"] == "Vodka" and row["observacao"] == "sem açúcar" and row["status"] == "pedido"
cons = {r["nome"]: r["quantidade"] for r in pedidos._consumo_do_item(con, it)}
assert cons == {"Vodka": 200, "Morango": 8, "Gelo comum": 400, "Copo descartável 400 ml": 2}, cons
import datetime as dt
esperado = 14.0 if dt.date.today().weekday() == 2 else None
assert row["preco_unitario"] in (14.0, 18.0)
ok(f"2 caipirinhas de vodka: consumo fotografado {cons}; preço fotografado R$ {row['preco_unitario']:.2f}")
assert regras.saldo(con, "Vodka")["total"] == 8000
ok("pedir NÃO mexe no estoque")

# receita mudada depois não altera o pedido
import cadastros
cadastros.editar_produto(con, CAIP, "Caipirinha de morango", "Drink", 30, [
    {"insumo_id": ins("Vodka"), "quantidade": 999, "grupo_escolha": "destilado"},
    {"insumo_id": ins("Cachaça"), "quantidade": 999, "grupo_escolha": "destilado"},
    {"insumo_id": ins("Morango"), "quantidade": 99}])
assert {r["nome"]: r["quantidade"] for r in pedidos._consumo_do_item(con, it)} == cons
cadastros.editar_produto(con, CAIP, "Caipirinha de morango", "Drink", 18, [      # restaura a receita original
    {"insumo_id": ins("Vodka"), "quantidade": 100, "grupo_escolha": "destilado"},
    {"insumo_id": ins("Cachaça"), "quantidade": 100, "grupo_escolha": "destilado"},
    {"insumo_id": ins("Morango"), "quantidade": 4}, {"insumo_id": ins("Gelo comum"), "quantidade": 200},
    {"insumo_id": ins("Copo descartável 400 ml"), "quantidade": 1}])
ok("mudar a receita e o preço depois não altera um pedido já feito")

# ---- entrega: Bar primeiro, depois Estoque
regras.transferir(con, "Vodka", 150, "Estoque", "Bar", ADMIN)          # Bar: 150, Estoque: 7850
r = pedidos.entregar_item(con, it, ADMIN)
assert sal("Vodka") == {"Estoque": 7800, "Bar": 0}, sal("Vodka")
assert sal("Morango") == {"Estoque": 32, "Bar": 0}, sal("Morango")
assert sal("Copo descartável 400 ml") == {"Estoque": 198, "Bar": 0}
ok("entrega: vodka saiu 150 ml do Bar + 50 ml do Estoque; morango/gelo/copo do Estoque")
assert con.execute("SELECT status FROM pedido_item WHERE id=?", (it,)).fetchone()[0] == "entregue"
assert all(m["tipo"] == "baixa_pedido" for m in con.execute("SELECT tipo FROM movimentacao WHERE pedido_item_id=?", (it,)))
msg = deve_falhar(pedidos.entregar_item, con, it, ADMIN)
assert "já foi entregue" in msg and sal("Vodka") == {"Estoque": 7800, "Bar": 0}
ok("segunda entrega do mesmo item é recusada (sem baixa dupla)")

# ---- estoque negativo + alerta
it2 = pedidos.adicionar_item(con, cid, CAIP, 1, ADMIN, escolhas={"destilado": ins("Vodka")})
regras.ajustar_inventario(con, "Vodka", 50, ADMIN, setor="Estoque")
regras.ajustar_inventario(con, "Vodka", 0, ADMIN, setor="Bar")
r = pedidos.entregar_item(con, it2, ADMIN)
assert sal("Vodka") == {"Estoque": 0, "Bar": -50}, sal("Vodka")
assert any("negativo" in a for a in r["avisos"])
assert any(a["tipo"] == "negativo" for a in regras.alertas_pendentes(con))
ok("faltou vodka: entrega gravada mesmo assim, Bar ficou em -50 ml com aviso e alerta")

# ---- cancelar
it3 = pedidos.adicionar_item(con, cid, CERV, 3, ADMIN)
antes = sal("Cerveja long neck")
pedidos.cancelar_item(con, it3, ADMIN)
assert sal("Cerveja long neck") == antes
deve_falhar(pedidos.cancelar_item, con, it3, ADMIN)
deve_falhar(pedidos.entregar_item, con, it3, ADMIN)
ok("cancelar item não entregue não mexe no estoque; não entrega nem cancela de novo")

deve_falhar(pedidos.cancelar_item, con, it, ADMIN)
deve_falhar(pedidos.cancelar_item, con, it, ADMIN, "  ")
pedidos.cancelar_item(con, it, ADMIN, "Cliente devolveu")
est = {k: v for k, v in sal("Vodka").items()}
assert est == {"Estoque": 50, "Bar": 100}, est      # +50 no Estoque, +150 no Bar sobre {0,-50}
assert sal("Morango") == {"Estoque": 36, "Bar": 0} and sal("Copo descartável 400 ml") == {"Estoque": 199, "Bar": 0}
ok("cancelar item entregue exige motivo e devolve exatamente aos setores de origem (estorno)")

# ---- fechar
it4 = pedidos.adicionar_item(con, cid, CERV, 2, ADMIN)
msg = deve_falhar(pedidos.fechar_comanda, con, cid, ADMIN)
assert "ainda não entregue" in msg
pedidos.cancelar_item(con, it2, ADMIN, "Erro")
pedidos.entregar_item(con, it4, ADMIN)
deve_falhar(pedidos.cancelar_comanda, con, cid, ADMIN)
r = pedidos.fechar_comanda(con, cid, ADMIN)
assert r["total"] == 16.0
ok("fechar recusado com item pendente; fechou com total R$ 16,00 (itens cancelados não contam)")
deve_falhar(pedidos.adicionar_item, con, cid, CERV, 1, ADMIN)
deve_falhar(pedidos.fechar_comanda, con, cid, ADMIN)
ok("comanda fechada não aceita itens nem novo fechamento")

c2 = pedidos.abrir_comanda(con, "Maria", ADMIN)
pedidos.adicionar_item(con, c2, CERV, 1, ADMIN)
assert len(pedidos.itens_pendentes(con)) == 1
pedidos.cancelar_comanda(con, c2, ADMIN)
assert pedidos.itens_pendentes(con) == [] and pedidos.dados_comanda(con, c2)["comanda"]["status"] == "cancelada"
assert [c["id"] for c in pedidos.listar_comandas(con)] == []
ok("cancelar comanda sem entregas cancela os itens pendentes e some da fila")

# ---- invariante
div = con.execute("""SELECT COUNT(*) FROM saldo_estoque s WHERE ABS(s.quantidade -
    (SELECT SUM(quantidade) FROM movimentacao m WHERE m.insumo_id=s.insumo_id AND m.setor_id=s.setor_id)) > 1e-9""").fetchone()[0]
assert div == 0
ok("saldo_estoque continua batendo com a soma das movimentações")
print("\nTUDO CERTO")
