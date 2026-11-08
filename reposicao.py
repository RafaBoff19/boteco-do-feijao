"""Comunicação Bar <-> Estoque: o Bar pede reposição e o Estoque responde.

Caminho de um pedido:
    pendente --enviar--> atendida    (gera TRANSFERÊNCIAS do Estoque para o Bar)
    pendente --recusar-> recusada    (motivo obrigatório)
    pendente --cancelar-> cancelada  (só quem pediu)

Pedir não mexe no estoque: só o envio gera movimentação. Se o Estoque não tiver o
suficiente, o envio é gravado mesmo assim, deixa o saldo negativo e avisa, como em
todo o sistema (estoque negativo nunca bloqueia).
"""
import regras
from regras import ErroDeRegra, _fmt

SETOR_ORIGEM = "Estoque"
SETOR_DESTINO = "Bar"

STATUS_NOMES = {
    "pendente": "Aguardando o Estoque",
    "atendida": "Enviada",
    "recusada": "Recusada",
    "cancelada": "Cancelada",
}


def _reposicao(con, reposicao_id):
    r = con.execute("SELECT * FROM reposicao WHERE id = ?", (reposicao_id,)).fetchone()
    if r is None:
        raise ErroDeRegra("Pedido de reposição não encontrado.")
    return r


def _marcar(con, reposicao_id, status, usuario_id, resposta):
    """Muda de 'pendente' para o novo status, dentro da transação de quem chamou.

    O WHERE status='pendente' impede que dois toques seguidos façam o envio duas vezes:
    o segundo não acha mais nada pendente e a operação inteira é desfeita.
    """
    cur = con.execute(
        "UPDATE reposicao SET status = ?, respondida_por = ?, resposta = ?, "
        "respondida_em = datetime('now', 'localtime') WHERE id = ? AND status = 'pendente'",
        (status, usuario_id, resposta, reposicao_id))
    if cur.rowcount != 1:
        raise ErroDeRegra("Este pedido já foi respondido.")


def criar(con, itens, usuario_id, urgente=False, observacao=None):
    """Cria um pedido de reposição. Devolve o id.

    itens: lista de {"insumo_id", "quantidade", "unidade" ('consumo' ou 'compra')}.
    """
    regras._validar_usuario(con, usuario_id)
    if not itens:
        raise ErroDeRegra("Adicione pelo menos um item ao pedido.")

    preparados, vistos = [], set()
    for n, item in enumerate(itens, start=1):
        ins = con.execute("SELECT * FROM insumo WHERE id = ? AND ativo = 1", (item.get("insumo_id"),)).fetchone()
        if ins is None:
            raise ErroDeRegra(f"Item {n}: insumo não encontrado ou inativo.")
        if ins["id"] in vistos:
            raise ErroDeRegra(f"{ins['nome']} aparece duas vezes. Junte as quantidades em uma linha só.")
        vistos.add(ins["id"])
        qtd, unidade = item.get("quantidade"), item.get("unidade", "consumo")
        if qtd is None or qtd <= 0:
            raise ErroDeRegra(f"{ins['nome']}: a quantidade deve ser maior que zero.")
        preparados.append((ins["id"], regras._para_consumo(ins, qtd, unidade), unidade))

    with con:
        rid = con.execute("INSERT INTO reposicao (urgente, observacao, solicitada_por) VALUES (?, ?, ?)",
                          (1 if urgente else 0, (observacao or "").strip() or None, usuario_id)).lastrowid
        con.executemany("INSERT INTO reposicao_item (reposicao_id, insumo_id, quantidade, unidade) VALUES (?, ?, ?, ?)",
                        [(rid, *p) for p in preparados])
    return rid


def atender(con, reposicao_id, usuario_id, enviados, resposta=None):
    """Envia a mercadoria: uma transferência Estoque -> Bar por item, tudo numa transação só.

    enviados: {reposicao_item_id: quantidade, na mesma unidade em que foi pedida (0 = não enviou)}.
    Item que não aparecer em `enviados` conta como enviado na quantidade pedida.
    """
    regras._validar_usuario(con, usuario_id)
    r = _reposicao(con, reposicao_id)
    if r["status"] != "pendente":
        raise ErroDeRegra("Este pedido já foi respondido.")
    origem, destino = regras._buscar_setor(con, SETOR_ORIGEM), regras._buscar_setor(con, SETOR_DESTINO)
    itens = con.execute(
        """SELECT ri.*, i.nome, i.fator_compra, i.unidade_compra, i.unidade_consumo
           FROM reposicao_item ri JOIN insumo i ON i.id = ri.insumo_id
           WHERE ri.reposicao_id = ? ORDER BY ri.id""", (reposicao_id,)).fetchall()

    plano = []
    for it in itens:
        fator = it["fator_compra"] if it["unidade"] == "compra" else 1
        enviado = enviados.get(it["id"], it["quantidade"] / fator)
        if enviado is None or enviado < 0:
            raise ErroDeRegra(f"{it['nome']}: informe quanto foi enviado (0 se não enviou).")
        plano.append((it, enviado * fator, fator))
    if not any(q > 0 for _, q, _ in plano):
        raise ErroDeRegra("Nenhum item foi enviado. Se não vai atender, recuse o pedido informando o motivo.")

    mov_ids, avisos = [], []
    motivo = f"Reposição nº {reposicao_id}"
    with con:
        _marcar(con, reposicao_id, "atendida", usuario_id, (resposta or "").strip() or None)
        for it, qtd, _ in plano:
            con.execute("UPDATE reposicao_item SET quantidade_enviada = ? WHERE id = ?", (qtd, it["id"]))
            if qtd <= 0:
                continue
            tid = con.execute("SELECT COALESCE(MAX(transferencia_id), 0) + 1 FROM movimentacao").fetchone()[0]
            for setor, sinal in ((origem["id"], -1), (destino["id"], 1)):
                mov_ids.append(regras._inserir(con, it["insumo_id"], setor, "transferencia", sinal * qtd, usuario_id,
                                               motivo=motivo, transferencia_id=tid, reposicao_item_id=it["id"]))
            avisos += regras._checar_alertas(con, it["insumo_id"], [origem["id"], destino["id"]])

    faltas = []
    for it, qtd, fator in plano:
        if qtd + 1e-9 < it["quantidade"]:
            un = it["unidade_compra"] if it["unidade"] == "compra" else it["unidade_consumo"]
            faltas.append(f"{it['nome']}: pedido {_fmt(it['quantidade'] / fator)}, enviado {_fmt(qtd / fator)} {un}")
    return {"movimentacao_ids": mov_ids, "avisos": avisos, "faltas": faltas}


def recusar(con, reposicao_id, usuario_id, motivo):
    regras._validar_usuario(con, usuario_id)
    _reposicao(con, reposicao_id)
    motivo = (motivo or "").strip()
    if not motivo:
        raise ErroDeRegra("Informe o motivo para recusar.")
    with con:
        _marcar(con, reposicao_id, "recusada", usuario_id, motivo)


def cancelar(con, reposicao_id, usuario_id):
    """Só quem pediu pode desistir, e só enquanto ninguém respondeu."""
    regras._validar_usuario(con, usuario_id)
    if _reposicao(con, reposicao_id)["solicitada_por"] != usuario_id:
        raise ErroDeRegra("Só quem fez o pedido pode cancelá-lo.")
    with con:
        _marcar(con, reposicao_id, "cancelada", usuario_id, None)


# ---------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------

def listar(con, so_pendentes=True, limite=100):
    filtro = "WHERE r.status = 'pendente'" if so_pendentes else ""
    return con.execute(
        f"""SELECT r.*, us.nome AS solicitante, ur.nome AS respondente,
                   (SELECT group_concat(i.nome, ', ') FROM reposicao_item ri JOIN insumo i ON i.id = ri.insumo_id
                     WHERE ri.reposicao_id = r.id) AS resumo
            FROM reposicao r
            JOIN usuario us ON us.id = r.solicitada_por
            LEFT JOIN usuario ur ON ur.id = r.respondida_por
            {filtro}
            ORDER BY CASE r.status WHEN 'pendente' THEN 0 ELSE 1 END,
                     CASE WHEN r.status = 'pendente' THEN r.urgente ELSE 0 END DESC,
                     CASE WHEN r.status = 'pendente' THEN r.solicitada_em END ASC,
                     r.solicitada_em DESC, r.id DESC
            LIMIT ?""", (limite,)).fetchall()


def dados(con, reposicao_id):
    r = con.execute(
        """SELECT r.*, us.nome AS solicitante, ur.nome AS respondente
           FROM reposicao r JOIN usuario us ON us.id = r.solicitada_por
           LEFT JOIN usuario ur ON ur.id = r.respondida_por WHERE r.id = ?""", (reposicao_id,)).fetchone()
    if r is None:
        return None
    itens = []
    for it in con.execute(
            """SELECT ri.*, i.nome, i.fator_compra, i.unidade_compra, i.unidade_consumo,
                      COALESCE((SELECT s.quantidade FROM saldo_estoque s JOIN setor st ON st.id = s.setor_id
                                 WHERE s.insumo_id = i.id AND st.nome = ?), 0) AS no_estoque,
                      COALESCE((SELECT s.quantidade FROM saldo_estoque s JOIN setor st ON st.id = s.setor_id
                                 WHERE s.insumo_id = i.id AND st.nome = ?), 0) AS no_bar
               FROM reposicao_item ri JOIN insumo i ON i.id = ri.insumo_id
               WHERE ri.reposicao_id = ? ORDER BY i.nome""", (SETOR_ORIGEM, SETOR_DESTINO, reposicao_id)):
        d = dict(it)
        fator = it["fator_compra"] if it["unidade"] == "compra" else 1
        d["nome_unidade"] = it["unidade_compra"] if it["unidade"] == "compra" else it["unidade_consumo"]
        d["pedida"] = it["quantidade"] / fator
        d["enviada"] = None if it["quantidade_enviada"] is None else it["quantidade_enviada"] / fator
        itens.append(d)
    return {"reposicao": r, "itens": itens, "status_nome": STATUS_NOMES[r["status"]]}


def contar_pendentes(con):
    return con.execute("SELECT COUNT(*) FROM reposicao WHERE status = 'pendente'").fetchone()[0]


def opcoes_de_insumos(con):
    """Insumos para o formulário, com o saldo de cada setor para ajudar a decidir quanto pedir."""
    return con.execute(
        """SELECT i.id, i.nome, i.unidade_consumo, i.unidade_compra, i.fator_compra,
                  COALESCE(cat.nome, 'Sem categoria') AS categoria,
                  COALESCE((SELECT s.quantidade FROM saldo_estoque s JOIN setor st ON st.id = s.setor_id
                             WHERE s.insumo_id = i.id AND st.nome = ?), 0) AS no_estoque,
                  COALESCE((SELECT s.quantidade FROM saldo_estoque s JOIN setor st ON st.id = s.setor_id
                             WHERE s.insumo_id = i.id AND st.nome = ?), 0) AS no_bar
           FROM insumo i LEFT JOIN categoria cat ON cat.id = i.categoria_id
           WHERE i.ativo = 1 ORDER BY categoria, i.nome""", (SETOR_ORIGEM, SETOR_DESTINO)).fetchall()
