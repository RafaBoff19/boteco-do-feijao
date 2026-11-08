"""Compras com aprovação: o Gerente pede, o Dono aprova, e quem recebe a mercadoria dá entrada.

Caminho de uma compra:
    pendente --aprovar--> aprovada --receber--> recebida   (gera ENTRADAS no setor Estoque)
    pendente --recusar--> recusada                          (motivo obrigatório)
    pendente/aprovada --cancelar--> cancelada               (motivo obrigatório)

Na tela a pessoa pensa em unidade de COMPRA ("3 fardos"); no banco tudo fica na unidade
de CONSUMO (36 un), como no resto do sistema. A conversão usa o fator_compra do insumo.

Pedir e aprovar não mexem no estoque: só o recebimento gera movimentação.
"""
import math

import regras
from regras import ErroDeRegra, _fmt

PERFIL_DONO = "Dono"

# [ASSUMIDO] A sugestão de compra repõe até o DOBRO do mínimo (confirmar com o dono do bar).
FATOR_SUGESTAO = 2

STATUS_NOMES = {
    "pendente": "Aguardando aprovação",
    "aprovada": "Aprovada · a receber",
    "recusada": "Recusada",
    "recebida": "Recebida",
    "cancelada": "Cancelada",
}


# ---------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------

def _compra(con, compra_id):
    c = con.execute("SELECT * FROM compra WHERE id = ?", (compra_id,)).fetchone()
    if c is None:
        raise ErroDeRegra("Compra não encontrada.")
    return c


def _perfil_do_usuario(con, usuario_id):
    r = con.execute("SELECT p.nome FROM usuario u JOIN perfil p ON p.id = u.perfil_id WHERE u.id = ?",
                    (usuario_id,)).fetchone()
    return r[0] if r else None


def _exigir_motivo(motivo, acao):
    motivo = (motivo or "").strip()
    if not motivo:
        raise ErroDeRegra(f"Informe o motivo para {acao}.")
    return motivo


# ---------------------------------------------------------------------
# Sugestão: o que está no mínimo (ou negativo) e quanto comprar
# ---------------------------------------------------------------------

def sugestao_de_compra(con):
    """Insumos no mínimo ou abaixo, com a quantidade sugerida em unidade de compra.

    Sugere o suficiente para chegar a FATOR_SUGESTAO x o mínimo, arredondando para cima
    (não dá para comprar meio fardo).
    """
    linhas = con.execute(
        """SELECT v.insumo_id, v.insumo, v.unidade, v.saldo_total, v.minimo_efetivo,
                  i.unidade_compra, i.fator_compra,
                  (SELECT MIN(x.ultimo_preco) FROM insumo_fornecedor x
                   WHERE x.insumo_id = i.id AND x.ultimo_preco IS NOT NULL) AS ultimo_preco
           FROM v_saldo_total v JOIN insumo i ON i.id = v.insumo_id
           WHERE v.minimo_efetivo IS NOT NULL AND v.saldo_total <= v.minimo_efetivo
           ORDER BY v.insumo""").fetchall()
    sugestoes = []
    for r in linhas:
        falta = FATOR_SUGESTAO * r["minimo_efetivo"] - r["saldo_total"]
        sugestoes.append({
            **dict(r),
            "sugerido": max(1, math.ceil(round(falta / r["fator_compra"], 6))),   # em unidade de compra
        })
    return sugestoes


# ---------------------------------------------------------------------
# Pedir
# ---------------------------------------------------------------------

def criar_compra(con, itens, usuario_id, fornecedor_id=None, observacao=None):
    """Cria uma solicitação de compra (status pendente). Devolve o id.

    itens: lista de {"insumo_id", "quantidade" (em UNIDADE DE COMPRA), "preco_estimado" (opcional,
    por unidade de compra)}.
    """
    regras._validar_usuario(con, usuario_id)
    regras._validar_fornecedor(con, fornecedor_id)
    if not itens:
        raise ErroDeRegra("Adicione pelo menos um item à compra.")

    preparados, vistos = [], set()
    for n, item in enumerate(itens, start=1):
        ins = con.execute("SELECT * FROM insumo WHERE id = ? AND ativo = 1", (item.get("insumo_id"),)).fetchone()
        if ins is None:
            raise ErroDeRegra(f"Item {n}: insumo não encontrado ou inativo.")
        if ins["id"] in vistos:
            raise ErroDeRegra(f"{ins['nome']} aparece duas vezes. Junte as quantidades em uma linha só.")
        vistos.add(ins["id"])
        qtd = item.get("quantidade")
        if qtd is None or qtd <= 0:
            raise ErroDeRegra(f"{ins['nome']}: a quantidade deve ser maior que zero.")
        preco = item.get("preco_estimado")
        if preco is not None and preco < 0:
            raise ErroDeRegra(f"{ins['nome']}: o preço não pode ser negativo.")
        preparados.append((ins["id"], qtd * ins["fator_compra"], preco))

    with con:
        cur = con.execute(
            "INSERT INTO compra (fornecedor_id, observacao, solicitada_por) VALUES (?, ?, ?)",
            (fornecedor_id, (observacao or "").strip() or None, usuario_id))
        compra_id = cur.lastrowid
        con.executemany(
            "INSERT INTO compra_item (compra_id, insumo_id, quantidade, preco_estimado) VALUES (?, ?, ?, ?)",
            [(compra_id, *p) for p in preparados])
    return compra_id


# ---------------------------------------------------------------------
# Decidir
# ---------------------------------------------------------------------

def aprovar(con, compra_id, usuario_id):
    regras._validar_usuario(con, usuario_id)
    c = _compra(con, compra_id)
    if c["status"] != "pendente":
        raise ErroDeRegra("Só dá para aprovar uma compra que está aguardando aprovação.")
    # quem pediu não aprova o próprio pedido (a não ser o Dono, que é quem aprova de qualquer jeito)
    if c["solicitada_por"] == usuario_id and _perfil_do_usuario(con, usuario_id) != PERFIL_DONO:
        raise ErroDeRegra("Você não pode aprovar uma compra que você mesmo pediu.")
    with con:
        con.execute("UPDATE compra SET status = 'aprovada', decidida_por = ?, "
                    "decidida_em = datetime('now', 'localtime') WHERE id = ?", (usuario_id, compra_id))


def recusar(con, compra_id, usuario_id, motivo):
    regras._validar_usuario(con, usuario_id)
    c = _compra(con, compra_id)
    if c["status"] != "pendente":
        raise ErroDeRegra("Só dá para recusar uma compra que está aguardando aprovação.")
    motivo = _exigir_motivo(motivo, "recusar")
    with con:
        con.execute("UPDATE compra SET status = 'recusada', decidida_por = ?, motivo = ?, "
                    "decidida_em = datetime('now', 'localtime') WHERE id = ?", (usuario_id, motivo, compra_id))


def cancelar(con, compra_id, usuario_id, motivo):
    regras._validar_usuario(con, usuario_id)
    c = _compra(con, compra_id)
    if c["status"] not in ("pendente", "aprovada"):
        raise ErroDeRegra("Esta compra já foi encerrada e não pode ser cancelada.")
    motivo = _exigir_motivo(motivo, "cancelar")
    with con:
        con.execute("UPDATE compra SET status = 'cancelada', decidida_por = ?, motivo = ?, "
                    "decidida_em = datetime('now', 'localtime') WHERE id = ?", (usuario_id, motivo, compra_id))


# ---------------------------------------------------------------------
# Receber: aqui o estoque muda
# ---------------------------------------------------------------------

def receber(con, compra_id, usuario_id, recebidos, setor="Estoque"):
    """Confere a mercadoria e dá entrada no estoque.

    recebidos: {compra_item_id: {"quantidade": em UNIDADE DE COMPRA (0 = não veio),
                                 "preco_pago": por unidade de compra, opcional}}
    Item que não aparecer em `recebidos` conta como recebido na quantidade pedida.
    Tudo numa transação só: ou entram todos os itens, ou nenhum.
    """
    regras._validar_usuario(con, usuario_id)
    c = _compra(con, compra_id)
    if c["status"] != "aprovada":
        raise ErroDeRegra("Só dá para receber uma compra aprovada.")
    st = regras._buscar_setor(con, setor)
    itens = con.execute(
        """SELECT ci.*, i.nome, i.fator_compra, i.unidade_compra FROM compra_item ci
           JOIN insumo i ON i.id = ci.insumo_id WHERE ci.compra_id = ? ORDER BY ci.id""", (compra_id,)).fetchall()

    plano = []
    for it in itens:
        info = recebidos.get(it["id"], {})
        qtd_compra = info.get("quantidade", it["quantidade"] / it["fator_compra"])
        preco = info.get("preco_pago")
        if qtd_compra is None or qtd_compra < 0:
            raise ErroDeRegra(f"{it['nome']}: informe quanto chegou (0 se não veio).")
        if preco is not None and preco < 0:
            raise ErroDeRegra(f"{it['nome']}: o preço não pode ser negativo.")
        plano.append((it, qtd_compra * it["fator_compra"], preco))
    if not any(qtd > 0 for _, qtd, _ in plano):
        raise ErroDeRegra("Nenhum item chegou. Se a compra não vai acontecer, cancele-a.")

    mov_ids, avisos = [], []
    motivo = f"Compra nº {compra_id}"
    with con:
        for it, qtd, preco in plano:
            con.execute("UPDATE compra_item SET quantidade_recebida = ?, preco_pago = ? WHERE id = ?",
                        (qtd, preco, it["id"]))
            if qtd <= 0:
                continue
            mov_ids.append(regras._inserir(con, it["insumo_id"], st["id"], "entrada", qtd, usuario_id,
                                           motivo=motivo, fornecedor_id=c["fornecedor_id"],
                                           compra_item_id=it["id"]))
            if c["fornecedor_id"] is not None and preco is not None:
                # guarda o último preço pago naquele fornecedor (e liga o insumo a ele, se ainda não era)
                con.execute(
                    """INSERT INTO insumo_fornecedor (insumo_id, fornecedor_id, ultimo_preco) VALUES (?, ?, ?)
                       ON CONFLICT (insumo_id, fornecedor_id) DO UPDATE SET ultimo_preco = excluded.ultimo_preco""",
                    (it["insumo_id"], c["fornecedor_id"], preco))
            avisos += regras._checar_alertas(con, it["insumo_id"], [st["id"]])
        con.execute("UPDATE compra SET status = 'recebida', recebida_por = ?, "
                    "recebida_em = datetime('now', 'localtime') WHERE id = ?", (usuario_id, compra_id))

    faltas = [f"{it['nome']}: pedido {_fmt(it['quantidade'] / it['fator_compra'])}, "
              f"chegou {_fmt(qtd / it['fator_compra'])} {it['unidade_compra']}"
              for it, qtd, _ in plano if qtd + 1e-9 < it["quantidade"]]
    return {"movimentacao_ids": mov_ids, "avisos": avisos, "faltas": faltas}


# ---------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------

def listar_compras(con, status=None, limite=100):
    """status: um status, uma lista de status, ou None para todas."""
    filtro, params = "", []
    if status:
        lista = [status] if isinstance(status, str) else list(status)
        filtro = f"WHERE c.status IN ({', '.join('?' * len(lista))})"
        params = lista
    return con.execute(
        f"""SELECT c.*, f.nome AS fornecedor, us.nome AS solicitante, ud.nome AS decisor,
                   (SELECT COUNT(*) FROM compra_item ci WHERE ci.compra_id = c.id) AS qtd_itens,
                   (SELECT SUM(ci.quantidade / i.fator_compra * ci.preco_estimado)
                      FROM compra_item ci JOIN insumo i ON i.id = ci.insumo_id
                     WHERE ci.compra_id = c.id) AS total_estimado,
                   (SELECT group_concat(i.nome, ', ') FROM compra_item ci JOIN insumo i ON i.id = ci.insumo_id
                     WHERE ci.compra_id = c.id) AS resumo
            FROM compra c
            LEFT JOIN fornecedor f ON f.id = c.fornecedor_id
            JOIN usuario us ON us.id = c.solicitada_por
            LEFT JOIN usuario ud ON ud.id = c.decidida_por
            {filtro}
            ORDER BY CASE c.status WHEN 'pendente' THEN 0 WHEN 'aprovada' THEN 1 ELSE 2 END,
                     c.solicitada_em DESC, c.id DESC
            LIMIT ?""", (*params, limite)).fetchall()


def dados_compra(con, compra_id):
    c = con.execute(
        """SELECT c.*, f.nome AS fornecedor, us.nome AS solicitante, ud.nome AS decisor, ur.nome AS recebedor
           FROM compra c
           LEFT JOIN fornecedor f ON f.id = c.fornecedor_id
           JOIN usuario us ON us.id = c.solicitada_por
           LEFT JOIN usuario ud ON ud.id = c.decidida_por
           LEFT JOIN usuario ur ON ur.id = c.recebida_por
           WHERE c.id = ?""", (compra_id,)).fetchone()
    if c is None:
        return None
    itens = []
    for r in con.execute(
            """SELECT ci.*, i.nome, i.unidade_compra, i.unidade_consumo, i.fator_compra,
                      COALESCE((SELECT SUM(s.quantidade) FROM saldo_estoque s WHERE s.insumo_id = i.id), 0) AS saldo
               FROM compra_item ci JOIN insumo i ON i.id = ci.insumo_id
               WHERE ci.compra_id = ? ORDER BY i.nome""", (compra_id,)):
        d = dict(r)
        d["qtd_compra"] = r["quantidade"] / r["fator_compra"]
        d["recebida_compra"] = (None if r["quantidade_recebida"] is None
                                else r["quantidade_recebida"] / r["fator_compra"])
        d["subtotal"] = None if r["preco_estimado"] is None else d["qtd_compra"] * r["preco_estimado"]
        itens.append(d)
    subtotais = [i["subtotal"] for i in itens if i["subtotal"] is not None]
    return {"compra": c, "itens": itens, "total_estimado": sum(subtotais) if subtotais else None,
            "status_nome": STATUS_NOMES[c["status"]]}


def contar_pendencias(con):
    """Quantas compras esperam aprovação e quantas esperam chegar (para o número no menu)."""
    contagem = {"pendente": 0, "aprovada": 0}
    for r in con.execute("SELECT status, COUNT(*) FROM compra WHERE status IN ('pendente', 'aprovada') "
                         "GROUP BY status"):
        contagem[r[0]] = r[1]
    return contagem
