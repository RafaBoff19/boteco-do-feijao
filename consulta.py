"""Mostra no terminal o que está no banco: saldos, alertas e cardápio com receitas.

Uso:  python consulta.py
"""
import sys

import bd

# garante acentos corretos no terminal do Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DIAS = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"]


def titulo(texto):
    print(f"\n=== {texto} ===")


def saldo_por_setor(con):
    titulo("SALDO POR SETOR")
    linhas = con.execute("""
        SELECT i.nome, st.nome AS setor, s.quantidade,
               i.unidade_consumo, i.unidade_compra, i.fator_compra
        FROM saldo_estoque s
        JOIN insumo i  ON i.id = s.insumo_id
        JOIN setor st  ON st.id = s.setor_id
        ORDER BY i.nome, st.nome
    """).fetchall()
    for l in linhas:
        em_compra = l["quantidade"] / l["fator_compra"]
        print(f"{l['nome']:<26}{l['setor']:<9}{l['quantidade']:>9.0f} {l['unidade_consumo']:<3}"
              f"  = {em_compra:.1f} {l['unidade_compra']}")


def alertas(con):
    titulo("ESTOQUE NO MÍNIMO OU ABAIXO")
    linhas = con.execute("SELECT * FROM v_estoque_baixo ORDER BY insumo").fetchall()
    if not linhas:
        print("nenhum")
    for l in linhas:
        print(f"{l['insumo']:<26}saldo {l['saldo_total']:.0f} {l['unidade']}"
              f"  (mínimo {l['minimo_efetivo']:.0f} {l['unidade']})")

    titulo("ESTOQUE NEGATIVO")
    linhas = con.execute("SELECT * FROM v_estoque_negativo ORDER BY insumo").fetchall()
    if not linhas:
        print("nenhum")
    for l in linhas:
        print(f"{l['insumo']:<26}{l['setor']:<9}{l['saldo']:.0f} {l['unidade']}")


def cardapio(con):
    titulo("CARDÁPIO E RECEITAS")
    for p in con.execute("SELECT * FROM produto WHERE ativo = 1 ORDER BY categoria, nome"):
        print(f"\n{p['nome']}  [{p['categoria']}]  R$ {p['preco_padrao']:.2f}")

        for promo in con.execute(
                "SELECT * FROM preco_promocao WHERE produto_id = ? AND ativo = 1", (p["id"],)):
            if promo["dias_semana"]:
                dias = ", ".join(DIAS[int(d)] for d in promo["dias_semana"].split(","))
            else:
                dias = "todos os dias"
            print(f"   promoção: R$ {promo['preco']:.2f} ({dias})")

        itens = con.execute("""
            SELECT i.nome, r.quantidade, i.unidade_consumo, r.grupo_escolha
            FROM receita_item r JOIN insumo i ON i.id = r.insumo_id
            WHERE r.produto_id = ?
            ORDER BY r.grupo_escolha IS NOT NULL, r.grupo_escolha, i.nome
        """, (p["id"],)).fetchall()
        for it in itens:
            ou = f"  (escolha: {it['grupo_escolha']})" if it["grupo_escolha"] else ""
            print(f"   - {it['quantidade']:g} {it['unidade_consumo']} de {it['nome']}{ou}")


def main():
    con = bd.conectar()
    saldo_por_setor(con)
    alertas(con)
    cardapio(con)
    con.close()


if __name__ == "__main__":
    main()
