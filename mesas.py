"""Mesas e QR Codes (Passo 10).

Cada mesa tem um código sorteado. O QR Code da mesa aponta para
    http://<IP do PC>:5000/m/<codigo>
O código é aleatório para ninguém adivinhar o link de outra mesa. Se um QR vazar
(foto no Instagram, por exemplo), "trocar o código" invalida o QR antigo; é só
imprimir o novo.
"""
import secrets

import segno

from regras import ErroDeRegra

MAX_MESAS = 100


def _novo_codigo(con):
    while True:
        codigo = secrets.token_urlsafe(6)          # 8 caracteres: letras, números, - e _
        if not con.execute("SELECT 1 FROM mesa WHERE codigo = ?", (codigo,)).fetchone():
            return codigo


def listar_mesas(con, incluir_inativas=False):
    return con.execute("SELECT * FROM mesa WHERE ? OR ativa = 1 ORDER BY numero",
                       (1 if incluir_inativas else 0,)).fetchall()


def definir_quantidade(con, quantidade):
    """Deixa ativas as mesas 1..quantidade. As de número maior são desativadas (QR para de funcionar),
    mas não apagadas: se voltarem, o QR antigo volta a valer."""
    if isinstance(quantidade, bool) or not isinstance(quantidade, int) or not 0 <= quantidade <= MAX_MESAS:
        raise ErroDeRegra(f"Informe um número de mesas entre 0 e {MAX_MESAS}.")
    with con:
        for numero in range(1, quantidade + 1):
            if con.execute("SELECT 1 FROM mesa WHERE numero = ?", (numero,)).fetchone():
                con.execute("UPDATE mesa SET ativa = 1 WHERE numero = ?", (numero,))
            else:
                con.execute("INSERT INTO mesa (numero, codigo) VALUES (?, ?)", (numero, _novo_codigo(con)))
        con.execute("UPDATE mesa SET ativa = 0 WHERE numero > ?", (quantidade,))


def trocar_codigo(con, numero):
    if not con.execute("SELECT 1 FROM mesa WHERE numero = ?", (numero,)).fetchone():
        raise ErroDeRegra("Mesa não encontrada.")
    with con:
        con.execute("UPDATE mesa SET codigo = ? WHERE numero = ?", (_novo_codigo(con), numero))


def mesa_por_codigo(con, codigo):
    """A mesa ativa daquele código, ou None (código errado, trocado ou mesa desativada)."""
    if not codigo or len(codigo) > 20:
        return None
    return con.execute("SELECT * FROM mesa WHERE codigo = ? AND ativa = 1", (codigo,)).fetchone()


def qr_svg(endereco):
    """Desenho do QR Code (SVG) para colocar direto na página e imprimir."""
    return segno.make(endereco, error="m").svg_inline(scale=6, border=2, dark="#000", light="#fff")
