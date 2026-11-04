"""Regras dos cadastros: categorias, fornecedores e insumos.

Mesmo estilo do regras.py: cada função recebe a conexão (bd.conectar()), valida tudo
e levanta ErroDeRegra com uma mensagem clara quando algo não pode ser feito.
Se qualquer parte falhar, nada é gravado.
"""
from regras import ErroDeRegra

UNIDADES_CONSUMO = ("ml", "g", "un")


# ---------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------

def _texto(valor, descricao, obrigatorio=True):
    valor = (valor or "").strip()
    if obrigatorio and not valor:
        raise ErroDeRegra(f"Informe {descricao}.")
    return valor or None


def _nome_livre(con, tabela, nome, ignorar_id=None):
    """Recusa nome repetido, ignorando maiúsculas/minúsculas ('cachaça' = 'Cachaça')."""
    for r in con.execute(f"SELECT id, nome FROM {tabela}"):
        if r["nome"].casefold() == nome.casefold() and r["id"] != ignorar_id:
            raise ErroDeRegra(f"Já existe um cadastro com o nome '{r['nome']}'.")


def _numero_minimo(valor, descricao):
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or valor < 0:
        raise ErroDeRegra(f"{descricao} deve ser um número maior ou igual a zero.")
    return valor


def _existe(con, tabela, id_, descricao):
    if con.execute(f"SELECT 1 FROM {tabela} WHERE id = ?", (id_,)).fetchone() is None:
        raise ErroDeRegra(f"{descricao} não encontrado(a).")


# ---------------------------------------------------------------------
# Categorias
# ---------------------------------------------------------------------

def criar_categoria(con, nome, estoque_minimo_padrao=None):
    """estoque_minimo_padrao é na unidade de consumo dos insumos da categoria (ml, g ou un)."""
    nome = _texto(nome, "o nome da categoria")
    _nome_livre(con, "categoria", nome)
    minimo = _numero_minimo(estoque_minimo_padrao, "O estoque mínimo padrão")
    with con:
        return con.execute(
            "INSERT INTO categoria (nome, estoque_minimo_padrao) VALUES (?, ?)", (nome, minimo)
        ).lastrowid


def editar_categoria(con, categoria_id, nome, estoque_minimo_padrao=None):
    _existe(con, "categoria", categoria_id, "Categoria")
    nome = _texto(nome, "o nome da categoria")
    _nome_livre(con, "categoria", nome, ignorar_id=categoria_id)
    minimo = _numero_minimo(estoque_minimo_padrao, "O estoque mínimo padrão")
    with con:
        con.execute("UPDATE categoria SET nome = ?, estoque_minimo_padrao = ? WHERE id = ?",
                    (nome, minimo, categoria_id))


def listar_categorias(con):
    return con.execute(
        """SELECT c.*, (SELECT COUNT(*) FROM insumo i WHERE i.categoria_id = c.id AND i.ativo = 1) AS qtd_insumos
           FROM categoria c ORDER BY c.nome""").fetchall()


# ---------------------------------------------------------------------
# Fornecedores
# ---------------------------------------------------------------------

def criar_fornecedor(con, nome, tipo=None, telefone=None):
    nome = _texto(nome, "o nome do fornecedor")
    _nome_livre(con, "fornecedor", nome)
    with con:
        return con.execute(
            "INSERT INTO fornecedor (nome, tipo, telefone) VALUES (?, ?, ?)",
            (nome, _texto(tipo, "", False), _texto(telefone, "", False)),
        ).lastrowid


def editar_fornecedor(con, fornecedor_id, nome, tipo=None, telefone=None):
    _existe(con, "fornecedor", fornecedor_id, "Fornecedor")
    nome = _texto(nome, "o nome do fornecedor")
    _nome_livre(con, "fornecedor", nome, ignorar_id=fornecedor_id)
    with con:
        con.execute("UPDATE fornecedor SET nome = ?, tipo = ?, telefone = ? WHERE id = ?",
                    (nome, _texto(tipo, "", False), _texto(telefone, "", False), fornecedor_id))


def definir_ativo_fornecedor(con, fornecedor_id, ativo):
    """Desativar não apaga nada: o histórico de entradas continua apontando para ele."""
    _existe(con, "fornecedor", fornecedor_id, "Fornecedor")
    with con:
        con.execute("UPDATE fornecedor SET ativo = ? WHERE id = ?", (1 if ativo else 0, fornecedor_id))


def listar_fornecedores(con, incluir_inativos=False):
    return con.execute(
        """SELECT f.*, (SELECT COUNT(*) FROM insumo_fornecedor x
                        JOIN insumo i ON i.id = x.insumo_id
                        WHERE x.fornecedor_id = f.id AND i.ativo = 1) AS qtd_insumos
           FROM fornecedor f WHERE (? OR f.ativo = 1)
           ORDER BY f.ativo DESC, f.nome""", (1 if incluir_inativos else 0,)).fetchall()


# ---------------------------------------------------------------------
# Insumos
# ---------------------------------------------------------------------

def _limpar_insumo(con, nome, categoria_id, unidade_consumo, unidade_compra, fator_compra,
                   estoque_minimo, unidade_minimo, fornecedores_ids, ignorar_id=None):
    nome = _texto(nome, "o nome do insumo")
    _nome_livre(con, "insumo", nome, ignorar_id)

    if unidade_consumo not in UNIDADES_CONSUMO:
        raise ErroDeRegra("A unidade de consumo deve ser ml, g ou un.")
    unidade_compra = _texto(unidade_compra, "a unidade de compra (caixa, fardo, saco, litro...)")

    if isinstance(fator_compra, bool) or not isinstance(fator_compra, (int, float)) or fator_compra <= 0:
        raise ErroDeRegra("A quantidade por unidade de compra deve ser maior que zero.")

    if categoria_id is not None:
        _existe(con, "categoria", categoria_id, "Categoria")

    minimo = _numero_minimo(estoque_minimo, "O estoque mínimo")
    if minimo is not None:
        if unidade_minimo == "compra":
            minimo = minimo * fator_compra          # guarda sempre na unidade de consumo
        elif unidade_minimo != "consumo":
            raise ErroDeRegra("A medida do estoque mínimo deve ser 'consumo' ou 'compra'.")

    ids = sorted(set(fornecedores_ids or ()))
    for fid in ids:
        _existe(con, "fornecedor", fid, "Fornecedor")

    return dict(nome=nome, categoria_id=categoria_id, unidade_consumo=unidade_consumo,
                unidade_compra=unidade_compra, fator_compra=float(fator_compra),
                estoque_minimo=minimo, fornecedores_ids=ids)


def _sincronizar_fornecedores(con, insumo_id, novos_ids):
    atuais = {r[0] for r in con.execute(
        "SELECT fornecedor_id FROM insumo_fornecedor WHERE insumo_id = ?", (insumo_id,))}
    novos = set(novos_ids)
    for fid in atuais - novos:
        con.execute("DELETE FROM insumo_fornecedor WHERE insumo_id = ? AND fornecedor_id = ?", (insumo_id, fid))
    for fid in novos - atuais:
        con.execute("INSERT INTO insumo_fornecedor (insumo_id, fornecedor_id) VALUES (?, ?)", (insumo_id, fid))


def tem_historico(con, insumo_id):
    """True se o insumo já foi movimentado ou já está em alguma receita."""
    return con.execute(
        """SELECT EXISTS (SELECT 1 FROM movimentacao WHERE insumo_id = ?)
                OR EXISTS (SELECT 1 FROM receita_item WHERE insumo_id = ?)""",
        (insumo_id, insumo_id)).fetchone()[0] == 1


def criar_insumo(con, nome, categoria_id, unidade_consumo, unidade_compra, fator_compra=1,
                 estoque_minimo=None, unidade_minimo="consumo", fornecedores_ids=()):
    """Cadastra um insumo com saldo zero. O estoque entra depois, por uma movimentação de entrada."""
    d = _limpar_insumo(con, nome, categoria_id, unidade_consumo, unidade_compra, fator_compra,
                       estoque_minimo, unidade_minimo, fornecedores_ids)
    with con:
        insumo_id = con.execute(
            """INSERT INTO insumo (nome, categoria_id, unidade_consumo, unidade_compra, fator_compra, estoque_minimo)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (d["nome"], d["categoria_id"], d["unidade_consumo"], d["unidade_compra"],
             d["fator_compra"], d["estoque_minimo"])).lastrowid
        _sincronizar_fornecedores(con, insumo_id, d["fornecedores_ids"])
    return insumo_id


def editar_insumo(con, insumo_id, nome, categoria_id, unidade_consumo, unidade_compra, fator_compra=1,
                  estoque_minimo=None, unidade_minimo="consumo", fornecedores_ids=()):
    atual = con.execute("SELECT * FROM insumo WHERE id = ?", (insumo_id,)).fetchone()
    if atual is None:
        raise ErroDeRegra("Insumo não encontrado.")

    # Trocar ml por g (por exemplo) mudaria o significado de todos os saldos e receitas já gravados.
    if unidade_consumo != atual["unidade_consumo"] and tem_historico(con, insumo_id):
        raise ErroDeRegra(
            f"A unidade de consumo não pode mais ser alterada ({atual['unidade_consumo']}): "
            "este insumo já tem movimentações ou receitas. Cadastre um insumo novo se for outra medida.")

    d = _limpar_insumo(con, nome, categoria_id, unidade_consumo, unidade_compra, fator_compra,
                       estoque_minimo, unidade_minimo, fornecedores_ids, ignorar_id=insumo_id)
    with con:
        con.execute(
            """UPDATE insumo SET nome = ?, categoria_id = ?, unidade_consumo = ?, unidade_compra = ?,
                      fator_compra = ?, estoque_minimo = ? WHERE id = ?""",
            (d["nome"], d["categoria_id"], d["unidade_consumo"], d["unidade_compra"],
             d["fator_compra"], d["estoque_minimo"], insumo_id))
        _sincronizar_fornecedores(con, insumo_id, d["fornecedores_ids"])


def definir_ativo_insumo(con, insumo_id, ativo):
    """Desativar tira o insumo das telas; reativar traz de volta. Nada é apagado."""
    ins = con.execute("SELECT nome, unidade_consumo FROM insumo WHERE id = ?", (insumo_id,)).fetchone()
    if ins is None:
        raise ErroDeRegra("Insumo não encontrado.")

    if not ativo:
        saldo = con.execute("SELECT COALESCE(SUM(quantidade), 0) FROM saldo_estoque WHERE insumo_id = ?",
                            (insumo_id,)).fetchone()[0]
        if abs(saldo) > 1e-9:
            raise ErroDeRegra(
                f"Não dá para desativar {ins['nome']}: ainda há {saldo:g} {ins['unidade_consumo']} no sistema. "
                "Registre a saída ou faça o ajuste de inventário até zerar.")
        usado_em = [r["nome"] for r in con.execute(
            """SELECT DISTINCT p.nome FROM receita_item r JOIN produto p ON p.id = r.produto_id
               WHERE r.insumo_id = ? AND p.ativo = 1""", (insumo_id,))]
        if usado_em:
            raise ErroDeRegra(
                f"Não dá para desativar {ins['nome']}: ele ainda é usado na receita de "
                f"{', '.join(usado_em)}. Tire-o da receita ou desative o produto antes.")

    with con:
        con.execute("UPDATE insumo SET ativo = ? WHERE id = ?", (1 if ativo else 0, insumo_id))


def listar_insumos(con, incluir_inativos=False):
    return con.execute(
        """SELECT i.*, c.nome AS categoria,
                  COALESCE(i.estoque_minimo, c.estoque_minimo_padrao) AS minimo_efetivo,
                  (SELECT group_concat(f.nome, ', ') FROM insumo_fornecedor x
                   JOIN fornecedor f ON f.id = x.fornecedor_id WHERE x.insumo_id = i.id) AS fornecedores,
                  COALESCE((SELECT SUM(quantidade) FROM saldo_estoque s WHERE s.insumo_id = i.id), 0) AS saldo_total
           FROM insumo i LEFT JOIN categoria c ON c.id = i.categoria_id
           WHERE (? OR i.ativo = 1)
           ORDER BY i.ativo DESC, c.nome, i.nome""", (1 if incluir_inativos else 0,)).fetchall()


def dados_insumo(con, insumo_id):
    """Tudo que a tela de edição precisa: o insumo, seus fornecedores e se a unidade está travada."""
    ins = con.execute("SELECT * FROM insumo WHERE id = ?", (insumo_id,)).fetchone()
    if ins is None:
        return None
    ids = [r[0] for r in con.execute(
        "SELECT fornecedor_id FROM insumo_fornecedor WHERE insumo_id = ?", (insumo_id,))]
    return {"insumo": ins, "fornecedores_ids": ids, "tem_historico": tem_historico(con, insumo_id)}


# =====================================================================
# Produtos, receitas e promoções
# =====================================================================
import datetime as _dt

DIAS_NOMES = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"]     # 0 = domingo ... 6 = sábado


def dia_operacional(em=None):
    """Data do 'dia de trabalho'. O bar fecha às 3h, então 01h30 de quinta ainda é a noite de quarta."""
    em = em or _dt.datetime.now()
    return (em - _dt.timedelta(hours=6)).date()


def _numero_positivo(valor):
    return not isinstance(valor, bool) and isinstance(valor, (int, float)) and valor > 0


def _limpar_itens(con, itens):
    """Valida a receita. Cada item: {"insumo_id", "quantidade" (na unidade de consumo), "grupo_escolha"}."""
    if not itens:
        raise ErroDeRegra("A receita precisa de pelo menos um ingrediente.")
    vistos, limpos, grupos = set(), [], {}
    for n, it in enumerate(itens, start=1):
        ins = con.execute("SELECT id, nome, ativo FROM insumo WHERE id = ?", (it.get("insumo_id"),)).fetchone()
        if ins is None:
            raise ErroDeRegra(f"Ingrediente {n}: insumo não encontrado.")
        if not ins["ativo"]:
            raise ErroDeRegra(f"Ingrediente {n}: {ins['nome']} está desativado.")
        if not _numero_positivo(it.get("quantidade")):
            raise ErroDeRegra(f"Ingrediente {n} ({ins['nome']}): a quantidade deve ser maior que zero.")
        if ins["id"] in vistos:
            raise ErroDeRegra(f"{ins['nome']} aparece mais de uma vez na receita.")
        vistos.add(ins["id"])
        grupo = (it.get("grupo_escolha") or "").strip().lower() or None
        if grupo:
            grupos.setdefault(grupo, []).append(ins["nome"])
        limpos.append((ins["id"], float(it["quantidade"]), grupo))
    for grupo, nomes in grupos.items():
        if len(nomes) < 2:
            raise ErroDeRegra(
                f"O grupo de escolha '{grupo}' tem só {nomes[0]}. Use o mesmo nome em pelo menos dois "
                "ingredientes (ex.: vodka e cachaça) ou deixe em branco para um ingrediente fixo.")
    return limpos


def _limpar_produto(con, nome, categoria, preco_padrao, itens, ignorar_id=None):
    nome = _texto(nome, "o nome do produto")
    _nome_livre(con, "produto", nome, ignorar_id)
    categoria = _texto(categoria, "a categoria do produto (Drink, Kit, Cerveja, Chopp...)")
    if isinstance(preco_padrao, bool) or not isinstance(preco_padrao, (int, float)) or preco_padrao < 0:
        raise ErroDeRegra("O preço deve ser um número maior ou igual a zero.")
    return nome, categoria, float(preco_padrao), _limpar_itens(con, itens)


def criar_produto(con, nome, categoria, preco_padrao, itens):
    nome, categoria, preco, limpos = _limpar_produto(con, nome, categoria, preco_padrao, itens)
    with con:
        produto_id = con.execute(
            "INSERT INTO produto (nome, categoria, preco_padrao) VALUES (?, ?, ?)",
            (nome, categoria, preco)).lastrowid
        con.executemany(
            "INSERT INTO receita_item (produto_id, insumo_id, quantidade, grupo_escolha) VALUES (?, ?, ?, ?)",
            [(produto_id, i, q, g) for i, q, g in limpos])
    return produto_id


def editar_produto(con, produto_id, nome, categoria, preco_padrao, itens):
    _existe(con, "produto", produto_id, "Produto")
    nome, categoria, preco, limpos = _limpar_produto(con, nome, categoria, preco_padrao, itens,
                                                      ignorar_id=produto_id)
    with con:
        con.execute("UPDATE produto SET nome = ?, categoria = ?, preco_padrao = ? WHERE id = ?",
                    (nome, categoria, preco, produto_id))
        con.execute("DELETE FROM receita_item WHERE produto_id = ?", (produto_id,))
        con.executemany(
            "INSERT INTO receita_item (produto_id, insumo_id, quantidade, grupo_escolha) VALUES (?, ?, ?, ?)",
            [(produto_id, i, q, g) for i, q, g in limpos])


def definir_ativo_produto(con, produto_id, ativo):
    """Desativar tira o produto da venda; o histórico e a receita ficam guardados."""
    _existe(con, "produto", produto_id, "Produto")
    with con:
        con.execute("UPDATE produto SET ativo = ? WHERE id = ?", (1 if ativo else 0, produto_id))


def receita_do_produto(con, produto_id):
    return con.execute(
        """SELECT r.insumo_id, i.nome AS insumo, i.unidade_consumo AS unidade, r.quantidade, r.grupo_escolha
           FROM receita_item r JOIN insumo i ON i.id = r.insumo_id
           WHERE r.produto_id = ?
           ORDER BY r.grupo_escolha IS NOT NULL, r.grupo_escolha, i.nome""", (produto_id,)).fetchall()


def _num(v):
    return f"{v:g}"


def receita_resumida(linhas):
    """Texto da receita para as telas. Ingredientes fixos primeiro; depois cada grupo de escolha."""
    textos, grupos = [], {}
    for l in linhas:
        item = f"{_num(l['quantidade'])} {l['unidade']} de {l['insumo']}"
        if l["grupo_escolha"]:
            grupos.setdefault(l["grupo_escolha"], []).append(item)
        else:
            textos.append(item)
    for grupo, itens in grupos.items():
        textos.append(f"Escolha ({grupo}): " + " ou ".join(itens))
    return textos


def listar_produtos(con, incluir_inativos=False, em=None):
    produtos = []
    for p in con.execute("SELECT * FROM produto WHERE (? OR ativo = 1) ORDER BY ativo DESC, categoria, nome",
                         (1 if incluir_inativos else 0,)).fetchall():
        vigente = preco_vigente(con, p["id"], em)
        produtos.append({**dict(p), "preco_vigente": vigente["preco"], "origem_preco": vigente["origem"],
                         "receita": receita_resumida(receita_do_produto(con, p["id"]))})
    return produtos


# ---------------------------------------------------------------------
# Promoções (preço temporário, sem mexer no preço padrão)
# ---------------------------------------------------------------------

def _dias_texto(csv):
    if not csv:
        return "todos os dias"
    return ", ".join(DIAS_NOMES[int(d)] for d in csv.split(","))


def _promocao_vale(promo, dia):
    """A promoção vale no dia operacional `dia` (um objeto date)?"""
    if promo["dias_semana"] is not None:
        dia_semana = (dia.weekday() + 1) % 7                        # python: segunda = 0 -> aqui domingo = 0
        if dia_semana not in {int(d) for d in promo["dias_semana"].split(",")}:
            return False
    iso = dia.isoformat()
    if promo["data_inicio"] and iso < promo["data_inicio"]:
        return False
    if promo["data_fim"] and iso > promo["data_fim"]:
        return False
    return True


def preco_vigente(con, produto_id, em=None):
    """Preço que vale agora: a menor promoção ativa que vale hoje, ou o preço padrão."""
    produto = con.execute("SELECT preco_padrao FROM produto WHERE id = ?", (produto_id,)).fetchone()
    if produto is None:
        raise ErroDeRegra("Produto não encontrado.")
    dia = dia_operacional(em)
    validas = [p["preco"] for p in con.execute(
        "SELECT * FROM preco_promocao WHERE produto_id = ? AND ativo = 1", (produto_id,)) if _promocao_vale(p, dia)]
    if validas:
        return {"preco": min(validas), "origem": "promocao"}
    return {"preco": produto["preco_padrao"], "origem": "padrao"}


def _data_ou_none(valor, descricao):
    valor = (valor or "").strip()
    if not valor:
        return None
    try:
        return _dt.date.fromisoformat(valor).isoformat()
    except ValueError:
        raise ErroDeRegra(f"{descricao} inválida (use o formato AAAA-MM-DD).")


def criar_promocao(con, produto_id, preco, dias_semana=None, data_inicio=None, data_fim=None):
    """dias_semana: lista com 0=domingo ... 6=sábado. Precisa de dias e/ou de um período."""
    _existe(con, "produto", produto_id, "Produto")
    if isinstance(preco, bool) or not isinstance(preco, (int, float)) or preco < 0:
        raise ErroDeRegra("O preço da promoção deve ser um número maior ou igual a zero.")

    dias = sorted({int(d) for d in (dias_semana or [])})
    if any(d < 0 or d > 6 for d in dias):
        raise ErroDeRegra("Dia da semana inválido.")
    inicio = _data_ou_none(data_inicio, "Data de início")
    fim = _data_ou_none(data_fim, "Data de fim")
    if not dias and not inicio and not fim:
        raise ErroDeRegra("Informe os dias da semana e/ou um período. "
                          "Sem isso o preço mudaria para sempre (nesse caso, altere o preço do produto).")
    if inicio and fim and fim < inicio:
        raise ErroDeRegra("A data de fim não pode ser antes da data de início.")

    with con:
        return con.execute(
            """INSERT INTO preco_promocao (produto_id, preco, dias_semana, data_inicio, data_fim)
               VALUES (?, ?, ?, ?, ?)""",
            (produto_id, float(preco), ",".join(map(str, dias)) or None, inicio, fim)).lastrowid


def listar_promocoes(con, produto_id, em=None):
    dia = dia_operacional(em)
    return [{**dict(p), "dias_texto": _dias_texto(p["dias_semana"]), "vale_hoje": bool(p["ativo"]) and _promocao_vale(p, dia)}
            for p in con.execute("SELECT * FROM preco_promocao WHERE produto_id = ? ORDER BY ativo DESC, id",
                                 (produto_id,)).fetchall()]


def definir_ativo_promocao(con, promocao_id, ativo):
    _existe(con, "preco_promocao", promocao_id, "Promoção")
    with con:
        con.execute("UPDATE preco_promocao SET ativo = ? WHERE id = ?", (1 if ativo else 0, promocao_id))


def excluir_promocao(con, promocao_id):
    _existe(con, "preco_promocao", promocao_id, "Promoção")
    with con:
        con.execute("DELETE FROM preco_promocao WHERE id = ?", (promocao_id,))
