"""Testes de compras com aprovação. Usa banco TEMPORÁRIO em memória.

Uso:  python teste_compras.py

A parte final testa as telas de verdade (pelo cliente de teste do Flask), em um
banco temporário: o estoque.db não é tocado.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

import auth
import bd
import compras
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


def id_de(con, tabela, nome):
    return con.execute(f"SELECT id FROM {tabela} WHERE nome = ?", (nome,)).fetchone()[0]


def saldo(con, insumo):
    return con.execute("SELECT saldo_total FROM v_saldo_total WHERE insumo = ?", (insumo,)).fetchone()[0]


def novo_usuario(con, nome, perfil):
    cur = con.execute("INSERT INTO usuario (nome, login, senha_hash, perfil_id) VALUES (?, ?, 'x', ?)",
                      (nome, nome.lower(), id_de(con, "perfil", perfil)))
    con.commit()
    return cur.lastrowid


def testar_regras():
    con = banco_de_teste()
    DONO = 1
    GER = novo_usuario(con, "Gina", "Gerente")
    VODKA, CERV, MORANGO = (id_de(con, "insumo", n) for n in ("Vodka", "Cerveja long neck", "Morango"))
    DIST = id_de(con, "fornecedor", "Distribuidora Exemplo")
    movs_antes = con.execute("SELECT COUNT(*) FROM movimentacao").fetchone()[0]

    # --- sugestão ---
    sug = {s["insumo"]: s for s in compras.sugestao_de_compra(con)}
    assert "Vodka" in sug and sug["Vodka"]["sugerido"] == 12, sug.get("Vodka")   # tem 8 L, mínimo 10 L -> até 20 L
    assert "Cachaça" not in sug                                                 # 12 L, acima do mínimo
    ok("sugestão: vodka no mínimo, sugere 12 litros para chegar ao dobro do mínimo")

    # --- pedir ---
    deve_falhar(compras.criar_compra, con, [], GER)
    deve_falhar(compras.criar_compra, con, [{"insumo_id": VODKA, "quantidade": 0}], GER)
    deve_falhar(compras.criar_compra, con, [{"insumo_id": VODKA, "quantidade": 1},
                                            {"insumo_id": VODKA, "quantidade": 2}], GER)
    deve_falhar(compras.criar_compra, con, [{"insumo_id": 999, "quantidade": 1}], GER)
    ok("pedido sem itens, quantidade zero, item repetido ou insumo inexistente é recusado")

    c1 = compras.criar_compra(con, [{"insumo_id": VODKA, "quantidade": 12, "preco_estimado": 30.0},
                                    {"insumo_id": CERV, "quantidade": 3, "preco_estimado": 60.0}],
                              GER, fornecedor_id=DIST, observacao="feriado")
    d = compras.dados_compra(con, c1)
    vodka = next(i for i in d["itens"] if i["nome"] == "Vodka")
    assert vodka["quantidade"] == 12000 and vodka["qtd_compra"] == 12
    assert d["total_estimado"] == 12 * 30 + 3 * 60
    assert con.execute("SELECT COUNT(*) FROM movimentacao").fetchone()[0] == movs_antes
    ok("pedido grava em ml (12 litros = 12.000 ml), calcula o total e NÃO mexe no estoque")

    # --- aprovar / recusar ---
    deve_falhar(compras.receber, con, c1, GER, {})
    msg = deve_falhar(compras.aprovar, con, c1, GER)
    assert "você mesmo" in msg
    ok("não dá para receber antes de aprovar; quem pediu não aprova o próprio pedido")

    deve_falhar(compras.recusar, con, c1, DONO, "  ")
    compras.aprovar(con, c1, DONO)
    deve_falhar(compras.aprovar, con, c1, DONO)
    deve_falhar(compras.recusar, con, c1, DONO, "tarde demais")
    assert compras.contar_pendencias(con) == {"pendente": 0, "aprovada": 1}
    ok("Dono aprova; recusa exige motivo; não se decide duas vezes")

    c2 = compras.criar_compra(con, [{"insumo_id": MORANGO, "quantidade": 2}], DONO)
    compras.aprovar(con, c2, DONO)
    ok("o Dono pode aprovar o pedido que ele mesmo fez")

    c3 = compras.criar_compra(con, [{"insumo_id": MORANGO, "quantidade": 1}], GER)
    compras.recusar(con, c3, DONO, "ainda tem bastante")
    deve_falhar(compras.cancelar, con, c3, GER, "x")
    ok("compra recusada fica encerrada (não dá para cancelar)")

    # --- receber ---
    itens = {i["nome"]: i["id"] for i in compras.dados_compra(con, c1)["itens"]}
    deve_falhar(compras.receber, con, c1, DONO, {itens["Vodka"]: {"quantidade": 0},
                                                 itens["Cerveja long neck"]: {"quantidade": 0}})
    deve_falhar(compras.receber, con, c1, DONO, {itens["Vodka"]: {"quantidade": -1}})
    vodka_antes, cerv_antes = saldo(con, "Vodka"), saldo(con, "Cerveja long neck")
    r = compras.receber(con, c1, GER, {itens["Vodka"]: {"quantidade": 12, "preco_pago": 28.5},
                                       itens["Cerveja long neck"]: {"quantidade": 2}})
    assert saldo(con, "Vodka") == vodka_antes + 12000
    assert saldo(con, "Cerveja long neck") == cerv_antes + 24
    assert len(r["movimentacao_ids"]) == 2 and len(r["faltas"]) == 1 and "Cerveja" in r["faltas"][0]
    ok("receber dá entrada em ml/un no Estoque; avisa que veio 2 de 3 fardos")

    mov = con.execute("SELECT * FROM movimentacao WHERE id = ?", (r["movimentacao_ids"][0],)).fetchone()
    assert mov["tipo"] == "entrada" and mov["fornecedor_id"] == DIST and mov["compra_item_id"] is not None
    assert mov["motivo"] == f"Compra nº {c1}"
    preco = con.execute("SELECT ultimo_preco FROM insumo_fornecedor WHERE insumo_id = ? AND fornecedor_id = ?",
                        (VODKA, DIST)).fetchone()[0]
    assert preco == 28.5
    ok("a entrada fica ligada à compra e ao fornecedor; o último preço pago é guardado")

    deve_falhar(compras.receber, con, c1, GER, {})
    deve_falhar(compras.cancelar, con, c1, DONO, "x")
    ok("compra recebida não pode ser recebida de novo (sem entrada dupla) nem cancelada")

    soma = con.execute("SELECT SUM(quantidade) FROM movimentacao WHERE insumo_id = ?", (VODKA,)).fetchone()[0]
    tabela = con.execute("SELECT SUM(quantidade) FROM saldo_estoque WHERE insumo_id = ?", (VODKA,)).fetchone()[0]
    assert soma == tabela
    ok("saldo_estoque continua batendo com a soma das movimentações")

    # --- cancelar ---
    deve_falhar(compras.cancelar, con, c2, DONO, "")
    compras.cancelar(con, c2, DONO, "fruteira fechada")
    assert compras.dados_compra(con, c2)["compra"]["status"] == "cancelada"
    assert len(compras.listar_compras(con, ("pendente", "aprovada"))) == 0
    assert len(compras.listar_compras(con)) == 3
    ok("compra aprovada pode ser cancelada com motivo; listagens filtram por status")

    print("OK   (regras de compras)")


def testar_telas():
    import app as modulo_app

    bd.ARQUIVO_BANCO = Path(tempfile.mkdtemp()) / "teste.db"
    auth._falhas.clear()
    modulo_app.app.config["TESTING"] = True
    web = modulo_app.app.test_client()

    con = bd.conectar()
    auth.configurar_primeiro_acesso(con, "Rafa", "rafa", "segredo1", "segredo1")
    eu = auth.carregar_usuario(con, 1)
    for nome, perfil in (("gina", "Gerente"), ("bia", "Bartender")):
        uid = auth.criar_usuario(con, nome.title(), nome, "senha1", id_de(con, "perfil", perfil), eu)
        con.execute("UPDATE usuario SET trocar_senha = 0 WHERE id = ?", (uid,))
    con.commit()
    VODKA = id_de(con, "insumo", "Vodka")
    con.close()

    def entrar(login, senha):
        web.post("/sair")
        web.post("/entrar", data={"login": login, "senha": senha})

    entrar("gina", "senha1")
    texto = web.get("/compras/nova?sugestao=1").get_data(as_text=True)
    assert "Vodka" in texto and 'value="12"' in texto
    r = web.post("/compras/nova", data={"fornecedor": "", "observacao": "",
                                        "insumo": [str(VODKA), ""], "quantidade": ["1,5", ""], "preco": ["", ""]})
    assert r.status_code == 302
    cid = int(r.location.rstrip("/").split("/")[-1])
    assert web.post(f"/compras/{cid}/aprovar").status_code == 403      # Gerente não tem compra.aprovar
    assert "APROVAR" not in web.get(f"/compras/{cid}").get_data(as_text=True)
    ok("tela: Gerente vê a sugestão, pede 1,5 litro, não vê nem consegue usar o botão de aprovar")

    entrar("bia", "senha1")
    assert web.get("/compras").status_code == 403
    ok("tela: Bartender não acessa compras")

    entrar("rafa", "segredo1")
    assert '<span class="contador">1</span>' in web.get("/compras").get_data(as_text=True)
    web.post(f"/compras/{cid}/aprovar")
    con = bd.conectar()
    antes = saldo(con, "Vodka")
    item = con.execute("SELECT id FROM compra_item WHERE compra_id = ?", (cid,)).fetchone()[0]
    con.close()
    r = web.post(f"/compras/{cid}/receber", data={"item": str(item), f"recebido_{item}": "1,5",
                                                  f"preco_{item}": ""}, follow_redirects=True)
    assert "1 entrada(s)" in r.get_data(as_text=True)
    con = bd.conectar()
    assert saldo(con, "Vodka") == antes + 1500
    con.close()
    ok("tela: Dono vê o aviso no menu, aprova e recebe; 1,5 litro vira 1.500 ml no estoque")

    print("OK   (telas)")


if __name__ == "__main__":
    testar_regras()
    testar_telas()
    print("\nTUDO CERTO")
