"""Testes dos cadastros. Usa um banco TEMPORÁRIO em memória: não mexe no seu estoque.db.

Uso:  python teste_cadastros.py
"""
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
id_cat = lambda nome: con.execute("SELECT id FROM categoria WHERE nome = ?", (nome,)).fetchone()[0]
id_forn = lambda nome: con.execute("SELECT id FROM fornecedor WHERE nome = ?", (nome,)).fetchone()[0]

# ---- Categorias
cid = cadastros.criar_categoria(con, "  Sucos  ", 5000)
assert con.execute("SELECT nome, estoque_minimo_padrao FROM categoria WHERE id=?", (cid,)).fetchone()[:] == ("Sucos", 5000)
ok("categoria criada (espaços nas pontas removidos)")
deve_falhar(cadastros.criar_categoria, con, "destilados")
deve_falhar(cadastros.criar_categoria, con, "   ")
deve_falhar(cadastros.criar_categoria, con, "Outra", -1)
ok("categoria: nome repetido (sem diferenciar maiúscula), vazio e mínimo negativo são recusados")
cadastros.editar_categoria(con, cid, "Sucos naturais", None)
assert con.execute("SELECT nome, estoque_minimo_padrao FROM categoria WHERE id=?", (cid,)).fetchone()[:] == ("Sucos naturais", None)
deve_falhar(cadastros.editar_categoria, con, 999, "X")
ok("categoria editada (nome e mínimo); editar com o próprio nome não conta como repetido")
cadastros.editar_categoria(con, cid, "Sucos naturais", 100)

# ---- Fornecedores
fid = cadastros.criar_fornecedor(con, "Padaria do Zé", "padaria", "(51) 99999-0000")
deve_falhar(cadastros.criar_fornecedor, con, "padaria do zé")
cadastros.editar_fornecedor(con, fid, "Padaria do Zé Silva", "padaria", None)
assert con.execute("SELECT nome, telefone FROM fornecedor WHERE id=?", (fid,)).fetchone()[:] == ("Padaria do Zé Silva", None)
cadastros.definir_ativo_fornecedor(con, fid, False)
assert fid not in [f["id"] for f in cadastros.listar_fornecedores(con)]
assert fid in [f["id"] for f in cadastros.listar_fornecedores(con, incluir_inativos=True)]
cadastros.definir_ativo_fornecedor(con, fid, True)
ok("fornecedor: criar, editar, desativar (some da lista) e reativar")

# ---- Insumos: criação
lim = cadastros.criar_insumo(con, "Limão", id_cat("Frutas"), "un", "saco", 50,
                             estoque_minimo=2, unidade_minimo="compra",
                             fornecedores_ids=[id_forn("Fruteira Exemplo")])
r = con.execute("SELECT estoque_minimo, fator_compra, unidade_consumo FROM insumo WHERE id=?", (lim,)).fetchone()
assert tuple(r) == (100.0, 50.0, "un")
ok("insumo criado: mínimo de 2 sacos virou 100 un (conversão pela unidade de compra)")
assert con.execute("SELECT saldo_total FROM v_saldo_total WHERE insumo_id=?", (lim,)).fetchone()[0] == 0
ok("insumo novo nasce com saldo zero")

deve_falhar(cadastros.criar_insumo, con, "LIMÃO", None, "un", "saco", 50)
deve_falhar(cadastros.criar_insumo, con, "Teste", None, "litros", "saco", 50)
deve_falhar(cadastros.criar_insumo, con, "Teste", None, "un", "", 50)
deve_falhar(cadastros.criar_insumo, con, "Teste", None, "un", "saco", 0)
deve_falhar(cadastros.criar_insumo, con, "Teste", None, "un", "saco", 5, estoque_minimo=-1)
deve_falhar(cadastros.criar_insumo, con, "Teste", 999, "un", "saco", 5)
deve_falhar(cadastros.criar_insumo, con, "Teste", None, "un", "saco", 5, fornecedores_ids=[999])
deve_falhar(cadastros.criar_insumo, con, "Teste", None, "un", "saco", 5, estoque_minimo=1, unidade_minimo="caixa")
assert con.execute("SELECT COUNT(*) FROM insumo WHERE nome='Teste'").fetchone()[0] == 0
ok("nome repetido, unidade inválida, compra vazia, fator zero, mínimo negativo, categoria/fornecedor inexistentes: todos recusados, nada gravado")

# ---- integração com o estoque e os alertas
r = regras.registrar_entrada(con, "Limão", 3, ADMIN, unidade="consumo")        # 3 un, mínimo 100
assert any("Estoque baixo de Limão" in a for a in r["avisos"])
ok("insumo recém-cadastrado já participa dos alertas de estoque mínimo")

# ---- edição
cadastros.editar_insumo(con, lim, "Limão taiti", id_cat("Frutas"), "un", "caixa", 40,
                        estoque_minimo=100, unidade_minimo="consumo", fornecedores_ids=[])
r = con.execute("SELECT nome, unidade_compra, fator_compra FROM insumo WHERE id=?", (lim,)).fetchone()
assert tuple(r) == ("Limão taiti", "caixa", 40.0)
assert con.execute("SELECT COUNT(*) FROM insumo_fornecedor WHERE insumo_id=?", (lim,)).fetchone()[0] == 0
ok("edição: nome, unidade de compra, fator e fornecedores (removidos) atualizados")
assert con.execute("SELECT saldo_total FROM v_saldo_total WHERE insumo_id=?", (lim,)).fetchone()[0] == 3
ok("editar o cadastro não mexe no saldo")

msg = deve_falhar(cadastros.editar_insumo, con, lim, "Limão taiti", id_cat("Frutas"), "g", "caixa", 40)
ok(f"unidade de consumo travada por ter histórico: '{msg[:60]}...'")
novo = cadastros.criar_insumo(con, "Hortelã", id_cat("Frutas"), "g", "maço", 100)
cadastros.editar_insumo(con, novo, "Hortelã", id_cat("Frutas"), "un", "maço", 10)
assert con.execute("SELECT unidade_consumo FROM insumo WHERE id=?", (novo,)).fetchone()[0] == "un"
ok("sem histórico, a unidade de consumo ainda pode ser corrigida")

deve_falhar(cadastros.editar_insumo, con, lim, "Cachaça", id_cat("Frutas"), "un", "caixa", 40)
cadastros.editar_insumo(con, lim, "Limão taiti", id_cat("Frutas"), "un", "caixa", 40)
ok("editar para o nome de outro insumo é recusado; manter o próprio nome é aceito")

# ---- desativação
deve_falhar(cadastros.definir_ativo_insumo, con, lim, False)
caca = con.execute("SELECT id FROM insumo WHERE nome='Cachaça'").fetchone()[0]
regras.ajustar_inventario(con, "Cachaça", 0, ADMIN)
msg_receita = deve_falhar(cadastros.definir_ativo_insumo, con, caca, False)
assert "Caipirinha" in msg_receita
ok("não desativa insumo com saldo; zerado mas usado em receita ativa também é recusado")
regras.registrar_saida(con, "Limão taiti", 3, ADMIN, "Fim do dia")
cadastros.definir_ativo_insumo(con, lim, False)
assert lim not in [i["id"] for i in cadastros.listar_insumos(con)]
assert lim in [i["id"] for i in cadastros.listar_insumos(con, incluir_inativos=True)]
assert con.execute("SELECT COUNT(*) FROM v_saldo_total WHERE insumo_id=?", (lim,)).fetchone()[0] == 0
deve_falhar(regras.registrar_entrada, con, "Limão taiti", 1, ADMIN)
ok("insumo zerado foi desativado: some da lista e do estoque e não aceita movimentação")
cadastros.definir_ativo_insumo(con, lim, True)
regras.registrar_entrada(con, "Limão taiti", 1, ADMIN)
ok("reativar traz o insumo de volta")

# ---- listagem e dados para edição
lst = {i["nome"]: i for i in cadastros.listar_insumos(con)}
assert lst["Vodka"]["minimo_efetivo"] == 10000 and lst["Vodka"]["fornecedores"] == "Distribuidora Exemplo"
d = cadastros.dados_insumo(con, lim)
assert d["tem_historico"] is True and d["fornecedores_ids"] == []
assert cadastros.dados_insumo(con, 999) is None
ok("listagem traz mínimo efetivo (da categoria) e fornecedores; dados_insumo informa se a unidade está travada")

print("\nTUDO CERTO")
