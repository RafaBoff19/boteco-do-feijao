"""Regras de negócio do estoque do Buteco do Feijão.

Cada função recebe a conexão (vinda de bd.conectar()) e devolve um dicionário com:
    "movimentacao_ids": os números das movimentações gravadas
    "avisos":           mensagens para mostrar ao usuário (estoque baixo, negativo...)

Exemplo:
    import bd, regras
    con = bd.conectar()
    r = regras.registrar_perda(con, "Morango", 5, "Fruta estragada", usuario_id=1)
    print(r["avisos"])

Convenções:
  * Quantidades são SEMPRE positivas ao chamar as funções; o sinal (entra/sai)
    é decidido aqui dentro.
  * O parâmetro unidade="consumo" (padrão) usa ml/g/un; unidade="compra" usa a
    unidade de compra do insumo (fardo, saco, litro...) e converte sozinho.
  * Insumo e setor podem ser passados pelo nome ("Vodka", "Bar") ou pelo id.
  * Estoque negativo NÃO bloqueia: a operação é gravada e vem um aviso.
  * Se qualquer parte de uma operação falhar, nada é gravado (transação).
"""


class ErroDeRegra(Exception):
    """Erro de uso (dado inválido). A tela mostra a mensagem para o usuário."""


# ---------------------------------------------------------------------
# Funções auxiliares (começam com _ : são de uso interno)
# ---------------------------------------------------------------------

def _buscar_por_nome_ou_id(con, tabela, valor, descricao, so_ativos=False):
    filtro_ativo = " AND ativo = 1" if so_ativos else ""
    campo = "id" if isinstance(valor, int) else "nome"
    linha = con.execute(
        f"SELECT * FROM {tabela} WHERE {campo} = ?{filtro_ativo}", (valor,)
    ).fetchone()
    if linha is None:
        raise ErroDeRegra(f"{descricao} não encontrado(a): {valor!r}")
    return linha


def _buscar_insumo(con, insumo):
    return _buscar_por_nome_ou_id(con, "insumo", insumo, "Insumo", so_ativos=True)


def _buscar_setor(con, setor):
    return _buscar_por_nome_ou_id(con, "setor", setor, "Setor")


def _validar_usuario(con, usuario_id):
    linha = con.execute(
        "SELECT id FROM usuario WHERE id = ? AND ativo = 1", (usuario_id,)
    ).fetchone()
    if linha is None:
        raise ErroDeRegra(f"Usuário não encontrado ou inativo: {usuario_id!r}")


def _validar_fornecedor(con, fornecedor_id):
    if fornecedor_id is None:
        return
    linha = con.execute(
        "SELECT id FROM fornecedor WHERE id = ? AND ativo = 1", (fornecedor_id,)
    ).fetchone()
    if linha is None:
        raise ErroDeRegra(f"Fornecedor não encontrado ou inativo: {fornecedor_id!r}")


def _validar_quantidade(quantidade):
    if isinstance(quantidade, bool) or not isinstance(quantidade, (int, float)) or quantidade <= 0:
        raise ErroDeRegra("A quantidade deve ser um número maior que zero.")


def _para_consumo(insumo, quantidade, unidade):
    """Converte para a unidade de consumo (ml, g ou un), que é como o banco guarda."""
    if unidade == "consumo":
        return quantidade
    if unidade == "compra":
        return quantidade * insumo["fator_compra"]
    raise ErroDeRegra("A unidade deve ser 'consumo' ou 'compra'.")


def _fmt(valor):
    """Formata para exibição em português: 12000 -> 12.000 ; 1.5 -> 1,5"""
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return texto.rstrip("0").rstrip(",") if "," in texto else texto


def _inserir(con, insumo_id, setor_id, tipo, quantidade, usuario_id,
             motivo=None, fornecedor_id=None, transferencia_id=None, pedido_item_id=None, compra_item_id=None,
             reposicao_item_id=None):
    """Grava uma movimentação (quantidade COM sinal). O saldo é atualizado pelo trigger do banco.

    pedido_item_id / compra_item_id / reposicao_item_id ligam a movimentação ao que a causou.
    Só entram no INSERT quando informados (são colunas criadas por migrações).
    """
    colunas = {"insumo_id": insumo_id, "setor_id": setor_id, "tipo": tipo, "quantidade": quantidade,
               "motivo": motivo, "fornecedor_id": fornecedor_id, "usuario_id": usuario_id,
               "transferencia_id": transferencia_id}
    if pedido_item_id is not None:
        colunas["pedido_item_id"] = pedido_item_id
    if compra_item_id is not None:
        colunas["compra_item_id"] = compra_item_id
    if reposicao_item_id is not None:
        colunas["reposicao_item_id"] = reposicao_item_id
    # os nomes das colunas vêm só deste código (nunca do usuário), então montar o SQL assim é seguro
    cur = con.execute(
        f"INSERT INTO movimentacao ({', '.join(colunas)}) VALUES ({', '.join('?' * len(colunas))})",
        tuple(colunas.values()))
    return cur.lastrowid


def _criar_alerta_se_novo(con, insumo_id, tipo, saldo):
    """Evita encher a tela de alertas repetidos: só cria se não houver um igual ainda não lido."""
    ja_existe = con.execute(
        "SELECT 1 FROM alerta WHERE insumo_id = ? AND tipo = ? AND lido_em IS NULL",
        (insumo_id, tipo),
    ).fetchone()
    if ja_existe is None:
        con.execute(
            "INSERT INTO alerta (insumo_id, tipo, saldo_no_momento) VALUES (?, ?, ?)",
            (insumo_id, tipo, saldo),
        )


def _checar_alertas(con, insumo_id, setores_ids):
    """Depois de uma movimentação: verifica estoque negativo (por setor) e estoque mínimo (total).

    Registra o alerta e devolve a lista de mensagens de aviso.
    """
    avisos = []
    insumo = con.execute("SELECT nome, unidade_consumo FROM insumo WHERE id = ?", (insumo_id,)).fetchone()
    un = insumo["unidade_consumo"]

    for setor_id in setores_ids:
        linha = con.execute(
            """SELECT st.nome AS setor, s.quantidade
               FROM saldo_estoque s JOIN setor st ON st.id = s.setor_id
               WHERE s.insumo_id = ? AND s.setor_id = ?""",
            (insumo_id, setor_id),
        ).fetchone()
        if linha is not None and linha["quantidade"] < 0:
            avisos.append(
                f"ATENÇÃO: estoque negativo de {insumo['nome']} no setor {linha['setor']}: "
                f"{_fmt(linha['quantidade'])} {un}. A operação foi registrada mesmo assim; "
                f"lembre de registrar a entrada correspondente."
            )
            _criar_alerta_se_novo(con, insumo_id, "negativo", linha["quantidade"])

    resumo = con.execute(
        "SELECT saldo_total, minimo_efetivo FROM v_saldo_total WHERE insumo_id = ?", (insumo_id,)
    ).fetchone()
    if (resumo is not None and resumo["minimo_efetivo"] is not None
            and 0 <= resumo["saldo_total"] <= resumo["minimo_efetivo"]):
        avisos.append(
            f"Estoque baixo de {insumo['nome']}: {_fmt(resumo['saldo_total'])} {un} "
            f"(mínimo {_fmt(resumo['minimo_efetivo'])} {un})."
        )
        _criar_alerta_se_novo(con, insumo_id, "minimo", resumo["saldo_total"])

    return avisos


# ---------------------------------------------------------------------
# Operações de estoque
# ---------------------------------------------------------------------

def registrar_entrada(con, insumo, quantidade, usuario_id, fornecedor_id=None,
                      setor="Estoque", unidade="consumo", motivo=None):
    """Entrada de mercadoria (compra, recebimento). Ex.: 2 fardos de cerveja."""
    _validar_quantidade(quantidade)
    ins, st = _buscar_insumo(con, insumo), _buscar_setor(con, setor)
    _validar_usuario(con, usuario_id)
    _validar_fornecedor(con, fornecedor_id)
    qtd = _para_consumo(ins, quantidade, unidade)

    with con:
        mov = _inserir(con, ins["id"], st["id"], "entrada", qtd, usuario_id,
                       motivo=motivo, fornecedor_id=fornecedor_id)
        avisos = _checar_alertas(con, ins["id"], [st["id"]])
    return {"movimentacao_ids": [mov], "avisos": avisos}


def registrar_saida(con, insumo, quantidade, usuario_id, motivo,
                    setor="Estoque", unidade="consumo"):
    """Saída avulsa (uso interno, consumo da casa...). A baixa por pedido virá na Fase 2."""
    _validar_quantidade(quantidade)
    if not motivo or not motivo.strip():
        raise ErroDeRegra("Informe o motivo da saída.")
    ins, st = _buscar_insumo(con, insumo), _buscar_setor(con, setor)
    _validar_usuario(con, usuario_id)
    qtd = _para_consumo(ins, quantidade, unidade)

    with con:
        mov = _inserir(con, ins["id"], st["id"], "saida", -qtd, usuario_id, motivo=motivo)
        avisos = _checar_alertas(con, ins["id"], [st["id"]])
    return {"movimentacao_ids": [mov], "avisos": avisos}


def registrar_perda(con, insumo, quantidade, motivo, usuario_id,
                    setor="Estoque", unidade="consumo"):
    """Perda ou desperdício (fruta estragada, copo quebrado...). O motivo é obrigatório."""
    _validar_quantidade(quantidade)
    if not motivo or not motivo.strip():
        raise ErroDeRegra("Informe o motivo da perda.")
    ins, st = _buscar_insumo(con, insumo), _buscar_setor(con, setor)
    _validar_usuario(con, usuario_id)
    qtd = _para_consumo(ins, quantidade, unidade)

    with con:
        mov = _inserir(con, ins["id"], st["id"], "perda", -qtd, usuario_id, motivo=motivo)
        avisos = _checar_alertas(con, ins["id"], [st["id"]])
    return {"movimentacao_ids": [mov], "avisos": avisos}


def ajustar_inventario(con, insumo, contagem_real, usuario_id, setor="Estoque",
                       unidade="consumo", motivo="Contagem de inventário"):
    """Aba de ajuste: você informa o que CONTOU e o sistema registra a diferença.

    Se o sistema diz 20000 g e a contagem deu 15000 g, grava um ajuste de -5000 g.
    """
    if isinstance(contagem_real, bool) or not isinstance(contagem_real, (int, float)) or contagem_real < 0:
        raise ErroDeRegra("A contagem deve ser um número maior ou igual a zero.")
    ins, st = _buscar_insumo(con, insumo), _buscar_setor(con, setor)
    _validar_usuario(con, usuario_id)
    real = _para_consumo(ins, contagem_real, unidade)

    linha = con.execute(
        "SELECT quantidade FROM saldo_estoque WHERE insumo_id = ? AND setor_id = ?",
        (ins["id"], st["id"]),
    ).fetchone()
    atual = linha["quantidade"] if linha else 0.0
    diferenca = real - atual

    if abs(diferenca) < 1e-9:
        return {"movimentacao_ids": [], "avisos": [], "diferenca": 0.0}

    with con:
        mov = _inserir(con, ins["id"], st["id"], "ajuste", diferenca, usuario_id, motivo=motivo)
        avisos = _checar_alertas(con, ins["id"], [st["id"]])
    return {"movimentacao_ids": [mov], "avisos": avisos, "diferenca": diferenca}


def transferir(con, insumo, quantidade, origem, destino, usuario_id, unidade="consumo"):
    """Move estoque entre setores (ex.: do Estoque para o Bar). Grava as duas pontas juntas."""
    _validar_quantidade(quantidade)
    ins = _buscar_insumo(con, insumo)
    setor_o, setor_d = _buscar_setor(con, origem), _buscar_setor(con, destino)
    if setor_o["id"] == setor_d["id"]:
        raise ErroDeRegra("Origem e destino devem ser setores diferentes.")
    _validar_usuario(con, usuario_id)
    qtd = _para_consumo(ins, quantidade, unidade)

    with con:
        tid = con.execute("SELECT COALESCE(MAX(transferencia_id), 0) + 1 FROM movimentacao").fetchone()[0]
        saiu = _inserir(con, ins["id"], setor_o["id"], "transferencia", -qtd, usuario_id, transferencia_id=tid)
        entrou = _inserir(con, ins["id"], setor_d["id"], "transferencia", qtd, usuario_id, transferencia_id=tid)
        avisos = _checar_alertas(con, ins["id"], [setor_o["id"], setor_d["id"]])
    return {"movimentacao_ids": [saiu, entrou], "avisos": avisos}


# ---------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------

def saldo(con, insumo):
    """Saldo atual de um insumo: total e por setor (na unidade de consumo)."""
    ins = _buscar_insumo(con, insumo)
    linhas = con.execute(
        """SELECT st.nome AS setor, COALESCE(s.quantidade, 0) AS quantidade
           FROM setor st
           LEFT JOIN saldo_estoque s ON s.setor_id = st.id AND s.insumo_id = ?
           ORDER BY st.id""",
        (ins["id"],),
    ).fetchall()
    por_setor = {l["setor"]: l["quantidade"] for l in linhas}
    return {
        "insumo": ins["nome"],
        "unidade": ins["unidade_consumo"],
        "total": sum(por_setor.values()),
        "por_setor": por_setor,
    }


def historico(con, insumo=None, limite=50):
    """Últimas movimentações (da mais recente para a mais antiga), de um insumo ou de todos."""
    consulta = """
        SELECT m.id, m.criado_em, m.turno_data, i.nome AS insumo, st.nome AS setor,
               m.tipo, m.quantidade, i.unidade_consumo AS unidade, m.motivo,
               u.nome AS usuario, f.nome AS fornecedor
        FROM movimentacao m
        JOIN insumo  i  ON i.id  = m.insumo_id
        JOIN setor   st ON st.id = m.setor_id
        JOIN usuario u  ON u.id  = m.usuario_id
        LEFT JOIN fornecedor f ON f.id = m.fornecedor_id
    """
    params = []
    if insumo is not None:
        consulta += " WHERE m.insumo_id = ?"
        params.append(_buscar_insumo(con, insumo)["id"])
    consulta += " ORDER BY m.id DESC LIMIT ?"
    params.append(limite)
    return con.execute(consulta, params).fetchall()


def alertas_pendentes(con):
    """Alertas ainda não lidos (para o painel de notificações)."""
    return con.execute(
        """SELECT a.id, i.nome AS insumo, a.tipo, a.saldo_no_momento, i.unidade_consumo AS unidade, a.criado_em
           FROM alerta a JOIN insumo i ON i.id = a.insumo_id
           WHERE a.lido_em IS NULL
           ORDER BY a.id"""
    ).fetchall()


def marcar_alerta_lido(con, alerta_id, usuario_id):
    _validar_usuario(con, usuario_id)
    with con:
        cur = con.execute(
            """UPDATE alerta SET lido_em = datetime('now', 'localtime'), lido_por = ?
               WHERE id = ? AND lido_em IS NULL""",
            (usuario_id, alerta_id),
        )
    if cur.rowcount == 0:
        raise ErroDeRegra("Alerta não encontrado ou já lido.")
