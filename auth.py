"""Login, senhas, usuários, perfis e permissões.

Ideias principais:
- A senha NUNCA é guardada. Guardamos só um "hash" (uma impressão digital da senha,
  gerada pelo werkzeug, que já vem com o Flask). No login, calculamos o hash do que a
  pessoa digitou e comparamos.
- Cada usuário tem um perfil (Dono, Gerente, Caixa, Bartender, Garçom) e cada perfil
  tem uma lista de permissões (ex.: 'estoque.movimentar'). O Dono sempre pode tudo.
- Sempre precisa existir pelo menos um Dono ativo, para ninguém ficar trancado fora.

Como no resto do projeto, erros de uso viram ErroDeRegra e nada é gravado pela metade.
"""
import re
import time

from werkzeug.security import check_password_hash, generate_password_hash

from regras import ErroDeRegra

PERFIL_DONO = "Dono"
SENHA_NAO_DEFINIDA = "DEFINIR_NO_PRIMEIRO_ACESSO"   # valor que o seed coloca no usuário admin
TAMANHO_MINIMO_SENHA = 4                             # 4 permite um PIN no tablet; o Dono deve usar mais

# Proteção contra "chutar" senhas: depois de 5 erros seguidos, o login fica bloqueado 5 minutos.
MAX_TENTATIVAS = 5
BLOQUEIO_SEGUNDOS = 5 * 60
_falhas = {}   # login -> [erros_seguidos, bloqueado_ate]  (fica na memória; zera ao reiniciar)


# ---------------------------------------------------------------------
# Validações
# ---------------------------------------------------------------------

def _normalizar_login(login):
    login = (login or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{2,30}", login):
        raise ErroDeRegra("O login deve ter de 2 a 30 caracteres: letras sem acento, números, ponto, hífen ou _.")
    return login


def _validar_nome(nome):
    nome = (nome or "").strip()
    if not nome:
        raise ErroDeRegra("Informe o nome da pessoa.")
    return nome


def validar_senha(senha, confirmacao=None):
    if senha is None or len(senha) < TAMANHO_MINIMO_SENHA:
        raise ErroDeRegra(f"A senha deve ter pelo menos {TAMANHO_MINIMO_SENHA} caracteres.")
    if senha != senha.strip():
        raise ErroDeRegra("A senha não pode começar nem terminar com espaço.")
    if confirmacao is not None and senha != confirmacao:
        raise ErroDeRegra("A confirmação não confere com a senha.")


def _senha_confere(senha_hash, senha):
    if not senha_hash or senha_hash == SENHA_NAO_DEFINIDA:
        return False
    try:
        return check_password_hash(senha_hash, senha or "")
    except ValueError:      # hash em formato estranho: trata como senha errada
        return False


def _perfil(con, perfil_id):
    p = con.execute("SELECT id, nome FROM perfil WHERE id = ?", (perfil_id,)).fetchone()
    if p is None:
        raise ErroDeRegra("Escolha um perfil válido.")
    return p


def _usuario(con, usuario_id):
    u = con.execute(
        """SELECT u.*, p.nome AS perfil FROM usuario u JOIN perfil p ON p.id = u.perfil_id
           WHERE u.id = ?""", (usuario_id,)).fetchone()
    if u is None:
        raise ErroDeRegra("Usuário não encontrado.")
    return u


def _e_dono(quem):
    return quem is not None and quem["perfil"] == PERFIL_DONO


def _donos_ativos(con, exceto_id=None):
    return con.execute(
        """SELECT COUNT(*) FROM usuario u JOIN perfil p ON p.id = u.perfil_id
           WHERE p.nome = ? AND u.ativo = 1 AND u.senha_hash <> ? AND u.id IS NOT ?""",
        (PERFIL_DONO, SENHA_NAO_DEFINIDA, exceto_id)).fetchone()[0]


# ---------------------------------------------------------------------
# Primeiro acesso: o sistema ainda não tem nenhum Dono com senha
# ---------------------------------------------------------------------

def precisa_primeiro_acesso(con):
    return _donos_ativos(con) == 0


def configurar_primeiro_acesso(con, nome, login, senha, confirmacao):
    """Cria (ou completa) o usuário Dono. Só funciona enquanto não existir um Dono com senha."""
    if not precisa_primeiro_acesso(con):
        raise ErroDeRegra("O sistema já foi configurado. Entre com seu usuário.")
    nome, login = _validar_nome(nome), _normalizar_login(login)
    validar_senha(senha, confirmacao)
    dono = con.execute("SELECT id FROM perfil WHERE nome = ?", (PERFIL_DONO,)).fetchone()
    if dono is None:
        raise ErroDeRegra("O perfil Dono não existe no banco.")
    # se o seed criou o "admin" sem senha, aproveitamos esse usuário (as movimentações
    # fictícias estão no nome dele); senão, criamos um novo
    existente = con.execute(
        "SELECT id FROM usuario WHERE senha_hash = ? AND perfil_id = ? ORDER BY id LIMIT 1",
        (SENHA_NAO_DEFINIDA, dono["id"])).fetchone()
    outro = con.execute("SELECT id FROM usuario WHERE login = ?", (login,)).fetchone()
    if outro and (existente is None or outro["id"] != existente["id"]):
        raise ErroDeRegra(f"Já existe um usuário com o login {login!r}.")
    with con:
        if existente:
            con.execute("UPDATE usuario SET nome=?, login=?, senha_hash=?, ativo=1, trocar_senha=0, "
                        "ultimo_acesso=datetime('now','localtime') WHERE id=?",
                        (nome, login, generate_password_hash(senha), existente["id"]))
            return existente["id"]
        cur = con.execute("INSERT INTO usuario (nome, login, senha_hash, perfil_id, ultimo_acesso) "
                          "VALUES (?, ?, ?, ?, datetime('now','localtime'))",
                          (nome, login, generate_password_hash(senha), dono["id"]))
        return cur.lastrowid


# ---------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------

def autenticar(con, login, senha, agora=None):
    """Confere login e senha. Devolve o id do usuário ou levanta ErroDeRegra."""
    agora = time.monotonic() if agora is None else agora
    chave = (login or "").strip().lower()
    erros, bloqueado_ate = _falhas.get(chave, (0, 0))
    if agora < bloqueado_ate:
        minutos = int((bloqueado_ate - agora) // 60) + 1
        raise ErroDeRegra(f"Muitas tentativas erradas. Tente de novo em {minutos} minuto(s).")

    u = con.execute("SELECT id, senha_hash, ativo FROM usuario WHERE login = ?", (chave,)).fetchone()
    if u is None or not u["ativo"] or not _senha_confere(u["senha_hash"], senha):
        erros += 1
        _falhas[chave] = (0, agora + BLOQUEIO_SEGUNDOS) if erros >= MAX_TENTATIVAS else (erros, 0)
        # mesma mensagem para "não existe" e "senha errada": não entrega quais logins existem
        raise ErroDeRegra("Usuário ou senha incorretos.")

    _falhas.pop(chave, None)
    with con:
        con.execute("UPDATE usuario SET ultimo_acesso = datetime('now', 'localtime') WHERE id = ?", (u["id"],))
    return u["id"]


def carregar_usuario(con, usuario_id):
    """Dados do usuário logado + conjunto de permissões. None se não existir ou estiver inativo."""
    if usuario_id is None:
        return None
    u = con.execute(
        """SELECT u.id, u.nome, u.login, u.perfil_id, u.trocar_senha, p.nome AS perfil
           FROM usuario u JOIN perfil p ON p.id = u.perfil_id
           WHERE u.id = ? AND u.ativo = 1 AND u.senha_hash <> ?""",
        (usuario_id, SENHA_NAO_DEFINIDA)).fetchone()
    if u is None:
        return None
    dados = dict(u)
    dados["permissoes"] = permissoes_do_perfil(con, u["perfil_id"], u["perfil"])
    return dados


def permissoes_do_perfil(con, perfil_id, nome_perfil=None):
    if nome_perfil == PERFIL_DONO:   # o Dono sempre pode tudo
        return {r[0] for r in con.execute("SELECT codigo FROM permissao")}
    return {r[0] for r in con.execute(
        """SELECT m.codigo FROM perfil_permissao pp JOIN permissao m ON m.id = pp.permissao_id
           WHERE pp.perfil_id = ?""", (perfil_id,))}


def tem_permissao(usuario, *codigos):
    """True se o usuário tem PELO MENOS UMA das permissões pedidas."""
    return usuario is not None and any(c in usuario["permissoes"] for c in codigos)


# ---------------------------------------------------------------------
# Senhas
# ---------------------------------------------------------------------

def trocar_propria_senha(con, usuario_id, senha_atual, nova, confirmacao):
    u = _usuario(con, usuario_id)
    if not _senha_confere(u["senha_hash"], senha_atual):
        raise ErroDeRegra("A senha atual está incorreta.")
    validar_senha(nova, confirmacao)
    if nova == senha_atual:
        raise ErroDeRegra("A nova senha deve ser diferente da atual.")
    with con:
        con.execute("UPDATE usuario SET senha_hash = ?, trocar_senha = 0 WHERE id = ?",
                    (generate_password_hash(nova), usuario_id))


def redefinir_senha(con, usuario_id, nova, quem):
    """O gestor define uma senha provisória; a pessoa troca no próximo login."""
    alvo = _usuario(con, usuario_id)
    _checar_pode_mexer(alvo["perfil"], quem)
    validar_senha(nova)
    with con:
        con.execute("UPDATE usuario SET senha_hash = ?, trocar_senha = 1 WHERE id = ?",
                    (generate_password_hash(nova), usuario_id))
    _falhas.pop(alvo["login"], None)   # desbloqueia, se estava bloqueado


# ---------------------------------------------------------------------
# Usuários
# ---------------------------------------------------------------------

def _checar_pode_mexer(nome_perfil_alvo, quem):
    """Só um Dono cria, edita ou define senha de outro Dono (evita alguém se promover)."""
    if nome_perfil_alvo == PERFIL_DONO and not _e_dono(quem):
        raise ErroDeRegra("Só o Dono pode criar ou alterar usuários com perfil Dono.")


def listar_usuarios(con):
    return con.execute(
        """SELECT u.id, u.nome, u.login, u.ativo, u.trocar_senha, u.ultimo_acesso, u.perfil_id,
                  p.nome AS perfil, (u.senha_hash = ?) AS sem_senha
           FROM usuario u JOIN perfil p ON p.id = u.perfil_id
           ORDER BY u.ativo DESC, p.id, u.nome""", (SENHA_NAO_DEFINIDA,)).fetchall()


def buscar_usuario(con, usuario_id):
    return con.execute(
        """SELECT u.id, u.nome, u.login, u.ativo, u.perfil_id, p.nome AS perfil
           FROM usuario u JOIN perfil p ON p.id = u.perfil_id WHERE u.id = ?""", (usuario_id,)).fetchone()


def criar_usuario(con, nome, login, senha, perfil_id, quem):
    """Cria um usuário com senha provisória (ele troca no primeiro login). Devolve o id."""
    nome, login = _validar_nome(nome), _normalizar_login(login)
    perfil = _perfil(con, perfil_id)
    _checar_pode_mexer(perfil["nome"], quem)
    validar_senha(senha)
    if con.execute("SELECT 1 FROM usuario WHERE login = ?", (login,)).fetchone():
        raise ErroDeRegra(f"Já existe um usuário com o login {login!r}.")
    with con:
        cur = con.execute(
            "INSERT INTO usuario (nome, login, senha_hash, perfil_id, trocar_senha) VALUES (?, ?, ?, ?, 1)",
            (nome, login, generate_password_hash(senha), perfil["id"]))
    return cur.lastrowid


def editar_usuario(con, usuario_id, nome, perfil_id, ativo, quem):
    alvo = _usuario(con, usuario_id)
    nome, novo_perfil = _validar_nome(nome), _perfil(con, perfil_id)
    ativo = 1 if ativo else 0
    _checar_pode_mexer(alvo["perfil"], quem)          # mexer em quem já é Dono
    _checar_pode_mexer(novo_perfil["nome"], quem)     # promover alguém a Dono

    if quem is not None and quem["id"] == usuario_id:
        if not ativo:
            raise ErroDeRegra("Você não pode desativar o seu próprio usuário.")
        if novo_perfil["id"] != alvo["perfil_id"]:
            raise ErroDeRegra("Você não pode mudar o seu próprio perfil. Peça a outro Dono.")

    deixa_de_ser_dono_ativo = (alvo["perfil"] == PERFIL_DONO and alvo["ativo"]
                               and (novo_perfil["nome"] != PERFIL_DONO or not ativo))
    if deixa_de_ser_dono_ativo and _donos_ativos(con, exceto_id=usuario_id) == 0:
        raise ErroDeRegra("Precisa existir pelo menos um Dono ativo. Cadastre outro Dono antes.")

    with con:
        con.execute("UPDATE usuario SET nome = ?, perfil_id = ?, ativo = ? WHERE id = ?",
                    (nome, novo_perfil["id"], ativo, usuario_id))


# ---------------------------------------------------------------------
# Perfis e permissões
# ---------------------------------------------------------------------

def listar_permissoes(con):
    return con.execute("SELECT id, codigo, descricao FROM permissao ORDER BY id").fetchall()


def listar_perfis(con):
    """Perfis com o conjunto de códigos de permissão de cada um."""
    perfis = []
    for p in con.execute("SELECT id, nome FROM perfil ORDER BY id"):
        d = dict(p)
        d["permissoes"] = permissoes_do_perfil(con, p["id"], p["nome"])
        d["fixo"] = p["nome"] == PERFIL_DONO
        perfis.append(d)
    return perfis


def definir_permissoes_perfil(con, perfil_id, codigos, quem):
    """Troca a lista de permissões de um perfil. Só o Dono faz; o perfil Dono não é editável."""
    if not _e_dono(quem):
        raise ErroDeRegra("Só o Dono pode mudar as permissões dos perfis.")
    perfil = _perfil(con, perfil_id)
    if perfil["nome"] == PERFIL_DONO:
        raise ErroDeRegra("O perfil Dono sempre tem todas as permissões.")
    validos = {r["codigo"]: r["id"] for r in listar_permissoes(con)}
    desconhecidos = set(codigos) - set(validos)
    if desconhecidos:
        raise ErroDeRegra(f"Permissão desconhecida: {', '.join(sorted(desconhecidos))}")
    with con:
        con.execute("DELETE FROM perfil_permissao WHERE perfil_id = ?", (perfil["id"],))
        con.executemany("INSERT INTO perfil_permissao (perfil_id, permissao_id) VALUES (?, ?)",
                        [(perfil["id"], validos[c]) for c in set(codigos)])
