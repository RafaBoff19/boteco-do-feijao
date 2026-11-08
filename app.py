"""Interface web do sistema de estoque do Buteco do Feijão.

Uso:   python app.py
Abre:  http://localhost:5000   (no próprio PC)
       http://<IP-do-PC>:5000  (em tablets e celulares ligados no mesmo Wi-Fi)

Funciona sem internet: só precisa que os aparelhos estejam na mesma rede do bar.
"""
import re
import secrets
import socket
import sqlite3
from datetime import timedelta
from functools import wraps

from flask import Flask, abort, flash, g, make_response, redirect, render_template, request, session, url_for

import auth
import bd
import cadastros
import compras
import mesas
import pedidos
import regras
import reposicao
from regras import ErroDeRegra

app = Flask(__name__)


def _chave_secreta():
    """A chave que "assina" o cookie de login. Se alguém souber a chave, consegue se passar
    por qualquer usuário; por isso ela é sorteada na primeira vez e guardada num arquivo
    fora do git (chave_secreta.txt). Guardar em arquivo faz o login sobreviver a reinícios."""
    arquivo = bd.PASTA / "chave_secreta.txt"
    if not arquivo.exists():
        arquivo.write_text(secrets.token_hex(32), encoding="utf-8")
    return arquivo.read_text(encoding="utf-8").strip()


app.secret_key = _chave_secreta()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,        # o JavaScript da página não lê o cookie
    SESSION_COOKIE_SAMESITE="Lax",       # outro site não consegue enviar formulários "em nome" do usuário
    PERMANENT_SESSION_LIFETIME=timedelta(hours=16),   # um turno inteiro (18h–03h) com folga
)

TIPOS = {
    "entrada": "Entrada",
    "saida": "Saída",
    "perda": "Perda",
    "ajuste": "Ajuste",
    "transferencia": "Transferência",
    "baixa_pedido": "Baixa de pedido",
    "estorno": "Estorno",
}


# ---------------------------------------------------------------------
# Banco: uma conexão por requisição, fechada no final
# ---------------------------------------------------------------------

def con():
    if "con" not in g:
        g.con = bd.conectar()
    return g.con


@app.teardown_appcontext
def fechar_conexao(_erro):
    c = g.pop("con", None)
    if c is not None:
        c.close()


# ---------------------------------------------------------------------
# Login: quem está usando o sistema e o que pode fazer
# ---------------------------------------------------------------------

# páginas que abrem sem estar logado
PAGINAS_PUBLICAS = {"login", "primeiro_acesso", "static",
                    "cliente_mesa", "cliente_pedir", "cliente_desistir"}   # cardápio do QR Code
# páginas liberadas para quem precisa trocar a senha antes de continuar
PAGINAS_TROCA_SENHA = {"minha_conta", "logout", "static"}


def uid():
    """Id do usuário logado (é ele que fica registrado nas movimentações e pedidos)."""
    return g.usuario["id"]


def pode(*codigos):
    """Usado no Python e nos templates: o usuário logado tem alguma destas permissões?"""
    return auth.tem_permissao(g.get("usuario"), *codigos)


def requer(*codigos):
    """Decorador: a tela só abre para quem tem pelo menos uma das permissões."""
    def decorador(funcao):
        @wraps(funcao)
        def envelope(*args, **kwargs):
            if not pode(*codigos):
                abort(403)
            return funcao(*args, **kwargs)
        return envelope
    return decorador


def pagina_inicial():
    """Primeira tela que o perfil pode ver (o Garçom, por exemplo, não vê o estoque)."""
    for permissoes, tela in [(("estoque.ver",), "estoque"),
                             (("pedido.registrar", "caixa.operar"), "comandas"),
                             (("pedido.entregar",), "entregas"),
                             (("produto.gerir", "fornecedor.gerir"), "cadastros_inicio"),
                             (("usuario.gerir",), "usuarios"),
                             (("mesa.gerir",), "mesas_lista"),
                             (("reposicao.pedir", "reposicao.atender"), "reposicoes_lista")]:
        if pode(*permissoes):
            return url_for(tela)
    return url_for("minha_conta")


@app.before_request
def conferir_login():
    g.usuario = None
    if request.endpoint in PAGINAS_PUBLICAS:
        return None
    c = con()
    if auth.precisa_primeiro_acesso(c):
        return redirect(url_for("primeiro_acesso"))
    g.usuario = auth.carregar_usuario(c, session.get("usuario_id"))
    if g.usuario is None:                 # não logou, ou foi desativado enquanto usava
        session.clear()
        destino = request.full_path if request.method == "GET" else None
        return redirect(url_for("login", proximo=destino))
    if g.usuario["trocar_senha"] and request.endpoint not in PAGINAS_TROCA_SENHA:
        flash("Crie uma senha nova para continuar.", "aviso")
        return redirect(url_for("minha_conta"))
    return None


@app.context_processor
def para_os_templates():
    return {"usuario": g.get("usuario"), "pode": pode, "compras_aviso": _compras_aviso(),
            "qr_aviso": _qr_aviso(), "reposicao_aviso": _reposicao_aviso()}


def _reposicao_aviso():
    """Número no menu Reposição: pedidos do Bar esperando resposta do Estoque."""
    if g.get("usuario") is None or not pode("reposicao.atender"):
        return 0
    return reposicao.contar_pendentes(con())


def _qr_aviso():
    """Número no menu Comandas: pedidos do QR Code esperando um garçom confirmar."""
    if g.get("usuario") is None or not pode("pedido.registrar"):
        return 0
    return len(pedidos.itens_aguardando(con()))


def _compras_aviso():
    """Número que aparece no menu Compras: o que espera por VOCÊ (aprovar ou receber)."""
    if g.get("usuario") is None or not pode("compra.aprovar", "estoque.movimentar"):
        return 0
    n = compras.contar_pendencias(con())
    return (n["pendente"] if pode("compra.aprovar") else 0) + (n["aprovada"] if pode("estoque.movimentar") else 0)


@app.errorhandler(403)
def sem_permissao(_erro):
    return render_template("sem_permissao.html", inicio=pagina_inicial()), 403


def _destino_seguro(texto):
    """Só aceita voltar para um caminho interno do sistema (evita redirecionar para outro site)."""
    texto = (texto or "").strip()
    if texto.startswith("/") and not texto.startswith("//") and "\\" not in texto:
        return texto
    return None


# ---------------------------------------------------------------------
# Formatação para o template (12000 -> 12.000, 1.5 -> 1,5)
# ---------------------------------------------------------------------

@app.template_filter("br")
def br(valor, casas=2):
    if valor is None:
        return "–"
    texto = f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if "," in texto:
        texto = texto.rstrip("0").rstrip(",")
    return texto


@app.template_filter("inp")
def inp(valor):
    """Valor para dentro de um campo de texto: 1500.0 -> '1500' ; 1.5 -> '1,5' (sem ponto de milhar)."""
    if valor is None or valor == "":
        return ""
    if isinstance(valor, str):
        return valor
    return format(valor, "f").rstrip("0").rstrip(".").replace(".", ",")


@app.template_filter("moeda")
def moeda(valor):
    if valor is None:
        return "–"
    return "R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@app.template_filter("tipo_nome")
def tipo_nome(tipo):
    return TIPOS.get(tipo, tipo)


def _numero(texto):
    """Lê números no jeito brasileiro: '1,5' e '1.5' = um e meio; '1.500' e '1.500,5' = mil e quinhentos."""
    texto = (texto or "").strip()
    if not texto:
        raise ErroDeRegra("Informe a quantidade.")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"[1-9]\d{0,2}(\.\d{3})+", texto):   # 1.500 / 12.000: ponto de milhar (0.250 continua sendo 0,25)
        texto = texto.replace(".", "")
    try:
        return float(texto)
    except ValueError:
        raise ErroDeRegra(f"Número inválido: {texto!r}")


def _numero_opcional(texto):
    return _numero(texto) if (texto or "").strip() else None


def _int_ou_none(texto):
    return int(texto) if (texto or "").strip() else None


# ---------------------------------------------------------------------
# Telas
# ---------------------------------------------------------------------

@app.route("/")
def estoque():
    if not pode("estoque.ver"):           # ex.: Garçom cai direto nas comandas
        return redirect(pagina_inicial())
    c = con()
    setores = [r["nome"] for r in c.execute("SELECT nome FROM setor ORDER BY id")]
    saldos = {(r["insumo_id"], r["setor"]): r["quantidade"] for r in c.execute(
        """SELECT s.insumo_id, st.nome AS setor, s.quantidade
           FROM saldo_estoque s JOIN setor st ON st.id = s.setor_id""")}

    resumo = c.execute(
        """SELECT i.id, i.nome, COALESCE(cat.nome, 'Sem categoria') AS categoria,
                  i.unidade_consumo, i.unidade_compra, i.fator_compra,
                  v.saldo_total, v.minimo_efetivo
           FROM insumo i
           JOIN v_saldo_total v ON v.insumo_id = i.id
           LEFT JOIN categoria cat ON cat.id = i.categoria_id
           ORDER BY categoria, i.nome""").fetchall()

    grupos = {}
    for r in resumo:
        por_setor = [(s, saldos.get((r["id"], s), 0.0)) for s in setores]
        minimo = r["minimo_efetivo"]
        if any(v < 0 for _, v in por_setor):
            status, texto = "negativo", "Negativo"
        elif minimo is not None and 0 <= r["saldo_total"] <= minimo:
            status, texto = "baixo", "Estoque baixo"
        else:
            status, texto = "ok", "OK"
        grupos.setdefault(r["categoria"], []).append({
            "nome": r["nome"],
            "unidade": r["unidade_consumo"],
            "total": r["saldo_total"],
            "em_compra": r["saldo_total"] / r["fator_compra"],
            "unidade_compra": r["unidade_compra"],
            "por_setor": por_setor,
            "minimo": minimo,
            "status": status,
            "status_texto": texto,
        })

    return render_template("estoque.html", grupos=grupos, alertas=regras.alertas_pendentes(c))


@app.post("/alertas/<int:alerta_id>/lido")
@requer("estoque.ver")
def alerta_lido(alerta_id):
    try:
        regras.marcar_alerta_lido(con(), alerta_id, uid())
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(url_for("estoque"))


def _dados_do_formulario():
    c = con()
    return {
        "insumos": c.execute(
            """SELECT i.id, i.nome, i.unidade_consumo, i.unidade_compra, i.fator_compra,
                      COALESCE(cat.nome, 'Sem categoria') AS categoria
               FROM insumo i LEFT JOIN categoria cat ON cat.id = i.categoria_id
               WHERE i.ativo = 1 ORDER BY categoria, i.nome""").fetchall(),
        "setores": [r["nome"] for r in c.execute("SELECT nome FROM setor ORDER BY id")],
        "fornecedores": c.execute("SELECT id, nome FROM fornecedor WHERE ativo = 1 ORDER BY nome").fetchall(),
        "tipos_permitidos": [t for t in ("entrada", "perda", "saida", "transferencia", "ajuste")
                             if pode(_permissao_do_tipo(t))],
    }


def _permissao_do_tipo(tipo):
    """Perda tem permissão própria (o Bartender registra perda, mas não dá entrada)."""
    return "estoque.perda" if tipo == "perda" else "estoque.movimentar"


@app.route("/movimentar", methods=["GET", "POST"])
@requer("estoque.movimentar", "estoque.perda")
def movimentar():
    if request.method == "GET":
        return render_template("movimentar.html", form={}, **_dados_do_formulario())

    f = request.form
    c = con()
    try:
        tipo = f.get("tipo", "")
        if tipo in TIPOS and not pode(_permissao_do_tipo(tipo)):
            raise ErroDeRegra(f"Seu perfil não pode registrar {TIPOS[tipo].lower()}.")
        if not f.get("insumo"):
            raise ErroDeRegra("Escolha o insumo.")
        insumo = int(f["insumo"])
        quantidade = _numero(f.get("quantidade"))
        unidade = f.get("unidade", "consumo")
        setor = f.get("setor") or "Estoque"
        motivo = (f.get("motivo") or "").strip() or None
        fornecedor = int(f["fornecedor"]) if f.get("fornecedor") else None

        if tipo == "entrada":
            r = regras.registrar_entrada(c, insumo, quantidade, uid(),
                                         fornecedor_id=fornecedor, setor=setor,
                                         unidade=unidade, motivo=motivo)
            sucesso = "Entrada registrada."
        elif tipo == "saida":
            r = regras.registrar_saida(c, insumo, quantidade, uid(), motivo,
                                       setor=setor, unidade=unidade)
            sucesso = "Saída registrada."
        elif tipo == "perda":
            r = regras.registrar_perda(c, insumo, quantidade, motivo, uid(),
                                       setor=setor, unidade=unidade)
            sucesso = "Perda registrada."
        elif tipo == "transferencia":
            r = regras.transferir(c, insumo, quantidade, setor, f.get("setor_destino"),
                                  uid(), unidade=unidade)
            sucesso = "Transferência registrada."
        elif tipo == "ajuste":
            r = regras.ajustar_inventario(c, insumo, quantidade, uid(), setor=setor,
                                          unidade=unidade, motivo=motivo or "Contagem de inventário")
            sucesso = ("Contagem conferida: o sistema já estava correto, nada foi alterado."
                       if not r["movimentacao_ids"] else "Ajuste de inventário registrado.")
        else:
            raise ErroDeRegra("Escolha o tipo de movimentação.")
    except (ErroDeRegra, ValueError, sqlite3.IntegrityError) as e:
        flash(str(e), "erro")
        return render_template("movimentar.html", form=f, **_dados_do_formulario())

    flash(sucesso, "ok")
    for aviso in r["avisos"]:
        flash(aviso, "aviso")
    return redirect(url_for("estoque") if pode("estoque.ver") else url_for("movimentar"))


@app.route("/historico")
@requer("estoque.ver")
def historico():
    c = con()
    filtro = request.args.get("insumo", type=int)
    insumos = c.execute("SELECT id, nome FROM insumo WHERE ativo = 1 ORDER BY nome").fetchall()
    linhas = regras.historico(c, filtro, limite=100)
    return render_template("historico.html", linhas=linhas, insumos=insumos, filtro=filtro)


# ---------------------------------------------------------------------
# Cadastros: insumos, fornecedores e categorias
# ---------------------------------------------------------------------

@app.route("/cadastros")
@requer("produto.gerir", "fornecedor.gerir")
def cadastros_inicio():
    return redirect(url_for("cadastro_insumos") if pode("produto.gerir") else url_for("cadastro_fornecedores"))


@app.route("/cadastros/insumos")
@requer("produto.gerir")
def cadastro_insumos():
    inativos = request.args.get("inativos") == "1"
    return render_template("insumos.html", aba="insumos", inativos=inativos,
                           insumos=cadastros.listar_insumos(con(), inativos))


def _opcoes_do_form_insumo(insumo_id=None):
    c = con()
    return {
        "categorias": c.execute("SELECT id, nome FROM categoria ORDER BY nome").fetchall(),
        # fornecedores ativos + os que já estão ligados a este insumo (mesmo se inativos)
        "fornecedores": c.execute(
            """SELECT id, nome FROM fornecedor
               WHERE ativo = 1 OR id IN (SELECT fornecedor_id FROM insumo_fornecedor WHERE insumo_id = ?)
               ORDER BY nome""", (insumo_id if insumo_id is not None else -1,)).fetchall(),
        "unidades": cadastros.UNIDADES_CONSUMO,
    }


def _salvar_insumo_do_form(insumo_id=None):
    f = request.form
    dados = dict(
        nome=f.get("nome"),
        categoria_id=_int_ou_none(f.get("categoria")),
        unidade_consumo=f.get("unidade_consumo"),
        unidade_compra=f.get("unidade_compra"),
        fator_compra=_numero(f.get("fator_compra") or "1"),
        estoque_minimo=_numero_opcional(f.get("estoque_minimo")),
        unidade_minimo=f.get("unidade_minimo", "consumo"),
        fornecedores_ids=[int(x) for x in f.getlist("fornecedores")],
    )
    if insumo_id is None:
        return cadastros.criar_insumo(con(), **dados)
    cadastros.editar_insumo(con(), insumo_id, **dados)
    return insumo_id


@app.route("/cadastros/insumos/novo", methods=["GET", "POST"])
@requer("produto.gerir")
def cadastro_insumo_novo():
    if request.method == "POST":
        try:
            _salvar_insumo_do_form()
            flash(f"Insumo '{request.form['nome'].strip()}' cadastrado com saldo zero. "
                  "Registre a entrada do estoque em Movimentar.", "ok")
            return redirect(url_for("cadastro_insumos"))
        except (ErroDeRegra, ValueError) as e:
            flash(str(e), "erro")
        form, marcados = request.form, request.form.getlist("fornecedores")
    else:
        form = {"unidade_consumo": "un", "fator_compra": "1", "unidade_minimo": "consumo"}
        marcados = []
    return render_template("insumo_form.html", aba="insumos", form=form, marcados=marcados,
                           insumo=None, travado=False, **_opcoes_do_form_insumo())


@app.route("/cadastros/insumos/<int:insumo_id>", methods=["GET", "POST"])
@requer("produto.gerir")
def cadastro_insumo_editar(insumo_id):
    dados = cadastros.dados_insumo(con(), insumo_id)
    if dados is None:
        abort(404)
    ins = dados["insumo"]

    if request.method == "POST":
        try:
            _salvar_insumo_do_form(insumo_id)
            flash(f"Insumo '{request.form['nome'].strip()}' atualizado.", "ok")
            return redirect(url_for("cadastro_insumos"))
        except (ErroDeRegra, ValueError) as e:
            flash(str(e), "erro")
        form, marcados = request.form, request.form.getlist("fornecedores")
    else:
        form = {
            "nome": ins["nome"],
            "categoria": str(ins["categoria_id"] or ""),
            "unidade_consumo": ins["unidade_consumo"],
            "unidade_compra": ins["unidade_compra"],
            "fator_compra": inp(ins["fator_compra"]),
            "estoque_minimo": inp(ins["estoque_minimo"]),
            "unidade_minimo": "consumo",
        }
        marcados = [str(x) for x in dados["fornecedores_ids"]]
    return render_template("insumo_form.html", aba="insumos", form=form, marcados=marcados,
                           insumo=ins, travado=dados["tem_historico"], **_opcoes_do_form_insumo(insumo_id))


@app.post("/cadastros/insumos/<int:insumo_id>/ativo")
@requer("produto.gerir")
def cadastro_insumo_ativo(insumo_id):
    ativar = request.form.get("ativo") == "1"
    try:
        cadastros.definir_ativo_insumo(con(), insumo_id, ativar)
        flash("Insumo reativado." if ativar else "Insumo desativado.", "ok")
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(request.referrer or url_for("cadastro_insumos"))


@app.route("/cadastros/fornecedores", methods=["GET", "POST"])
@requer("fornecedor.gerir")
def cadastro_fornecedores():
    c = con()
    inativos = request.args.get("inativos") == "1"
    form = {}
    if request.method == "POST":
        f = request.form
        try:
            if f.get("id"):
                cadastros.editar_fornecedor(c, int(f["id"]), f.get("nome"), f.get("tipo"), f.get("telefone"))
                flash("Fornecedor atualizado.", "ok")
            else:
                cadastros.criar_fornecedor(c, f.get("nome"), f.get("tipo"), f.get("telefone"))
                flash("Fornecedor cadastrado.", "ok")
            return redirect(url_for("cadastro_fornecedores"))
        except (ErroDeRegra, ValueError) as e:
            flash(str(e), "erro")
            form = f
    elif request.args.get("editar", type=int):
        linha = c.execute("SELECT * FROM fornecedor WHERE id = ?", (request.args.get("editar", type=int),)).fetchone()
        form = dict(linha) if linha else {}
    return render_template("fornecedores.html", aba="fornecedores", form=form, inativos=inativos,
                           fornecedores=cadastros.listar_fornecedores(c, inativos))


@app.post("/cadastros/fornecedores/<int:fornecedor_id>/ativo")
@requer("fornecedor.gerir")
def cadastro_fornecedor_ativo(fornecedor_id):
    ativar = request.form.get("ativo") == "1"
    try:
        cadastros.definir_ativo_fornecedor(con(), fornecedor_id, ativar)
        flash("Fornecedor reativado." if ativar else "Fornecedor desativado.", "ok")
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(request.referrer or url_for("cadastro_fornecedores"))


@app.route("/cadastros/categorias", methods=["GET", "POST"])
@requer("produto.gerir")
def cadastro_categorias():
    c = con()
    form = {}
    if request.method == "POST":
        f = request.form
        try:
            minimo = _numero_opcional(f.get("estoque_minimo_padrao"))
            if f.get("id"):
                cadastros.editar_categoria(c, int(f["id"]), f.get("nome"), minimo)
                flash("Categoria atualizada.", "ok")
            else:
                cadastros.criar_categoria(c, f.get("nome"), minimo)
                flash("Categoria cadastrada.", "ok")
            return redirect(url_for("cadastro_categorias"))
        except (ErroDeRegra, ValueError) as e:
            flash(str(e), "erro")
            form = f
    elif request.args.get("editar", type=int):
        linha = c.execute("SELECT * FROM categoria WHERE id = ?", (request.args.get("editar", type=int),)).fetchone()
        if linha:
            form = {"id": linha["id"], "nome": linha["nome"], "estoque_minimo_padrao": inp(linha["estoque_minimo_padrao"])}
    return render_template("categorias.html", aba="categorias", form=form,
                           categorias=cadastros.listar_categorias(c))


# ---------------------------------------------------------------------
# Cadastros: produtos, receitas e promoções
# ---------------------------------------------------------------------

@app.route("/cadastros/produtos")
@requer("produto.gerir")
def cadastro_produtos():
    inativos = request.args.get("inativos") == "1"
    return render_template("produtos.html", aba="produtos", inativos=inativos,
                           produtos=cadastros.listar_produtos(con(), inativos))


def _opcoes_do_form_produto():
    c = con()
    grupos = {r[0] for r in c.execute("SELECT DISTINCT grupo_escolha FROM receita_item WHERE grupo_escolha IS NOT NULL")}
    return {
        "insumos": c.execute("SELECT id, nome, unidade_consumo FROM insumo WHERE ativo = 1 ORDER BY nome").fetchall(),
        "grupos": sorted(grupos | {"destilado"}),
        "categorias": ["Drink", "Kit", "Cerveja", "Chopp"],
    }


def _linhas_do_form_produto():
    """Lê as linhas de ingredientes do formulário, ignorando as totalmente em branco."""
    f = request.form
    linhas = []
    for i, q, g in zip(f.getlist("insumo"), f.getlist("quantidade"), f.getlist("grupo")):
        if i.strip() or q.strip() or g.strip():
            linhas.append({"insumo": i, "quantidade": q, "grupo": g})
    return linhas


def _itens_a_partir_das_linhas(linhas):
    itens = []
    for n, l in enumerate(linhas, start=1):
        if not l["insumo"]:
            raise ErroDeRegra(f"Ingrediente {n}: escolha o insumo.")
        try:
            quantidade = _numero(l["quantidade"])
        except ErroDeRegra as e:
            raise ErroDeRegra(f"Ingrediente {n}: {e}")
        itens.append({"insumo_id": int(l["insumo"]), "quantidade": quantidade, "grupo_escolha": l["grupo"]})
    return itens


def _salvar_produto_do_form(produto_id=None):
    f = request.form
    itens = _itens_a_partir_das_linhas(_linhas_do_form_produto())
    preco = _numero(f.get("preco"))
    if produto_id is None:
        return cadastros.criar_produto(con(), f.get("nome"), f.get("categoria"), preco, itens)
    cadastros.editar_produto(con(), produto_id, f.get("nome"), f.get("categoria"), preco, itens)
    return produto_id


@app.route("/cadastros/produtos/novo", methods=["GET", "POST"])
@requer("produto.gerir")
def cadastro_produto_novo():
    if request.method == "POST":
        try:
            _salvar_produto_do_form()
            flash(f"Produto '{request.form['nome'].strip()}' cadastrado.", "ok")
            return redirect(url_for("cadastro_produtos"))
        except (ErroDeRegra, ValueError) as e:
            flash(str(e), "erro")
        form, linhas = request.form, _linhas_do_form_produto() or [{}]
    else:
        form, linhas = {"categoria": "Drink"}, [{}, {}]
    return render_template("produto_form.html", aba="produtos", form=form, linhas=linhas, produto=None,
                           **_opcoes_do_form_produto())


@app.route("/cadastros/produtos/<int:produto_id>", methods=["GET", "POST"])
@requer("produto.gerir")
def cadastro_produto_editar(produto_id):
    produto = con().execute("SELECT * FROM produto WHERE id = ?", (produto_id,)).fetchone()
    if produto is None:
        abort(404)
    if request.method == "POST":
        try:
            _salvar_produto_do_form(produto_id)
            flash(f"Produto '{request.form['nome'].strip()}' atualizado.", "ok")
            return redirect(url_for("cadastro_produtos"))
        except (ErroDeRegra, ValueError) as e:
            flash(str(e), "erro")
        form, linhas = request.form, _linhas_do_form_produto() or [{}]
    else:
        form = {"nome": produto["nome"], "categoria": produto["categoria"], "preco": inp(produto["preco_padrao"])}
        linhas = [{"insumo": str(r["insumo_id"]), "quantidade": inp(r["quantidade"]), "grupo": r["grupo_escolha"] or ""}
                  for r in cadastros.receita_do_produto(con(), produto_id)]
    return render_template("produto_form.html", aba="produtos", form=form, linhas=linhas, produto=produto,
                           **_opcoes_do_form_produto())


@app.post("/cadastros/produtos/<int:produto_id>/ativo")
@requer("produto.gerir")
def cadastro_produto_ativo(produto_id):
    ativar = request.form.get("ativo") == "1"
    try:
        cadastros.definir_ativo_produto(con(), produto_id, ativar)
        flash("Produto reativado." if ativar else "Produto desativado: saiu da venda.", "ok")
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(request.referrer or url_for("cadastro_produtos"))


@app.route("/cadastros/produtos/<int:produto_id>/promocoes", methods=["GET", "POST"])
@requer("produto.gerir")
def cadastro_promocoes(produto_id):
    c = con()
    produto = c.execute("SELECT * FROM produto WHERE id = ?", (produto_id,)).fetchone()
    if produto is None:
        abort(404)
    form = {}
    if request.method == "POST":
        f = request.form
        try:
            cadastros.criar_promocao(
                c, produto_id, _numero(f.get("preco")),
                dias_semana=[int(d) for d in f.getlist("dias")],
                data_inicio=f.get("data_inicio"), data_fim=f.get("data_fim"))
            flash("Promoção cadastrada.", "ok")
            return redirect(url_for("cadastro_promocoes", produto_id=produto_id))
        except (ErroDeRegra, ValueError) as e:
            flash(str(e), "erro")
            form = f
    vigente = cadastros.preco_vigente(c, produto_id)
    return render_template("promocoes.html", aba="produtos", produto=produto, form=form,
                           marcados=form.getlist("dias") if form else [], vigente=vigente,
                           promocoes=cadastros.listar_promocoes(c, produto_id),
                           dias_nomes=["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"])


@app.post("/cadastros/promocoes/<int:promocao_id>/<acao>")
@requer("produto.gerir")
def cadastro_promocao_acao(promocao_id, acao):
    c = con()
    linha = c.execute("SELECT produto_id FROM preco_promocao WHERE id = ?", (promocao_id,)).fetchone()
    if linha is None:
        abort(404)
    try:
        if acao == "excluir":
            cadastros.excluir_promocao(c, promocao_id)
            flash("Promoção excluída.", "ok")
        elif acao in ("ativar", "desativar"):
            cadastros.definir_ativo_promocao(c, promocao_id, acao == "ativar")
            flash("Promoção ativada." if acao == "ativar" else "Promoção desativada.", "ok")
        else:
            abort(404)
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(url_for("cadastro_promocoes", produto_id=linha["produto_id"]))


# ---------------------------------------------------------------------
# Inicialização
# ---------------------------------------------------------------------


# ---------------------------------------------------------------------
# Comandas, pedidos e entregas (Fase 2)
# ---------------------------------------------------------------------
def _voltar(padrao):
    """Volta para a página de onde veio, só se for um caminho interno do próprio sistema."""
    destino = request.form.get("voltar", "")
    if destino.startswith("/") and not destino.startswith("//"):
        return redirect(destino)
    return redirect(padrao)


def _inteiro(texto, descricao):
    try:
        return _int_ou_none(texto)
    except ValueError:
        raise ErroDeRegra(f"{descricao} inválido(a): use só números inteiros.")


def _avisos(r):
    for a in r.get("avisos", []):
        flash(a, "aviso")


@app.route("/comandas", methods=["GET", "POST"])
@requer("pedido.registrar", "caixa.operar")
def comandas():
    c = con()
    if request.method == "POST":
        if not pode("pedido.registrar"):
            abort(403)
        try:
            mesa = _inteiro(request.form.get("mesa"), "Mesa")
            cid = pedidos.abrir_comanda(c, request.form.get("identificacao"), uid(), mesa=mesa)
            return redirect(url_for("comanda", comanda_id=cid))
        except ErroDeRegra as e:
            flash(str(e), "erro")
    ver = request.args.get("ver", "abertas")
    lista = pedidos.listar_comandas(c, "aberta" if ver == "abertas" else None)
    aguardando = pedidos.itens_aguardando(c) if pode("pedido.registrar") else []
    return render_template("comandas.html", lista=lista, ver=ver, aguardando=aguardando)


@app.route("/comandas/<int:comanda_id>")
@requer("pedido.registrar", "caixa.operar", "pedido.entregar")
def comanda(comanda_id):
    c = con()
    dados = pedidos.dados_comanda(c, comanda_id)
    if dados is None:
        abort(404)
    produtos = []
    if dados["comanda"]["status"] == "aberta":
        for p in cadastros.listar_produtos(c):
            p = dict(p)
            p["opcoes"] = pedidos.opcoes_do_produto(c, p["id"])
            produtos.append(p)
    return render_template("comanda.html", d=dados, produtos=produtos)


@app.post("/comandas/<int:comanda_id>/itens")
@requer("pedido.registrar")
def comanda_item_novo(comanda_id):
    escolhas = {k[len("escolha_"):]: v for k, v in request.form.items() if k.startswith("escolha_")}
    try:
        quantidade = _inteiro(request.form.get("quantidade"), "Quantidade") or 1
        pedidos.adicionar_item(con(), comanda_id, _inteiro(request.form.get("produto_id"), "Produto"), quantidade,
                               uid(), escolhas=escolhas, observacao=request.form.get("observacao"))
        flash("Item adicionado. O estoque só baixa quando for marcado como entregue.", "ok")
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(url_for("comanda", comanda_id=comanda_id) + "#produtos")


@app.post("/itens/<int:item_id>/entregar")
@requer("pedido.entregar")
def item_entregar(item_id):
    try:
        r = pedidos.entregar_item(con(), item_id, uid())
        flash("Item entregue: ingredientes baixados do estoque.", "ok")
        _avisos(r)
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return _voltar(url_for("entregas"))


@app.post("/itens/<int:item_id>/cancelar")
@requer("pedido.registrar", "pedido.entregar", "pedido.estornar")
def item_cancelar(item_id):
    try:
        item = con().execute("SELECT status FROM pedido_item WHERE id = ?", (item_id,)).fetchone()
        # item já entregue: cancelar devolve ingredientes ao estoque, então exige permissão própria
        if item and item["status"] == "entregue" and not pode("pedido.estornar"):
            raise ErroDeRegra("Este item já foi entregue: seu perfil não tem permissão para cancelá-lo.")
        if item and item["status"] == "pedido" and not pode("pedido.registrar", "pedido.entregar"):
            abort(403)
        pedidos.cancelar_item(con(), item_id, uid(), request.form.get("motivo"))
        flash("Item cancelado.", "ok")
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return _voltar(url_for("entregas"))


@app.post("/comandas/<int:comanda_id>/fechar")
@requer("caixa.operar")
def comanda_fechar(comanda_id):
    try:
        r = pedidos.fechar_comanda(con(), comanda_id, uid())
        flash(f"Comanda fechada. Total: {moeda(r['total'])}.", "ok")
        return redirect(url_for("comandas"))
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(url_for("comanda", comanda_id=comanda_id))


@app.post("/comandas/<int:comanda_id>/cancelar")
@requer("caixa.operar")
def comanda_cancelar(comanda_id):
    try:
        pedidos.cancelar_comanda(con(), comanda_id, uid())
        flash("Comanda cancelada.", "ok")
        return redirect(url_for("comandas"))
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(url_for("comanda", comanda_id=comanda_id))


@app.route("/entregas")
@requer("pedido.entregar")
def entregas():
    return render_template("entregas.html", fila=pedidos.itens_pendentes(con()))


# ---------------------------------------------------------------------
# Login, conta e usuários (Passo 8)
# ---------------------------------------------------------------------

def _entrar(usuario_id):
    session.clear()                       # descarta qualquer sessão anterior neste aparelho
    session["usuario_id"] = usuario_id
    session.permanent = True              # vale por PERMANENT_SESSION_LIFETIME, mesmo fechando o navegador


@app.route("/entrar", methods=["GET", "POST"])
def login():
    c = con()
    if auth.precisa_primeiro_acesso(c):
        return redirect(url_for("primeiro_acesso"))
    proximo = _destino_seguro(request.values.get("proximo"))
    if request.method == "POST":
        try:
            _entrar(auth.autenticar(c, request.form.get("login"), request.form.get("senha")))
            g.usuario = auth.carregar_usuario(c, session["usuario_id"])
            return redirect(proximo or pagina_inicial())
        except ErroDeRegra as e:
            flash(str(e), "erro")
    return render_template("login.html", login_digitado=request.form.get("login", ""), proximo=proximo or "")


@app.post("/sair")
def logout():
    session.clear()
    flash("Você saiu do sistema.", "ok")
    return redirect(url_for("login"))


@app.route("/primeiro-acesso", methods=["GET", "POST"])
def primeiro_acesso():
    c = con()
    if not auth.precisa_primeiro_acesso(c):
        return redirect(url_for("login"))
    f = request.form
    if request.method == "POST":
        try:
            _entrar(auth.configurar_primeiro_acesso(c, f.get("nome"), f.get("login"),
                                                    f.get("senha"), f.get("confirmacao")))
            flash("Pronto! Você é o Dono do sistema. Agora cadastre a equipe em Usuários.", "ok")
            return redirect(url_for("usuarios"))
        except ErroDeRegra as e:
            flash(str(e), "erro")
    return render_template("primeiro_acesso.html", form=f)


@app.route("/minha-conta", methods=["GET", "POST"])
def minha_conta():
    if request.method == "POST":
        f = request.form
        try:
            auth.trocar_propria_senha(con(), uid(), f.get("senha_atual"), f.get("senha"), f.get("confirmacao"))
            flash("Senha alterada.", "ok")
            g.usuario = auth.carregar_usuario(con(), uid())
            return redirect(pagina_inicial())
        except ErroDeRegra as e:
            flash(str(e), "erro")
    return render_template("conta.html")


def _perfis_que_posso_dar():
    """Quem não é Dono não vê a opção 'Dono' (não pode criar nem promover um Dono)."""
    perfis = con().execute("SELECT id, nome FROM perfil ORDER BY id").fetchall()
    if g.usuario["perfil"] == auth.PERFIL_DONO:
        return perfis
    return [p for p in perfis if p["nome"] != auth.PERFIL_DONO]


@app.route("/usuarios", methods=["GET", "POST"])
@requer("usuario.gerir")
def usuarios():
    c = con()
    f = request.form
    if request.method == "POST":
        try:
            auth.criar_usuario(c, f.get("nome"), f.get("login"), f.get("senha"),
                               _inteiro(f.get("perfil_id"), "Perfil"), g.usuario)
            flash(f"Usuário criado. Na primeira vez que entrar, {f.get('nome').strip()} vai criar a própria senha.", "ok")
            return redirect(url_for("usuarios"))
        except ErroDeRegra as e:
            flash(str(e), "erro")
    return render_template("usuarios.html", lista=auth.listar_usuarios(c), perfis=_perfis_que_posso_dar(),
                           form=f, minimo=auth.TAMANHO_MINIMO_SENHA)


@app.route("/usuarios/<int:usuario_id>", methods=["GET", "POST"])
@requer("usuario.gerir")
def usuario_editar(usuario_id):
    c = con()
    alvo = auth.buscar_usuario(c, usuario_id)
    if alvo is None:
        abort(404)
    f = request.form
    if request.method == "POST":
        try:
            if f.get("acao") == "senha":
                auth.redefinir_senha(c, usuario_id, f.get("senha"), g.usuario)
                flash("Senha provisória definida. A pessoa vai criar uma nova no próximo login.", "ok")
            else:
                auth.editar_usuario(c, usuario_id, f.get("nome"), _inteiro(f.get("perfil_id"), "Perfil"),
                                    f.get("ativo") == "1", g.usuario)
                flash("Usuário atualizado.", "ok")
            return redirect(url_for("usuarios"))
        except ErroDeRegra as e:
            flash(str(e), "erro")
    perfis = _perfis_que_posso_dar()
    if alvo["perfil"] == auth.PERFIL_DONO and g.usuario["perfil"] != auth.PERFIL_DONO:
        flash("Só o Dono pode alterar usuários com perfil Dono.", "aviso")
    return render_template("usuario_form.html", alvo=alvo, perfis=perfis, minimo=auth.TAMANHO_MINIMO_SENHA,
                           eu_mesmo=(usuario_id == uid()))


@app.route("/perfis", methods=["GET", "POST"])
@requer("usuario.gerir")
def perfis():
    c = con()
    if request.method == "POST":
        try:
            perfil_id = _inteiro(request.form.get("perfil_id"), "Perfil")
            auth.definir_permissoes_perfil(c, perfil_id, request.form.getlist("permissao"), g.usuario)
            flash("Permissões salvas. Valem a partir da próxima tela que a equipe abrir.", "ok")
            return redirect(url_for("perfis"))
        except ErroDeRegra as e:
            flash(str(e), "erro")
    return render_template("perfis.html", perfis=auth.listar_perfis(c), permissoes=auth.listar_permissoes(c),
                           sou_dono=(g.usuario["perfil"] == auth.PERFIL_DONO))


# ---------------------------------------------------------------------
# Pedidos pelo QR Code (Passo 10)
#   Cliente (sem login):  /m/<codigo>  ->  abre a comanda, vê o cardápio, pede
#   Equipe:               confirma em Comandas; cadastra mesas e imprime QR em /mesas
# ---------------------------------------------------------------------

def _comanda_do_cliente(mesa):
    """A comanda que ESTE celular abriu nesta mesa (guardada no cookie assinado), se ainda estiver aberta."""
    cid = session.get("cliente", {}).get(mesa["codigo"])
    if cid is None:
        return None
    c = con().execute("SELECT * FROM comanda WHERE id = ?", (cid,)).fetchone()
    if c is None or c["status"] != "aberta" or c["mesa"] != mesa["numero"]:
        return None
    return c


def _mesa_ou_404(codigo):
    mesa = mesas.mesa_por_codigo(con(), codigo)
    if mesa is None:
        abort(make_response(render_template("cliente_invalido.html"), 404))
    return mesa


@app.route("/m/<codigo>", methods=["GET", "POST"])
def cliente_mesa(codigo):
    mesa = _mesa_ou_404(codigo)
    comanda_cli = _comanda_do_cliente(mesa)
    if request.method == "POST" and comanda_cli is None:
        try:
            cid = pedidos.abrir_comanda_cliente(con(), mesa["numero"], request.form.get("nome"))
            meus = dict(session.get("cliente", {}))
            meus[mesa["codigo"]] = cid
            session["cliente"] = meus
            session.permanent = True
            return redirect(url_for("cliente_mesa", codigo=codigo) + "#cardapio")
        except ErroDeRegra as e:
            flash(str(e), "erro")
    if comanda_cli is None:
        return render_template("cliente_inicio.html", mesa=mesa)

    c = con()
    produtos = []
    for p in cadastros.listar_produtos(c):
        p = dict(p)
        p["opcoes"] = pedidos.opcoes_do_produto(c, p["id"])
        produtos.append(p)
    return render_template("cliente_mesa.html", mesa=mesa, d=pedidos.dados_comanda(c, comanda_cli["id"]),
                           produtos=produtos, max_qtd=pedidos.MAX_QTD_CLIENTE)


@app.post("/m/<codigo>/pedir")
def cliente_pedir(codigo):
    mesa = _mesa_ou_404(codigo)
    comanda_cli = _comanda_do_cliente(mesa)
    if comanda_cli is None:
        flash("Sua comanda foi fechada. Abra uma nova para continuar pedindo.", "aviso")
        return redirect(url_for("cliente_mesa", codigo=codigo))
    f = request.form
    escolhas = {k[len("escolha_"):]: v for k, v in f.items() if k.startswith("escolha_")}
    try:
        pedidos.pedir_pelo_qrcode(con(), comanda_cli["id"], _inteiro(f.get("produto_id"), "Produto"),
                                  _inteiro(f.get("quantidade"), "Quantidade") or 1,
                                  escolhas=escolhas, observacao=f.get("observacao"))
        flash("Pedido enviado! Um garçom vai confirmar em instantes.", "ok")
        return redirect(url_for("cliente_mesa", codigo=codigo))
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(url_for("cliente_mesa", codigo=codigo) + "#cardapio")


@app.post("/m/<codigo>/desistir/<int:item_id>")
def cliente_desistir(codigo, item_id):
    mesa = _mesa_ou_404(codigo)
    comanda_cli = _comanda_do_cliente(mesa)
    if comanda_cli is not None:
        try:
            pedidos.cliente_desistir(con(), comanda_cli["id"], item_id)
            flash("Pedido retirado.", "ok")
        except ErroDeRegra as e:
            flash(str(e), "erro")
    return redirect(url_for("cliente_mesa", codigo=codigo))


@app.post("/itens/<int:item_id>/confirmar")
@requer("pedido.registrar")
def item_confirmar(item_id):
    try:
        pedidos.confirmar_item(con(), item_id, uid())
        flash("Pedido confirmado: foi para a fila de Entregas.", "ok")
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return _voltar(url_for("comandas"))


def _endereco_base():
    """Endereço que vai dentro do QR Code. Se o Dono abriu o sistema como 'localhost',
    troca pelo IP do PC na rede (o celular do cliente não chega em 'localhost')."""
    host = request.host
    nome, _, porta = host.partition(":")
    if nome in ("localhost", "127.0.0.1", "::1", "[::1]"):
        host = f"{ip_local()}:{porta or 80}"
    return f"http://{host}"


@app.route("/mesas", methods=["GET", "POST"])
@requer("mesa.gerir")
def mesas_lista():
    c = con()
    if request.method == "POST":
        try:
            if request.form.get("acao") == "trocar":
                numero = _inteiro(request.form.get("numero"), "Mesa")
                mesas.trocar_codigo(c, numero)
                flash(f"Código da mesa {numero} trocado. O QR antigo parou de funcionar: imprima o novo.", "aviso")
            else:
                mesas.definir_quantidade(c, _inteiro(request.form.get("quantidade"), "Quantidade de mesas"))
                flash("Mesas atualizadas.", "ok")
            return redirect(url_for("mesas_lista"))
        except ErroDeRegra as e:
            flash(str(e), "erro")
    base = _endereco_base()
    lista = [{"numero": m["numero"], "link": f"{base}/m/{m['codigo']}",
              "svg": mesas.qr_svg(f"{base}/m/{m['codigo']}")} for m in mesas.listar_mesas(c)]
    return render_template("mesas.html", lista=lista, base=base)


# ---------------------------------------------------------------------
# Comunicação Bar <-> Estoque (Passo 11)
# ---------------------------------------------------------------------

@app.route("/reposicao")
@requer("reposicao.pedir", "reposicao.atender")
def reposicoes_lista():
    ver = request.args.get("ver", "pendentes")
    return render_template("reposicoes.html", lista=reposicao.listar(con(), so_pendentes=(ver == "pendentes")),
                           ver=ver, nomes=reposicao.STATUS_NOMES)


@app.route("/reposicao/nova", methods=["GET", "POST"])
@requer("reposicao.pedir")
def reposicao_nova():
    f = request.form
    linhas = [{}]
    if request.method == "POST":
        linhas = [{"insumo": i, "quantidade": q, "unidade": u}
                  for i, q, u in zip(f.getlist("insumo"), f.getlist("quantidade"), f.getlist("unidade"))
                  if i.strip() or q.strip()] or [{}]
        try:
            itens = []
            for n, l in enumerate(linhas, start=1):
                if not l.get("insumo"):
                    raise ErroDeRegra(f"Item {n}: escolha o insumo.")
                try:
                    itens.append({"insumo_id": int(l["insumo"]), "quantidade": _numero(l["quantidade"]),
                                  "unidade": l["unidade"]})
                except (ErroDeRegra, ValueError) as e:
                    raise ErroDeRegra(f"Item {n}: {e}")
            rid = reposicao.criar(con(), itens, uid(), urgente=f.get("urgente") == "1", observacao=f.get("observacao"))
            flash("Pedido enviado ao Estoque.", "ok")
            return redirect(url_for("reposicao_ver", reposicao_id=rid))
        except ErroDeRegra as e:
            flash(str(e), "erro")
    return render_template("reposicao_form.html", form=f, linhas=linhas, insumos=reposicao.opcoes_de_insumos(con()))


@app.route("/reposicao/<int:reposicao_id>")
@requer("reposicao.pedir", "reposicao.atender")
def reposicao_ver(reposicao_id):
    d = reposicao.dados(con(), reposicao_id)
    if d is None:
        abort(404)
    return render_template("reposicao.html", d=d, r=d["reposicao"])


@app.post("/reposicao/<int:reposicao_id>/enviar")
@requer("reposicao.atender")
def reposicao_enviar(reposicao_id):
    f = request.form
    try:
        enviados = {}
        for item_id in f.getlist("item"):
            try:
                enviados[int(item_id)] = _numero(f.get(f"enviado_{item_id}"))
            except (ErroDeRegra, ValueError) as e:
                raise ErroDeRegra(f"{f.get(f'nome_{item_id}', 'Item')}: {e}")
        r = reposicao.atender(con(), reposicao_id, uid(), enviados, resposta=f.get("resposta"))
        flash("Enviado ao Bar: transferências registradas.", "ok")
        for falta in r["faltas"]:
            flash(f"Enviado menos que o pedido · {falta}", "aviso")
        _avisos(r)
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(url_for("reposicao_ver", reposicao_id=reposicao_id))


@app.post("/reposicao/<int:reposicao_id>/recusar")
@requer("reposicao.atender")
def reposicao_recusar(reposicao_id):
    try:
        reposicao.recusar(con(), reposicao_id, uid(), request.form.get("motivo"))
        flash("Pedido recusado. O Bar vai ver o motivo.", "ok")
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(url_for("reposicao_ver", reposicao_id=reposicao_id))


@app.post("/reposicao/<int:reposicao_id>/cancelar")
@requer("reposicao.pedir")
def reposicao_cancelar(reposicao_id):
    try:
        reposicao.cancelar(con(), reposicao_id, uid())
        flash("Pedido cancelado.", "ok")
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(url_for("reposicao_ver", reposicao_id=reposicao_id))


# ---------------------------------------------------------------------
# Compras com aprovação (Passo 9)
# ---------------------------------------------------------------------

PERMISSOES_COMPRAS = ("compra.solicitar", "compra.aprovar", "estoque.movimentar")


@app.route("/compras")
@requer(*PERMISSOES_COMPRAS)
def compras_lista():
    ver = request.args.get("ver", "abertas")
    status = ("pendente", "aprovada") if ver == "abertas" else None
    return render_template("compras.html", lista=compras.listar_compras(con(), status), ver=ver,
                           nomes=compras.STATUS_NOMES)


def _opcoes_do_form_compra():
    c = con()
    return {
        "insumos": c.execute(
            """SELECT i.id, i.nome, i.unidade_compra, i.unidade_consumo, i.fator_compra,
                      COALESCE(cat.nome, 'Sem categoria') AS categoria
               FROM insumo i LEFT JOIN categoria cat ON cat.id = i.categoria_id
               WHERE i.ativo = 1 ORDER BY categoria, i.nome""").fetchall(),
        "fornecedores": c.execute("SELECT id, nome FROM fornecedor WHERE ativo = 1 ORDER BY nome").fetchall(),
        "sugestao": compras.sugestao_de_compra(c),
        "fator_sugestao": compras.FATOR_SUGESTAO,
    }


def _linhas_do_form_compra():
    f = request.form
    return [{"insumo": i, "quantidade": q, "preco": p}
            for i, q, p in zip(f.getlist("insumo"), f.getlist("quantidade"), f.getlist("preco"))
            if i.strip() or q.strip() or p.strip()]


@app.route("/compras/nova", methods=["GET", "POST"])
@requer("compra.solicitar")
def compra_nova():
    if request.method == "POST":
        linhas = _linhas_do_form_compra()
        try:
            itens = []
            for n, l in enumerate(linhas, start=1):
                if not l["insumo"]:
                    raise ErroDeRegra(f"Item {n}: escolha o insumo.")
                try:
                    itens.append({"insumo_id": int(l["insumo"]), "quantidade": _numero(l["quantidade"]),
                                  "preco_estimado": _numero_opcional(l["preco"])})
                except ErroDeRegra as e:
                    raise ErroDeRegra(f"Item {n}: {e}")
            cid = compras.criar_compra(con(), itens, uid(), fornecedor_id=_inteiro(request.form.get("fornecedor"), "Fornecedor"),
                                       observacao=request.form.get("observacao"))
            flash("Compra enviada para aprovação.", "ok")
            return redirect(url_for("compra_ver", compra_id=cid))
        except ErroDeRegra as e:
            flash(str(e), "erro")
        return render_template("compra_form.html", form=request.form, linhas=linhas or [{}], **_opcoes_do_form_compra())

    opcoes = _opcoes_do_form_compra()
    if request.args.get("sugestao"):
        linhas = [{"insumo": str(s["insumo_id"]), "quantidade": str(s["sugerido"]),
                   "preco": inp(s["ultimo_preco"])} for s in opcoes["sugestao"]]
    else:
        linhas = []
    return render_template("compra_form.html", form={}, linhas=linhas or [{}], **opcoes)


@app.route("/compras/<int:compra_id>")
@requer(*PERMISSOES_COMPRAS)
def compra_ver(compra_id):
    dados = compras.dados_compra(con(), compra_id)
    if dados is None:
        abort(404)
    c = dados["compra"]
    pode_cancelar = c["status"] in ("pendente", "aprovada") and (
        pode("compra.aprovar") or (pode("compra.solicitar") and c["solicitada_por"] == uid()))
    return render_template("compra.html", d=dados, c=c, pode_cancelar=pode_cancelar)


def _acao_compra(compra_id, funcao, sucesso, *args):
    try:
        funcao(con(), compra_id, uid(), *args)
        flash(sucesso, "ok")
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(url_for("compra_ver", compra_id=compra_id))


@app.post("/compras/<int:compra_id>/aprovar")
@requer("compra.aprovar")
def compra_aprovar(compra_id):
    return _acao_compra(compra_id, compras.aprovar, "Compra aprovada. Agora é só comprar e, quando chegar, dar o recebimento.")


@app.post("/compras/<int:compra_id>/recusar")
@requer("compra.aprovar")
def compra_recusar(compra_id):
    return _acao_compra(compra_id, compras.recusar, "Compra recusada.", request.form.get("motivo"))


@app.post("/compras/<int:compra_id>/cancelar")
@requer("compra.solicitar", "compra.aprovar")
def compra_cancelar(compra_id):
    c = con().execute("SELECT solicitada_por FROM compra WHERE id = ?", (compra_id,)).fetchone()
    # quem só pede compras pode cancelar apenas as próprias
    if c is not None and not pode("compra.aprovar") and c["solicitada_por"] != uid():
        abort(403)
    return _acao_compra(compra_id, compras.cancelar, "Compra cancelada.", request.form.get("motivo"))


@app.post("/compras/<int:compra_id>/receber")
@requer("estoque.movimentar")
def compra_receber(compra_id):
    f = request.form
    try:
        recebidos = {}
        for item_id in f.getlist("item"):
            nome = f.get(f"nome_{item_id}", "item")
            try:
                recebidos[int(item_id)] = {"quantidade": _numero(f.get(f"recebido_{item_id}")),
                                           "preco_pago": _numero_opcional(f.get(f"preco_{item_id}"))}
            except ErroDeRegra as e:
                raise ErroDeRegra(f"{nome}: {e}")
        r = compras.receber(con(), compra_id, uid(), recebidos)
        flash(f"Mercadoria recebida: {len(r['movimentacao_ids'])} entrada(s) registrada(s) no Estoque.", "ok")
        for falta in r["faltas"]:
            flash(f"Chegou menos que o pedido · {falta}", "aviso")
        _avisos(r)
    except (ErroDeRegra, ValueError) as e:
        flash(str(e), "erro")
    return redirect(url_for("compra_ver", compra_id=compra_id))


def ip_local():
    """Descobre o IP do PC na rede do bar (não envia nada, só consulta a rota)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    PORTA = 5000
    print("\nSistema de estoque - Buteco do Feijão")
    print(f"  Neste PC:             http://localhost:{PORTA}")
    print(f"  Tablets e celulares:  http://{ip_local()}:{PORTA}   (mesmo Wi-Fi)")
    print("  Para encerrar: Ctrl+C\n")
    # debug=False de propósito: com host aberto na rede, o modo debug seria um risco.
    app.run(host="0.0.0.0", port=PORTA, debug=False)
