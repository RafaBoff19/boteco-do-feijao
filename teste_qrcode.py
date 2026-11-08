"""Testes dos pedidos pelo QR Code da mesa. Usa banco TEMPORÁRIO em memória.

Uso:  python teste_qrcode.py

A parte final simula dois aparelhos (o celular do cliente, sem login, e o tablet
do garçom) usando o app de verdade, em um banco temporário: o estoque.db não é tocado.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

import auth
import bd
import mesas
import pedidos
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


def prod(con, nome):
    return con.execute("SELECT id FROM produto WHERE nome = ?", (nome,)).fetchone()[0]


def movs(con):
    return con.execute("SELECT COUNT(*) FROM movimentacao").fetchone()[0]


def testar_regras():
    con = banco_de_teste()
    GARCOM = 1
    CERV = prod(con, "Cerveja long neck")

    # --- mesas ---
    mesas.definir_quantidade(con, 3)
    m = mesas.listar_mesas(con)
    assert [x["numero"] for x in m] == [1, 2, 3] and len({x["codigo"] for x in m}) == 3
    codigo2 = m[1]["codigo"]
    assert mesas.mesa_por_codigo(con, codigo2)["numero"] == 2
    assert mesas.mesa_por_codigo(con, "chute") is None
    mesas.trocar_codigo(con, 2)
    assert mesas.mesa_por_codigo(con, codigo2) is None
    novo2 = mesas.listar_mesas(con)[1]["codigo"]
    mesas.definir_quantidade(con, 1)
    assert mesas.mesa_por_codigo(con, novo2) is None              # mesa 2 desativada
    mesas.definir_quantidade(con, 2)
    assert mesas.mesa_por_codigo(con, novo2)["numero"] == 2       # voltou com o mesmo QR
    deve_falhar(mesas.definir_quantidade, con, 500)
    assert "<svg" in mesas.qr_svg("http://192.168.0.10:5000/m/abc")
    ok("mesas: códigos sorteados e únicos; trocar código invalida o QR antigo; mesa desativada não abre")

    # --- cliente pede ---
    deve_falhar(pedidos.abrir_comanda_cliente, con, 1, "   ")
    cid = pedidos.abrir_comanda_cliente(con, 1, "  Ana   Paula ")
    c = con.execute("SELECT * FROM comanda WHERE id = ?", (cid,)).fetchone()
    assert c["identificacao"] == "Ana Paula" and c["mesa"] == 1
    assert c["aberta_por"] == pedidos.usuario_qrcode(con)
    ok("cliente abre a comanda com o nome; fica no nome do usuário 'Cliente (QR Code)'")

    antes = movs(con)
    deve_falhar(pedidos.pedir_pelo_qrcode, con, cid, CERV, 0)
    deve_falhar(pedidos.pedir_pelo_qrcode, con, cid, CERV, pedidos.MAX_QTD_CLIENTE + 1)
    item = pedidos.pedir_pelo_qrcode(con, cid, CERV, 2)
    i = con.execute("SELECT * FROM pedido_item WHERE id = ?", (item,)).fetchone()
    assert i["origem"] == "cliente" and i["confirmado_em"] is None and i["status"] == "pedido"
    assert i["preco_unitario"] == 8.0
    assert movs(con) == antes
    ok("pedido do cliente nasce aguardando o garçom, com o preço do sistema, sem mexer no estoque")

    assert [x["id"] for x in pedidos.itens_aguardando(con)] == [item]
    assert item not in [x["id"] for x in pedidos.itens_pendentes(con)]
    msg = deve_falhar(pedidos.entregar_item, con, item, GARCOM)
    assert "confirmado" in msg
    deve_falhar(pedidos.fechar_comanda, con, cid, GARCOM)
    ok("aguardando não aparece em Entregas, não pode ser entregue e impede fechar a comanda")

    # --- garçom confirma ---
    deve_falhar(pedidos.confirmar_item, con, item, pedidos.usuario_qrcode(con))   # usuário de sistema não confirma
    pedidos.confirmar_item(con, item, GARCOM)
    deve_falhar(pedidos.confirmar_item, con, item, GARCOM)
    assert pedidos.itens_aguardando(con) == []
    assert item in [x["id"] for x in pedidos.itens_pendentes(con)]
    deve_falhar(pedidos.cliente_desistir, con, cid, item)
    r = pedidos.entregar_item(con, item, GARCOM)
    assert len(r["movimentacao_ids"]) >= 1
    ok("garçom confirma -> vai para Entregas -> ITEM ENTREGUE dá baixa normalmente")

    equipe = pedidos.adicionar_item(con, cid, CERV, 1, GARCOM)
    assert con.execute("SELECT origem FROM pedido_item WHERE id = ?", (equipe,)).fetchone()[0] == "equipe"
    assert equipe in [x["id"] for x in pedidos.itens_pendentes(con)]
    ok("item lançado pela equipe continua indo direto para Entregas")

    # --- desistir / recusar / limite ---
    outro = pedidos.abrir_comanda_cliente(con, 2, "Beto")
    x = pedidos.pedir_pelo_qrcode(con, outro, CERV, 1)
    deve_falhar(pedidos.cliente_desistir, con, cid, x)              # item de outra comanda
    pedidos.cliente_desistir(con, outro, x)
    assert con.execute("SELECT status FROM pedido_item WHERE id = ?", (x,)).fetchone()[0] == "cancelado"
    y = pedidos.pedir_pelo_qrcode(con, outro, CERV, 1)
    pedidos.cancelar_item(con, y, GARCOM, "Recusado pelo garçom")
    for _ in range(pedidos.MAX_AGUARDANDO_CLIENTE):
        pedidos.pedir_pelo_qrcode(con, outro, CERV, 1)
    msg = deve_falhar(pedidos.pedir_pelo_qrcode, con, outro, CERV, 1)
    assert "Aguarde" in msg
    ok("cliente desiste só do próprio item; garçom recusa; limite de itens esperando evita trote")

    assert "qrcode" not in [u["login"] for u in auth.listar_usuarios(con)]
    assert auth.buscar_usuario(con, pedidos.usuario_qrcode(con)) is None
    deve_falhar(auth.autenticar, con, "qrcode", "DEFINIR_NO_PRIMEIRO_ACESSO")
    ok("o usuário do QR Code não aparece em Usuários e ninguém entra com ele")

    print("OK   (regras do QR Code)")


def testar_telas():
    import app as modulo_app

    bd.ARQUIVO_BANCO = Path(tempfile.mkdtemp()) / "teste.db"
    auth._falhas.clear()
    modulo_app.app.config["TESTING"] = True

    con = bd.conectar()
    auth.configurar_primeiro_acesso(con, "Rafa", "rafa", "segredo1", "segredo1")
    eu = auth.carregar_usuario(con, 1)
    gid = auth.criar_usuario(con, "Wagner", "wagner", "senha1",
                             con.execute("SELECT id FROM perfil WHERE nome = 'Garçom'").fetchone()[0], eu)
    con.execute("UPDATE usuario SET trocar_senha = 0 WHERE id = ?", (gid,))
    con.commit()
    CERV = prod(con, "Cerveja long neck")
    con.close()

    dono = modulo_app.app.test_client()
    rede = "http://192.168.0.50:5000"          # como se o Dono abrisse o sistema pelo IP do PC
    dono.post("/entrar", data={"login": "rafa", "senha": "segredo1"}, base_url=rede)
    dono.post("/mesas", data={"quantidade": "4"}, base_url=rede)
    texto = dono.get("/mesas", base_url=rede).get_data(as_text=True)
    assert texto.count("<svg") == 4 and "http://192.168.0.50:5000/m/" in texto
    con = bd.conectar()
    codigo = con.execute("SELECT codigo FROM mesa WHERE numero = 3").fetchone()[0]
    con.close()
    ok("tela: Dono cadastra 4 mesas e vê os 4 QR Codes com o endereço da rede")

    cliente = modulo_app.app.test_client()
    assert cliente.get("/m/codigo-errado").status_code == 404
    assert cliente.get("/comandas").status_code == 302                # cliente não entra na área da equipe
    assert "Abrir minha comanda" in cliente.get(f"/m/{codigo}").get_data(as_text=True)
    cliente.post(f"/m/{codigo}", data={"nome": "Carla"})
    texto = cliente.get(f"/m/{codigo}").get_data(as_text=True)
    assert "Minha comanda" in texto and "Cerveja long neck" in texto and "Carla" in texto
    r = cliente.post(f"/m/{codigo}/pedir", data={"produto_id": str(CERV), "quantidade": "2"},
                     follow_redirects=True)
    assert "Aguardando garçom" in r.get_data(as_text=True)
    ok("tela: cliente sem login abre a comanda pelo QR, vê o cardápio e pede")

    intruso = modulo_app.app.test_client()
    assert "Abrir minha comanda" in intruso.get(f"/m/{codigo}").get_data(as_text=True)
    ok("tela: outro celular na mesma mesa não vê a comanda da Carla")

    garcom = modulo_app.app.test_client()
    garcom.post("/entrar", data={"login": "wagner", "senha": "senha1"})
    texto = garcom.get("/comandas").get_data(as_text=True)
    assert "CONFIRMAR" in texto and "Mesa 3" in texto and '<span class="contador"' in texto
    assert "Cerveja long neck" not in garcom.get("/entregas").get_data(as_text=True)
    con = bd.conectar()
    item = con.execute("SELECT id FROM pedido_item WHERE origem = 'cliente'").fetchone()[0]
    con.close()
    garcom.post(f"/itens/{item}/confirmar")
    assert "Cerveja long neck" in garcom.get("/entregas").get_data(as_text=True)
    assert "Confirmado" in cliente.get(f"/m/{codigo}").get_data(as_text=True)
    ok("tela: garçom vê o aviso, confirma, o item vai para Entregas e o cliente vê 'Confirmado'")

    assert garcom.get("/mesas").status_code == 403
    con = bd.conectar()
    con.execute("UPDATE comanda SET status = 'fechada'")
    con.commit()
    con.close()
    assert "Abrir minha comanda" in cliente.get(f"/m/{codigo}").get_data(as_text=True)
    ok("tela: Garçom não gerencia mesas; comanda fechada pelo caixa -> cliente abre outra")

    print("OK   (telas)")


if __name__ == "__main__":
    testar_regras()
    testar_telas()
    print("\nTUDO CERTO")
