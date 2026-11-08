"""Comandas, pedidos e baixa de estoque por entrega (Fase 2).

Fluxo:  abrir_comanda -> adicionar_item (status 'pedido') -> entregar_item (ITEM ENTREGUE: baixa dos ingredientes)
        -> fechar_comanda.

Regras principais:
  * O que o item consome é "fotografado" na hora do pedido (pedido_item_consumo); mudar a receita depois
    não altera pedidos já feitos. O preço também é fotografado (preço vigente, com promoção).
  * A baixa só acontece na ENTREGA. Cancelar um item ainda não entregue não mexe no estoque.
  * Baixa: tira primeiro do BAR; o que faltar sai do ESTOQUE; se ainda faltar, o Bar fica negativo
    (nunca bloqueia, só avisa e gera alerta).
  * Cancelar item já entregue exige motivo e devolve os ingredientes (movimentação 'estorno') aos
    mesmos setores de onde saíram.
"""
import cadastros
import regras
from regras import ErroDeRegra

SETOR_PRINCIPAL = "Bar"
SETOR_RESERVA = "Estoque"

# Pedidos pelo QR Code (Passo 10)
LOGIN_QRCODE = "qrcode"          # usuário de sistema (inativo) que "assina" o que o cliente faz
MAX_QTD_CLIENTE = 10             # o cliente pede no máximo 10 unidades por vez
MAX_AGUARDANDO_CLIENTE = 10      # e no máximo 10 itens esperando o garçom (evita trote/spam)

# item pedido pelo cliente e que o garçom ainda não confirmou
SQL_AGUARDANDO = "(pi.origem = 'cliente' AND pi.confirmado_em IS NULL AND pi.status = 'pedido')"


def _comanda(con, comanda_id):
    c = con.execute("SELECT * FROM comanda WHERE id = ?", (comanda_id,)).fetchone()
    if c is None:
        raise ErroDeRegra("Comanda não encontrada.")
    return c


def _item(con, item_id):
    i = con.execute("SELECT * FROM pedido_item WHERE id = ?", (item_id,)).fetchone()
    if i is None:
        raise ErroDeRegra("Item não encontrado.")
    return i


def _fmt_q(v):
    return f"{v:g}".replace(".", ",")


# ---------------------------------------------------------------------
# Comandas
# ---------------------------------------------------------------------
def abrir_comanda(con, identificacao, usuario_id, mesa=None):
    identificacao = (identificacao or "").strip()
    if not identificacao:
        raise ErroDeRegra("Informe a identificação da comanda (nome do cliente ou número).")
    if mesa is not None:
        if isinstance(mesa, bool) or not isinstance(mesa, int) or mesa < 1:
            raise ErroDeRegra("Mesa inválida: use um número inteiro a partir de 1.")
    regras._validar_usuario(con, usuario_id)
    with con:
        cur = con.execute(
            "INSERT INTO comanda (identificacao, mesa, aberta_por) VALUES (?, ?, ?)",
            (identificacao, mesa, usuario_id))
    return cur.lastrowid


def adicionar_item(con, comanda_id, produto_id, quantidade, usuario_id, escolhas=None, observacao=None,
                   origem="equipe"):
    """escolhas: {grupo: insumo_id} — obrigatório para cada grupo de escolha da receita.

    origem='cliente' é usado só por pedir_pelo_qrcode: o item nasce aguardando o garçom.
    """
    if isinstance(quantidade, bool) or not isinstance(quantidade, int) or quantidade < 1:
        raise ErroDeRegra("Quantidade inválida: use um número inteiro a partir de 1.")
    if origem not in ("equipe", "cliente"):
        raise ErroDeRegra("Origem do pedido inválida.")
    if origem == "equipe":
        regras._validar_usuario(con, usuario_id)   # o usuário do QR Code é inativo de propósito
    c = _comanda(con, comanda_id)
    if c["status"] != "aberta":
        raise ErroDeRegra("Esta comanda não está aberta.")
    p = con.execute("SELECT * FROM produto WHERE id = ?", (produto_id,)).fetchone()
    if p is None:
        raise ErroDeRegra("Produto não encontrado.")
    if not p["ativo"]:
        raise ErroDeRegra(f"O produto '{p['nome']}' está desativado.")

    receita = cadastros.receita_do_produto(con, produto_id)
    escolhas = {str(k).strip().lower(): v for k, v in (escolhas or {}).items()}
    consumo, nomes_escolhidos = {}, []
    grupos = {}
    for r in receita:
        if r["grupo_escolha"] is None:
            consumo[r["insumo_id"]] = consumo.get(r["insumo_id"], 0) + r["quantidade"] * quantidade
        else:
            grupos.setdefault(r["grupo_escolha"], []).append(r)
    for grupo, opcoes in grupos.items():
        escolhido = escolhas.get(grupo)
        try:
            escolhido = int(escolhido)
        except (TypeError, ValueError):
            escolhido = None
        opcao = next((o for o in opcoes if o["insumo_id"] == escolhido), None)
        if opcao is None:
            nomes = ", ".join(o["insumo"] for o in opcoes)
            raise ErroDeRegra(f"Escolha uma opção de '{grupo}' ({nomes}).")
        consumo[opcao["insumo_id"]] = consumo.get(opcao["insumo_id"], 0) + opcao["quantidade"] * quantidade
        nomes_escolhidos.append(opcao["insumo"])
    if not consumo:
        raise ErroDeRegra("Este produto não tem receita cadastrada.")

    preco = cadastros.preco_vigente(con, produto_id)["preco"]
    obs = (observacao or "").strip() or None
    with con:
        cur = con.execute(
            """INSERT INTO pedido_item (comanda_id, produto_id, quantidade, preco_unitario, escolhas, observacao,
                                        criado_por, origem)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (comanda_id, produto_id, quantidade, preco, ", ".join(nomes_escolhidos) or None, obs, usuario_id,
             origem))
        item_id = cur.lastrowid
        con.executemany(
            "INSERT INTO pedido_item_consumo (pedido_item_id, insumo_id, quantidade) VALUES (?, ?, ?)",
            [(item_id, ins, q) for ins, q in consumo.items()])
    return item_id


def _consumo_do_item(con, item_id):
    return con.execute(
        """SELECT c.insumo_id, c.quantidade, i.nome, i.unidade_consumo AS unidade
           FROM pedido_item_consumo c JOIN insumo i ON i.id = c.insumo_id
           WHERE c.pedido_item_id = ? ORDER BY i.nome""", (item_id,)).fetchall()


def entregar_item(con, item_id, usuario_id):
    """ITEM ENTREGUE: dá baixa nos ingredientes (Bar primeiro, depois Estoque, depois Bar negativo)."""
    regras._validar_usuario(con, usuario_id)
    item = _item(con, item_id)
    if item["status"] == "entregue":
        raise ErroDeRegra("Este item já foi entregue (a baixa já foi feita).")
    if item["status"] == "cancelado":
        raise ErroDeRegra("Este item foi cancelado.")
    if item["origem"] == "cliente" and item["confirmado_em"] is None:
        raise ErroDeRegra("Este pedido do QR Code ainda não foi confirmado pelo garçom.")
    if _comanda(con, item["comanda_id"])["status"] != "aberta":
        raise ErroDeRegra("A comanda deste item não está aberta.")
    bar = regras._buscar_setor(con, SETOR_PRINCIPAL)
    reserva = regras._buscar_setor(con, SETOR_RESERVA)

    avisos, mov_ids = [], []
    with con:
        for c in _consumo_do_item(con, item_id):
            falta = c["quantidade"]
            saldos = {s["setor_id"]: s["quantidade"] for s in con.execute(
                "SELECT setor_id, quantidade FROM saldo_estoque WHERE insumo_id = ?", (c["insumo_id"],))}
            no_bar = max(saldos.get(bar["id"], 0), 0)
            tirar_bar = min(falta, no_bar)
            falta -= tirar_bar
            no_res = max(saldos.get(reserva["id"], 0), 0)
            tirar_res = min(falta, no_res)
            falta -= tirar_res
            tirar_bar += falta            # o que sobrar deixa o Bar negativo
            usados = []
            if tirar_bar > 0:
                mov_ids.append(regras._inserir(con, c["insumo_id"], bar["id"], "baixa_pedido", -tirar_bar,
                                               usuario_id, pedido_item_id=item_id))
                usados.append(bar["id"])
            if tirar_res > 0:
                mov_ids.append(regras._inserir(con, c["insumo_id"], reserva["id"], "baixa_pedido", -tirar_res,
                                               usuario_id, pedido_item_id=item_id))
                usados.append(reserva["id"])
            avisos += regras._checar_alertas(con, c["insumo_id"], usados)
        con.execute(
            """UPDATE pedido_item SET status = 'entregue', entregue_por = ?, entregue_em = datetime('now','localtime')
               WHERE id = ?""", (usuario_id, item_id))
    return {"movimentacao_ids": mov_ids, "avisos": avisos}


def cancelar_item(con, item_id, usuario_id, motivo=None):
    regras._validar_usuario(con, usuario_id)
    item = _item(con, item_id)
    if item["status"] == "cancelado":
        raise ErroDeRegra("Este item já está cancelado.")
    if _comanda(con, item["comanda_id"])["status"] != "aberta":
        raise ErroDeRegra("A comanda deste item não está aberta.")
    motivo = (motivo or "").strip() or None
    entregue = item["status"] == "entregue"
    if entregue and not motivo:
        raise ErroDeRegra("Informe o motivo para cancelar um item já entregue.")
    mov_ids, avisos = [], []
    with con:
        if entregue:
            baixas = con.execute(
                """SELECT insumo_id, setor_id, SUM(quantidade) AS q FROM movimentacao
                   WHERE pedido_item_id = ? GROUP BY insumo_id, setor_id""", (item_id,)).fetchall()
            for b in baixas:
                if b["q"] < 0:
                    mov_ids.append(regras._inserir(con, b["insumo_id"], b["setor_id"], "estorno", -b["q"],
                                                   usuario_id, motivo=f"Cancelamento de item entregue: {motivo}",
                                                   pedido_item_id=item_id))
        con.execute(
            """UPDATE pedido_item SET status = 'cancelado', cancelado_por = ?, cancelado_em = datetime('now','localtime'),
               motivo_cancelamento = ? WHERE id = ?""", (usuario_id, motivo, item_id))
    return {"movimentacao_ids": mov_ids, "avisos": avisos}


def fechar_comanda(con, comanda_id, usuario_id):
    regras._validar_usuario(con, usuario_id)
    c = _comanda(con, comanda_id)
    if c["status"] != "aberta":
        raise ErroDeRegra("Esta comanda não está aberta.")
    pend = con.execute("SELECT COUNT(*) FROM pedido_item WHERE comanda_id = ? AND status = 'pedido'",
                       (comanda_id,)).fetchone()[0]
    if pend:
        raise ErroDeRegra(f"Há {pend} item(ns) ainda não entregue(s). Entregue ou cancele antes de fechar.")
    with con:
        con.execute("UPDATE comanda SET status='fechada', fechada_em=datetime('now','localtime') WHERE id=?",
                    (comanda_id,))
    return {"total": total_comanda(con, comanda_id)}


def cancelar_comanda(con, comanda_id, usuario_id):
    regras._validar_usuario(con, usuario_id)
    c = _comanda(con, comanda_id)
    if c["status"] != "aberta":
        raise ErroDeRegra("Esta comanda não está aberta.")
    ent = con.execute("SELECT COUNT(*) FROM pedido_item WHERE comanda_id = ? AND status = 'entregue'",
                      (comanda_id,)).fetchone()[0]
    if ent:
        raise ErroDeRegra("Há itens já entregues. Cancele-os (com motivo) antes de cancelar a comanda, "
                          "ou feche a comanda.")
    with con:
        con.execute("UPDATE pedido_item SET status='cancelado', cancelado_por=?, cancelado_em=datetime('now','localtime'), "
                    "motivo_cancelamento='Comanda cancelada' WHERE comanda_id=? AND status='pedido'",
                    (usuario_id, comanda_id))
        con.execute("UPDATE comanda SET status='cancelada', fechada_em=datetime('now','localtime') WHERE id=?",
                    (comanda_id,))


# ---------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------
def total_comanda(con, comanda_id):
    """Soma dos itens não cancelados (pedidos + entregues)."""
    return con.execute(
        "SELECT COALESCE(SUM(quantidade * preco_unitario), 0) FROM pedido_item "
        "WHERE comanda_id = ? AND status <> 'cancelado'", (comanda_id,)).fetchone()[0]


def listar_comandas(con, status="aberta"):
    sql = """SELECT c.*, u.nome AS aberta_por_nome,
                    (SELECT COUNT(*) FROM pedido_item WHERE comanda_id = c.id AND status = 'pedido') AS pendentes,
                    (SELECT COUNT(*) FROM pedido_item pi WHERE pi.comanda_id = c.id AND """ + SQL_AGUARDANDO + """)
                        AS aguardando,
                    (SELECT COALESCE(SUM(quantidade * preco_unitario), 0) FROM pedido_item
                      WHERE comanda_id = c.id AND status <> 'cancelado') AS total
             FROM comanda c JOIN usuario u ON u.id = c.aberta_por"""
    if status:
        return con.execute(sql + " WHERE c.status = ? ORDER BY c.id DESC", (status,)).fetchall()
    return con.execute(sql + " ORDER BY c.id DESC LIMIT 100").fetchall()


def dados_comanda(con, comanda_id):
    c = con.execute("SELECT * FROM comanda WHERE id = ?", (comanda_id,)).fetchone()
    if c is None:
        return None
    itens = con.execute(
        """SELECT pi.*, p.nome AS produto FROM pedido_item pi JOIN produto p ON p.id = pi.produto_id
           WHERE pi.comanda_id = ? ORDER BY pi.id""", (comanda_id,)).fetchall()
    return {"comanda": c, "itens": itens, "total": total_comanda(con, comanda_id)}


def itens_pendentes(con):
    """Fila de entregas: itens pedidos e ainda não entregues, dos mais antigos aos mais novos."""
    return con.execute(
        """SELECT pi.*, p.nome AS produto, c.identificacao, c.mesa
           FROM pedido_item pi JOIN produto p ON p.id = pi.produto_id JOIN comanda c ON c.id = pi.comanda_id
           WHERE pi.status = 'pedido' AND c.status = 'aberta' AND NOT """ + SQL_AGUARDANDO + """
           ORDER BY COALESCE(pi.confirmado_em, pi.criado_em), pi.id""").fetchall()


def opcoes_do_produto(con, produto_id):
    """Grupos de escolha da receita: {grupo: [{insumo_id, insumo}, ...]} (para montar o formulário)."""
    grupos = {}
    for r in cadastros.receita_do_produto(con, produto_id):
        if r["grupo_escolha"] is not None:
            grupos.setdefault(r["grupo_escolha"], []).append({"insumo_id": r["insumo_id"], "insumo": r["insumo"]})
    return grupos


# ---------------------------------------------------------------------
# Pedidos pelo QR Code (Passo 10)
# O cliente não tem login: o que ele faz fica no nome do usuário de sistema
# "Cliente (QR Code)", e cada item espera um garçom confirmar.
# ---------------------------------------------------------------------
def usuario_qrcode(con):
    r = con.execute("SELECT id FROM usuario WHERE login = ?", (LOGIN_QRCODE,)).fetchone()
    if r is None:
        raise ErroDeRegra("Usuário do QR Code não existe no banco (migração 005).")
    return r[0]


def abrir_comanda_cliente(con, mesa, nome):
    """O cliente abre a própria comanda na mesa do QR Code."""
    nome = " ".join((nome or "").split())
    if not nome:
        raise ErroDeRegra("Digite o seu nome para abrir a comanda.")
    if len(nome) > 40:
        raise ErroDeRegra("Nome muito longo: use até 40 letras.")
    with con:
        cur = con.execute("INSERT INTO comanda (identificacao, mesa, aberta_por) VALUES (?, ?, ?)",
                          (nome, mesa, usuario_qrcode(con)))
    return cur.lastrowid


def pedir_pelo_qrcode(con, comanda_id, produto_id, quantidade, escolhas=None, observacao=None):
    if isinstance(quantidade, bool) or not isinstance(quantidade, int) or not 1 <= quantidade <= MAX_QTD_CLIENTE:
        raise ErroDeRegra(f"Escolha de 1 a {MAX_QTD_CLIENTE} unidades.")
    if len((observacao or "").strip()) > 120:
        raise ErroDeRegra("Observação muito longa: use até 120 letras.")
    esperando = con.execute(
        f"SELECT COUNT(*) FROM pedido_item pi WHERE pi.comanda_id = ? AND {SQL_AGUARDANDO}", (comanda_id,)).fetchone()[0]
    if esperando >= MAX_AGUARDANDO_CLIENTE:
        raise ErroDeRegra("Você já tem vários pedidos esperando o garçom. Aguarde a confirmação para pedir mais.")
    return adicionar_item(con, comanda_id, produto_id, quantidade, usuario_qrcode(con),
                          escolhas=escolhas, observacao=observacao, origem="cliente")


def confirmar_item(con, item_id, usuario_id):
    """O garçom confere o pedido do QR Code e libera para a fila de Entregas."""
    regras._validar_usuario(con, usuario_id)
    item = _item(con, item_id)
    if item["origem"] != "cliente" or item["confirmado_em"] is not None:
        raise ErroDeRegra("Este item não está aguardando confirmação.")
    if item["status"] != "pedido":
        raise ErroDeRegra("Este item já foi cancelado.")
    if _comanda(con, item["comanda_id"])["status"] != "aberta":
        raise ErroDeRegra("A comanda deste item não está aberta.")
    with con:
        con.execute("UPDATE pedido_item SET confirmado_por = ?, confirmado_em = datetime('now','localtime') "
                    "WHERE id = ?", (usuario_id, item_id))


def cliente_desistir(con, comanda_id, item_id):
    """O cliente pode desistir de um item enquanto o garçom ainda não confirmou."""
    item = _item(con, item_id)
    if item["comanda_id"] != comanda_id:
        raise ErroDeRegra("Item não encontrado.")
    if not (item["origem"] == "cliente" and item["confirmado_em"] is None and item["status"] == "pedido"):
        raise ErroDeRegra("Este item já foi confirmado. Para cancelar, chame o garçom.")
    with con:
        con.execute("UPDATE pedido_item SET status = 'cancelado', cancelado_por = ?, "
                    "cancelado_em = datetime('now','localtime'), motivo_cancelamento = 'Cliente desistiu' "
                    "WHERE id = ?", (usuario_qrcode(con), item_id))


def itens_aguardando(con):
    """Pedidos do QR Code esperando um garçom confirmar, dos mais antigos aos mais novos."""
    return con.execute(
        f"""SELECT pi.*, p.nome AS produto, c.identificacao, c.mesa
            FROM pedido_item pi JOIN produto p ON p.id = pi.produto_id JOIN comanda c ON c.id = pi.comanda_id
            WHERE {SQL_AGUARDANDO} AND c.status = 'aberta' ORDER BY pi.criado_em, pi.id""").fetchall()
