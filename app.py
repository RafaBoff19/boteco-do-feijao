"""Interface web do sistema de estoque do Buteco do Feijão.

Uso:   python app.py
Abre:  http://localhost:5000   (no próprio PC)
       http://<IP-do-PC>:5000  (em tablets e celulares ligados no mesmo Wi-Fi)

Funciona sem internet: só precisa que os aparelhos estejam na mesma rede do bar.
"""
import re
import socket
import sqlite3

from flask import Flask, abort, flash, g, redirect, render_template, request, url_for

import bd
import cadastros
import pedidos
import regras
from regras import ErroDeRegra

app = Flask(__name__)
app.secret_key = "trocar-quando-tiver-login"   # exigida pelo flash(); trocar quando houver login

# Enquanto não existe login, todas as operações ficam em nome do usuário 1 (Administrador).
USUARIO_PROVISORIO = 1

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
def alerta_lido(alerta_id):
    try:
        regras.marcar_alerta_lido(con(), alerta_id, USUARIO_PROVISORIO)
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
    }


@app.route("/movimentar", methods=["GET", "POST"])
def movimentar():
    if request.method == "GET":
        return render_template("movimentar.html", form={}, **_dados_do_formulario())

    f = request.form
    c = con()
    try:
        tipo = f.get("tipo", "")
        if not f.get("insumo"):
            raise ErroDeRegra("Escolha o insumo.")
        insumo = int(f["insumo"])
        quantidade = _numero(f.get("quantidade"))
        unidade = f.get("unidade", "consumo")
        setor = f.get("setor") or "Estoque"
        motivo = (f.get("motivo") or "").strip() or None
        fornecedor = int(f["fornecedor"]) if f.get("fornecedor") else None

        if tipo == "entrada":
            r = regras.registrar_entrada(c, insumo, quantidade, USUARIO_PROVISORIO,
                                         fornecedor_id=fornecedor, setor=setor,
                                         unidade=unidade, motivo=motivo)
            sucesso = "Entrada registrada."
        elif tipo == "saida":
            r = regras.registrar_saida(c, insumo, quantidade, USUARIO_PROVISORIO, motivo,
                                       setor=setor, unidade=unidade)
            sucesso = "Saída registrada."
        elif tipo == "perda":
            r = regras.registrar_perda(c, insumo, quantidade, motivo, USUARIO_PROVISORIO,
                                       setor=setor, unidade=unidade)
            sucesso = "Perda registrada."
        elif tipo == "transferencia":
            r = regras.transferir(c, insumo, quantidade, setor, f.get("setor_destino"),
                                  USUARIO_PROVISORIO, unidade=unidade)
            sucesso = "Transferência registrada."
        elif tipo == "ajuste":
            r = regras.ajustar_inventario(c, insumo, quantidade, USUARIO_PROVISORIO, setor=setor,
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
    return redirect(url_for("estoque"))


@app.route("/historico")
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
def cadastros_inicio():
    return redirect(url_for("cadastro_insumos"))


@app.route("/cadastros/insumos")
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
def cadastro_insumo_ativo(insumo_id):
    ativar = request.form.get("ativo") == "1"
    try:
        cadastros.definir_ativo_insumo(con(), insumo_id, ativar)
        flash("Insumo reativado." if ativar else "Insumo desativado.", "ok")
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(request.referrer or url_for("cadastro_insumos"))


@app.route("/cadastros/fornecedores", methods=["GET", "POST"])
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
def cadastro_fornecedor_ativo(fornecedor_id):
    ativar = request.form.get("ativo") == "1"
    try:
        cadastros.definir_ativo_fornecedor(con(), fornecedor_id, ativar)
        flash("Fornecedor reativado." if ativar else "Fornecedor desativado.", "ok")
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(request.referrer or url_for("cadastro_fornecedores"))


@app.route("/cadastros/categorias", methods=["GET", "POST"])
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
def cadastro_produto_ativo(produto_id):
    ativar = request.form.get("ativo") == "1"
    try:
        cadastros.definir_ativo_produto(con(), produto_id, ativar)
        flash("Produto reativado." if ativar else "Produto desativado: saiu da venda.", "ok")
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(request.referrer or url_for("cadastro_produtos"))


@app.route("/cadastros/produtos/<int:produto_id>/promocoes", methods=["GET", "POST"])
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
def comandas():
    c = con()
    if request.method == "POST":
        try:
            mesa = _inteiro(request.form.get("mesa"), "Mesa")
            cid = pedidos.abrir_comanda(c, request.form.get("identificacao"), USUARIO_PROVISORIO, mesa=mesa)
            return redirect(url_for("comanda", comanda_id=cid))
        except ErroDeRegra as e:
            flash(str(e), "erro")
    ver = request.args.get("ver", "abertas")
    lista = pedidos.listar_comandas(c, "aberta" if ver == "abertas" else None)
    return render_template("comandas.html", lista=lista, ver=ver)


@app.route("/comandas/<int:comanda_id>")
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
def comanda_item_novo(comanda_id):
    escolhas = {k[len("escolha_"):]: v for k, v in request.form.items() if k.startswith("escolha_")}
    try:
        quantidade = _inteiro(request.form.get("quantidade"), "Quantidade") or 1
        pedidos.adicionar_item(con(), comanda_id, _inteiro(request.form.get("produto_id"), "Produto"), quantidade,
                               USUARIO_PROVISORIO, escolhas=escolhas, observacao=request.form.get("observacao"))
        flash("Item adicionado. O estoque só baixa quando for marcado como entregue.", "ok")
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(url_for("comanda", comanda_id=comanda_id) + "#produtos")


@app.post("/itens/<int:item_id>/entregar")
def item_entregar(item_id):
    try:
        r = pedidos.entregar_item(con(), item_id, USUARIO_PROVISORIO)
        flash("Item entregue: ingredientes baixados do estoque.", "ok")
        _avisos(r)
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return _voltar(url_for("entregas"))


@app.post("/itens/<int:item_id>/cancelar")
def item_cancelar(item_id):
    try:
        pedidos.cancelar_item(con(), item_id, USUARIO_PROVISORIO, request.form.get("motivo"))
        flash("Item cancelado.", "ok")
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return _voltar(url_for("entregas"))


@app.post("/comandas/<int:comanda_id>/fechar")
def comanda_fechar(comanda_id):
    try:
        r = pedidos.fechar_comanda(con(), comanda_id, USUARIO_PROVISORIO)
        flash(f"Comanda fechada. Total: {moeda(r['total'])}.", "ok")
        return redirect(url_for("comandas"))
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(url_for("comanda", comanda_id=comanda_id))


@app.post("/comandas/<int:comanda_id>/cancelar")
def comanda_cancelar(comanda_id):
    try:
        pedidos.cancelar_comanda(con(), comanda_id, USUARIO_PROVISORIO)
        flash("Comanda cancelada.", "ok")
        return redirect(url_for("comandas"))
    except ErroDeRegra as e:
        flash(str(e), "erro")
    return redirect(url_for("comanda", comanda_id=comanda_id))


@app.route("/entregas")
def entregas():
    return render_template("entregas.html", fila=pedidos.itens_pendentes(con()))



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
