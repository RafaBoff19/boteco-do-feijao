"""Testes de login, usuários, perfis e permissões. Usa banco TEMPORÁRIO em memória.

Uso:  python teste_login.py

A parte final testa as telas de verdade (pelo cliente de teste do Flask), também
em um banco temporário: o estoque.db não é tocado.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

import auth
import bd
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


def perfil_id(con, nome):
    return con.execute("SELECT id FROM perfil WHERE nome = ?", (nome,)).fetchone()[0]


def testar_regras():
    con = banco_de_teste()
    auth._falhas.clear()

    # --- primeiro acesso ---
    assert auth.precisa_primeiro_acesso(con)
    deve_falhar(auth.autenticar, con, "admin", "DEFINIR_NO_PRIMEIRO_ACESSO")
    ok("admin do seed sem senha não entra (nem com o texto provisório)")

    deve_falhar(auth.configurar_primeiro_acesso, con, "Rafa", "rafa", "12", "12")
    deve_falhar(auth.configurar_primeiro_acesso, con, "Rafa", "rafa", "segredo1", "outra")
    dono = auth.configurar_primeiro_acesso(con, "Rafa", "Rafa", "segredo1", "segredo1")
    assert dono == 1, "deveria aproveitar o admin do seed (id 1)"
    assert not auth.precisa_primeiro_acesso(con)
    deve_falhar(auth.configurar_primeiro_acesso, con, "Outro", "outro", "segredo1", "segredo1")
    ok("primeiro acesso cria o Dono (reaproveita o admin) e não pode ser repetido")

    hash_gravado = con.execute("SELECT senha_hash FROM usuario WHERE id = 1").fetchone()[0]
    assert "segredo1" not in hash_gravado
    ok("a senha não fica gravada pura no banco")

    # --- login ---
    assert auth.autenticar(con, " RAFA ", "segredo1") == dono      # login não diferencia maiúsculas
    m1 = deve_falhar(auth.autenticar, con, "rafa", "errada")
    m2 = deve_falhar(auth.autenticar, con, "naoexiste", "errada")
    assert m1 == m2
    ok("login certo entra; erro não revela se o usuário existe")

    for _ in range(auth.MAX_TENTATIVAS - 1):
        deve_falhar(auth.autenticar, con, "rafa", "errada", agora=1000)
    msg = deve_falhar(auth.autenticar, con, "rafa", "segredo1", agora=1001)
    assert "tentativas" in msg, msg
    assert auth.autenticar(con, "rafa", "segredo1", agora=1000 + auth.BLOQUEIO_SEGUNDOS + 1) == dono
    ok("5 erros seguidos bloqueiam o login por 5 minutos")

    eu = auth.carregar_usuario(con, dono)
    todas = {r[0] for r in con.execute("SELECT codigo FROM permissao")}
    assert eu["permissoes"] == todas
    ok("o Dono tem todas as permissões")

    # --- usuários ---
    gid = auth.criar_usuario(con, "Gina", "gina", "1234", perfil_id(con, "Gerente"), eu)
    wid = auth.criar_usuario(con, "Wagner", "wagner", "4321", perfil_id(con, "Garçom"), eu)
    deve_falhar(auth.criar_usuario, con, "Gina 2", "GINA", "1234", perfil_id(con, "Caixa"), eu)
    deve_falhar(auth.criar_usuario, con, "X", "x y", "1234", perfil_id(con, "Caixa"), eu)
    ok("cria usuários; login repetido ou inválido é recusado")

    garcom = auth.carregar_usuario(con, auth.autenticar(con, "wagner", "4321"))
    assert garcom["trocar_senha"] == 1
    deve_falhar(auth.trocar_propria_senha, con, wid, "errada", "nova123", "nova123")
    auth.trocar_propria_senha(con, wid, "4321", "nova123", "nova123")
    assert auth.carregar_usuario(con, wid)["trocar_senha"] == 0
    ok("usuário novo entra com senha provisória e é obrigado a trocar")

    assert auth.tem_permissao(garcom, "pedido.registrar")
    assert not auth.tem_permissao(garcom, "estoque.ver", "usuario.gerir")
    ok("Garçom registra pedido mas não vê estoque nem usuários")

    gerente = auth.carregar_usuario(con, gid)
    assert "pedido.estornar" in gerente["permissoes"]
    deve_falhar(auth.criar_usuario, con, "Dono 2", "dono2", "1234", perfil_id(con, "Dono"), gerente)
    deve_falhar(auth.editar_usuario, con, dono, "Rafa", perfil_id(con, "Gerente"), True, gerente)
    deve_falhar(auth.redefinir_senha, con, dono, "abcd", gerente)
    deve_falhar(auth.editar_usuario, con, wid, "Wagner", perfil_id(con, "Dono"), True, gerente)
    ok("quem não é Dono não cria, promove, edita nem troca senha de um Dono")

    deve_falhar(auth.editar_usuario, con, dono, "Rafa", perfil_id(con, "Dono"), False, eu)
    deve_falhar(auth.editar_usuario, con, dono, "Rafa", perfil_id(con, "Gerente"), True, eu)
    ok("ninguém desativa nem rebaixa a si mesmo")

    d2 = auth.criar_usuario(con, "Sócia", "socia", "abcd", perfil_id(con, "Dono"), eu)
    auth.trocar_propria_senha(con, d2, "abcd", "socia123", "socia123")
    socia = auth.carregar_usuario(con, d2)
    auth.editar_usuario(con, dono, "Rafa", perfil_id(con, "Dono"), False, socia)    # agora pode: sobra a sócia
    deve_falhar(auth.editar_usuario, con, d2, "Sócia", perfil_id(con, "Gerente"), True, socia)
    assert auth.carregar_usuario(con, dono) is None
    deve_falhar(auth.autenticar, con, "rafa", "segredo1")
    auth.editar_usuario(con, dono, "Rafa", perfil_id(con, "Dono"), True, socia)
    ok("sempre sobra um Dono ativo; usuário desativado não entra")

    auth.redefinir_senha(con, wid, "temp", eu)
    assert auth.autenticar(con, "wagner", "temp") == wid
    assert auth.carregar_usuario(con, wid)["trocar_senha"] == 1
    ok("senha provisória definida pelo gestor obriga a trocar de novo")

    # --- permissões configuráveis ---
    garcom_id = perfil_id(con, "Garçom")
    deve_falhar(auth.definir_permissoes_perfil, con, garcom_id, ["estoque.ver"], gerente)
    deve_falhar(auth.definir_permissoes_perfil, con, perfil_id(con, "Dono"), [], eu)
    deve_falhar(auth.definir_permissoes_perfil, con, garcom_id, ["voar"], eu)
    auth.definir_permissoes_perfil(con, garcom_id, ["pedido.registrar", "pedido.entregar", "estoque.ver"], eu)
    assert "estoque.ver" in auth.carregar_usuario(con, wid)["permissoes"]
    ok("só o Dono muda permissões; perfil Dono é fixo; vale na hora")

    print("OK   (regras de login)")


def testar_telas():
    """Usa o app de verdade, apontando para um banco temporário."""
    import app as modulo_app

    pasta = Path(tempfile.mkdtemp())
    bd.ARQUIVO_BANCO = pasta / "teste.db"
    auth._falhas.clear()
    modulo_app.app.config["TESTING"] = True
    web = modulo_app.app.test_client()

    r = web.get("/")
    assert r.status_code == 302 and "/primeiro-acesso" in r.location
    r = web.post("/primeiro-acesso", data={"nome": "Rafa", "login": "rafa", "senha": "segredo1", "confirmacao": "segredo1"})
    assert r.status_code == 302 and "/usuarios" in r.location
    ok("tela: banco novo pede o primeiro acesso e já entra como Dono")

    con = bd.conectar()
    eu = auth.carregar_usuario(con, 1)
    auth.criar_usuario(con, "Wagner", "wagner", "4321", perfil_id(con, "Garçom"), eu)
    auth.criar_usuario(con, "Bia", "bia", "4321", perfil_id(con, "Bartender"), eu)
    con.close()

    web.post("/sair")
    r = web.get("/historico")
    assert r.status_code == 302 and "/entrar" in r.location and "proximo=" in r.location
    ok("tela: sem login, qualquer página manda para Entrar")

    r = web.post("/entrar", data={"login": "wagner", "senha": "4321"})
    assert "/minha-conta" in web.get("/comandas").location
    web.post("/minha-conta", data={"senha_atual": "4321", "senha": "w12345", "confirmacao": "w12345"})
    r = web.get("/")
    assert r.status_code == 302 and "/comandas" in r.location
    assert web.get("/comandas").status_code == 200
    assert web.get("/historico").status_code == 403
    assert web.get("/usuarios").status_code == 403
    assert web.post("/movimentar", data={"tipo": "entrada"}).status_code == 403
    texto = web.get("/comandas").get_data(as_text=True)
    assert "Wagner" in texto and ">Estoque<" not in texto and "Usuários" not in texto
    ok("tela: Garçom troca a senha, cai em Comandas e é barrado no estoque e nos usuários")

    r = web.post("/comandas", data={"identificacao": "Mesa do João"})
    assert r.status_code == 302
    con = bd.conectar()
    aberta_por = con.execute("SELECT aberta_por FROM comanda ORDER BY id DESC LIMIT 1").fetchone()[0]
    wid = con.execute("SELECT id FROM usuario WHERE login = 'wagner'").fetchone()[0]
    con.close()
    assert aberta_por == wid
    ok("tela: a comanda fica no nome de quem está logado (não mais do Administrador)")

    web.post("/sair")
    web.post("/entrar", data={"login": "bia", "senha": "4321"})
    web.post("/minha-conta", data={"senha_atual": "4321", "senha": "b12345", "confirmacao": "b12345"})
    texto = web.get("/movimentar").get_data(as_text=True)
    assert 'value="perda"' in texto and 'value="entrada"' not in texto
    r = web.post("/movimentar", data={"tipo": "entrada", "insumo": "1", "quantidade": "1"})
    assert "não pode registrar entrada" in r.get_data(as_text=True)
    ok("tela: Bartender só vê e só consegue registrar perda")

    web.post("/sair")
    for _ in range(auth.MAX_TENTATIVAS):
        web.post("/entrar", data={"login": "rafa", "senha": "errada"})
    r = web.post("/entrar", data={"login": "rafa", "senha": "segredo1"})
    assert "tentativas" in r.get_data(as_text=True)
    ok("tela: bloqueio depois de várias senhas erradas")

    r = web.get("/entrar?proximo=//site-malicioso.com")
    assert "site-malicioso" not in r.get_data(as_text=True)
    ok("tela: o 'voltar depois do login' não aceita endereço de outro site")

    print("OK   (telas)")


if __name__ == "__main__":
    testar_regras()
    testar_telas()
    print("\nTUDO CERTO")
