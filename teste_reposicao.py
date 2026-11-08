"""Testes da comunicação Bar <-> Estoque. Usa banco TEMPORÁRIO em memória.

Uso:  python teste_reposicao.py

A parte final testa as telas de verdade (pelo cliente de teste do Flask), em um
banco temporário: o estoque.db não é tocado.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

import auth
import bd
import reposicao
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


def saldo(con, insumo, setor):
    r = con.execute(
        """SELECT s.quantidade FROM saldo_estoque s JOIN insumo i ON i.id = s.insumo_id
           JOIN setor st ON st.id = s.setor_id WHERE i.nome = ? AND st.nome = ?""", (insumo, setor)).fetchone()
    return r[0] if r else 0


def movs(con):
    return con.execute("SELECT COUNT(*) FROM movimentacao").fetchone()[0]


def novo_usuario(con, nome, perfil):
    cur = con.execute("INSERT INTO usuario (nome, login, senha_hash, perfil_id) VALUES (?, ?, 'x', ?)",
                      (nome, nome.lower(), id_de(con, "perfil", perfil)))
    con.commit()
    return cur.lastrowid


def testar_regras():
    con = banco_de_teste()
    GER = novo_usuario(con, "Gina", "Gerente")
    BAR = novo_usuario(con, "Bia", "Bartender")
    CERV, VODKA, GELO = (id_de(con, "insumo", n) for n in ("Cerveja long neck", "Vodka", "Gelo comum"))

    # --- pedir ---
    deve_falhar(reposicao.criar, con, [], BAR)
    deve_falhar(reposicao.criar, con, [{"insumo_id": CERV, "quantidade": 0}], BAR)
    deve_falhar(reposicao.criar, con, [{"insumo_id": 999, "quantidade": 1}], BAR)
    deve_falhar(reposicao.criar, con, [{"insumo_id": CERV, "quantidade": 1, "unidade": "caixote"}], BAR)
    deve_falhar(reposicao.criar, con, [{"insumo_id": CERV, "quantidade": 1}, {"insumo_id": CERV, "quantidade": 2}], BAR)
    ok("pedido vazio, quantidade zero, insumo inexistente, medida inválida ou item repetido é recusado")

    antes = movs(con)
    r1 = reposicao.criar(con, [{"insumo_id": CERV, "quantidade": 2, "unidade": "compra"},
                               {"insumo_id": VODKA, "quantidade": 500, "unidade": "consumo"}], BAR)
    d = reposicao.dados(con, r1)
    cerv = next(i for i in d["itens"] if i["nome"] == "Cerveja long neck")
    assert cerv["quantidade"] == 24 and cerv["pedida"] == 2 and cerv["nome_unidade"] == "fardo"
    assert movs(con) == antes and reposicao.contar_pendentes(con) == 1
    ok("pedir grava em unidades (2 fardos = 24 un), lembra a medida e NÃO mexe no estoque")

    # --- enviar ---
    itens = {i["nome"]: i["id"] for i in reposicao.dados(con, r1)["itens"]}
    deve_falhar(reposicao.atender, con, r1, GER, {itens["Cerveja long neck"]: 0, itens["Vodka"]: 0})
    deve_falhar(reposicao.atender, con, r1, GER, {itens["Vodka"]: -1})
    assert movs(con) == antes
    est_antes, bar_antes = saldo(con, "Cerveja long neck", "Estoque"), saldo(con, "Cerveja long neck", "Bar")
    total_antes = est_antes + bar_antes
    r = reposicao.atender(con, r1, GER, {itens["Cerveja long neck"]: 1, itens["Vodka"]: 500}, resposta="só tinha 1")
    assert saldo(con, "Cerveja long neck", "Estoque") == est_antes - 12
    assert saldo(con, "Cerveja long neck", "Bar") == bar_antes + 12
    assert saldo(con, "Cerveja long neck", "Estoque") + saldo(con, "Cerveja long neck", "Bar") == total_antes
    assert len(r["movimentacao_ids"]) == 4 and len(r["faltas"]) == 1 and "pedido 2, enviado 1 fardo" in r["faltas"][0]
    ok("enviar transfere do Estoque para o Bar, o total não muda, e avisa que foi 1 de 2 fardos")

    m = con.execute("SELECT * FROM movimentacao WHERE id = ?", (r["movimentacao_ids"][0],)).fetchone()
    assert m["tipo"] == "transferencia" and m["motivo"] == f"Reposição nº {r1}" and m["reposicao_item_id"] is not None
    assert m["usuario_id"] == GER and m["transferencia_id"] is not None
    ids = con.execute("SELECT COUNT(DISTINCT transferencia_id) FROM movimentacao WHERE reposicao_item_id IS NOT NULL").fetchone()[0]
    assert ids == 2
    ok("cada item vira uma transferência própria, ligada ao pedido e em nome de quem enviou")

    depois = movs(con)
    deve_falhar(reposicao.atender, con, r1, GER, {})
    deve_falhar(reposicao.recusar, con, r1, GER, "tarde")
    deve_falhar(reposicao.cancelar, con, r1, BAR)
    assert movs(con) == depois
    ok("pedido respondido não pode ser enviado de novo (sem transferência dupla)")

    # --- estoque negativo não bloqueia ---
    r2 = reposicao.criar(con, [{"insumo_id": GELO, "quantidade": 999999, "unidade": "consumo"}], BAR, urgente=True)
    r = reposicao.atender(con, r2, GER, {})
    assert saldo(con, "Gelo comum", "Estoque") < 0
    assert any("negativo" in a for a in r["avisos"])
    ok("enviar mais do que tem é gravado, deixa o Estoque negativo e avisa (nunca bloqueia)")

    # --- recusar / cancelar / ordem ---
    r3 = reposicao.criar(con, [{"insumo_id": CERV, "quantidade": 1}], BAR)
    deve_falhar(reposicao.recusar, con, r3, GER, "   ")
    reposicao.recusar(con, r3, GER, "acabou")
    assert reposicao.dados(con, r3)["reposicao"]["resposta"] == "acabou"

    r4 = reposicao.criar(con, [{"insumo_id": CERV, "quantidade": 1}], BAR)
    r5 = reposicao.criar(con, [{"insumo_id": CERV, "quantidade": 1}], GER, urgente=True)
    deve_falhar(reposicao.cancelar, con, r4, GER)
    assert [x["id"] for x in reposicao.listar(con)] == [r5, r4]
    reposicao.cancelar(con, r4, BAR)
    assert [x["id"] for x in reposicao.listar(con)] == [r5]
    assert len(reposicao.listar(con, so_pendentes=False)) == 5
    ok("recusa exige motivo; só quem pediu cancela; urgentes vêm primeiro na fila")

    for ins in (CERV, VODKA, GELO):
        soma = con.execute("SELECT SUM(quantidade) FROM movimentacao WHERE insumo_id = ?", (ins,)).fetchone()[0]
        tab = con.execute("SELECT SUM(quantidade) FROM saldo_estoque WHERE insumo_id = ?", (ins,)).fetchone()[0]
        assert soma == tab
    ok("saldo_estoque continua batendo com a soma das movimentações")

    print("OK   (regras de reposição)")


def testar_telas():
    import app as modulo_app

    bd.ARQUIVO_BANCO = Path(tempfile.mkdtemp()) / "teste.db"
    auth._falhas.clear()
    modulo_app.app.config["TESTING"] = True

    con = bd.conectar()
    auth.configurar_primeiro_acesso(con, "Rafa", "rafa", "segredo1", "segredo1")
    eu = auth.carregar_usuario(con, 1)
    for nome, perfil in (("gina", "Gerente"), ("bia", "Bartender"), ("wagner", "Garçom")):
        uid = auth.criar_usuario(con, nome.title(), nome, "senha1", id_de(con, "perfil", perfil), eu)
        con.execute("UPDATE usuario SET trocar_senha = 0 WHERE id = ?", (uid,))
    con.commit()
    CERV = id_de(con, "insumo", "Cerveja long neck")
    bar_antes = saldo(con, "Cerveja long neck", "Bar")
    con.close()

    def aparelho(login):
        web = modulo_app.app.test_client()
        web.post("/entrar", data={"login": login, "senha": "senha1"})
        return web

    bia, gina, wagner = aparelho("bia"), aparelho("gina"), aparelho("wagner")

    assert wagner.get("/reposicao").status_code == 403
    ok("tela: Garçom não acessa a reposição")

    assert bia.get("/reposicao/nova").status_code == 200
    r = bia.post("/reposicao/nova", data={"insumo": [str(CERV), ""], "quantidade": ["1,5", ""],
                                          "unidade": ["compra", "compra"], "urgente": "1"})
    assert r.status_code == 302
    rid = int(r.location.rstrip("/").split("/")[-1])
    texto = bia.get(f"/reposicao/{rid}").get_data(as_text=True)
    assert "ENVIAR AO BAR" not in texto and "Cancelar meu pedido" in texto
    assert bia.post(f"/reposicao/{rid}/enviar", data={}).status_code == 403
    ok("tela: Bartender pede 1,5 fardo, não vê nem consegue usar o botão de enviar")

    assert '<span class="contador">1</span>' in gina.get("/reposicao").get_data(as_text=True)
    con = bd.conectar()
    item = con.execute("SELECT id FROM reposicao_item WHERE reposicao_id = ?", (rid,)).fetchone()[0]
    con.close()
    assert 'value="1,5"' in gina.get(f"/reposicao/{rid}").get_data(as_text=True)
    r = gina.post(f"/reposicao/{rid}/enviar", data={"item": str(item), f"enviado_{item}": "1,5"}, follow_redirects=True)
    assert "transferências registradas" in r.get_data(as_text=True)
    con = bd.conectar()
    assert saldo(con, "Cerveja long neck", "Bar") == bar_antes + 18
    con.close()
    assert "Enviada" in bia.get(f"/reposicao/{rid}").get_data(as_text=True)
    ok("tela: Gerente vê o aviso no menu e envia; 1,5 fardo vira 18 un no Bar; o Bar vê 'Enviada'")

    print("OK   (telas)")


if __name__ == "__main__":
    testar_regras()
    testar_telas()
    print("\nTUDO CERTO")
