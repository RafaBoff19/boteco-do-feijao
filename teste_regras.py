"""Testes das regras de negócio.

Cria um banco TEMPORÁRIO em memória a partir de banco/schema.sql e banco/seed.sql,
então NÃO mexe no seu estoque.db.

Uso:  python teste_regras.py
"""
import sqlite3
import sys
from pathlib import Path

import regras
from regras import ErroDeRegra

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PASTA = Path(__file__).resolve().parent


def banco_de_teste():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((PASTA / "banco" / "schema.sql").read_text(encoding="utf-8"))
    con.executescript((PASTA / "banco" / "seed.sql").read_text(encoding="utf-8"))
    return con


def ok(mensagem):
    print("OK  ", mensagem)


def deve_falhar(funcao, *args, **kwargs):
    """Confirma que a função levanta ErroDeRegra e devolve a mensagem."""
    try:
        funcao(*args, **kwargs)
    except ErroDeRegra as e:
        return str(e)
    raise AssertionError("Era para dar erro, mas a operação passou.")


con = banco_de_teste()
ADMIN = 1
total_mov = lambda: con.execute("SELECT COUNT(*) FROM movimentacao").fetchone()[0]

# 1) entrada em unidade de COMPRA converte sozinha: 2 fardos = 24 garrafas
antes = regras.saldo(con, "Cerveja long neck")["total"]
r = regras.registrar_entrada(con, "Cerveja long neck", 2, ADMIN, fornecedor_id=1, unidade="compra")
assert regras.saldo(con, "Cerveja long neck")["total"] == antes + 24
ok("entrada de 2 fardos de cerveja somou 24 garrafas (300 -> 324)")

# 2) entrada que tira o insumo da zona de alerta: sem aviso de mínimo
r = regras.registrar_entrada(con, "Vodka", 3, ADMIN, unidade="compra")   # +3 litros
assert regras.saldo(con, "Vodka")["total"] == 11000 and r["avisos"] == []
ok("entrada de 3 L de vodka: 8 L -> 11 L, acima do mínimo, sem aviso")

# 3) saída que leva ao mínimo: aviso + alerta registrado
r = regras.registrar_saida(con, "Vodka", 2, ADMIN, "Consumo da casa", unidade="compra")  # -2 L = 9 L
assert len(r["avisos"]) == 1 and "Estoque baixo" in r["avisos"][0]
pend = regras.alertas_pendentes(con)
assert len(pend) == 1 and pend[0]["tipo"] == "minimo"
ok(f"vodka caiu para 9 L: {r['avisos'][0]}")

# 4) segunda saída: continua avisando, mas NÃO duplica o alerta
r = regras.registrar_saida(con, "Vodka", 500, ADMIN, "Degustação")
assert len(r["avisos"]) == 1 and len(regras.alertas_pendentes(con)) == 1
ok("novo aviso na tela, mas o alerta pendente não foi duplicado")

# 5) depois de lido, um novo aviso gera outro alerta
regras.marcar_alerta_lido(con, pend[0]["id"], ADMIN)
assert regras.alertas_pendentes(con) == []
regras.registrar_saida(con, "Vodka", 100, ADMIN, "Degustação")
assert len(regras.alertas_pendentes(con)) == 1
ok("alerta marcado como lido; nova movimentação com estoque baixo gerou novo alerta")
deve_falhar(regras.marcar_alerta_lido, con, pend[0]["id"], ADMIN)
ok("marcar o mesmo alerta como lido duas vezes é recusado")

# 6) perda: com motivo vale; sem motivo é recusada
regras.registrar_perda(con, "Morango", 5, "Fruta estragada", ADMIN)
assert regras.saldo(con, "Morango")["total"] == 35
msg = deve_falhar(regras.registrar_perda, con, "Morango", 2, "  ", ADMIN)
ok(f"perda de 5 morangos registrada (40 -> 35); sem motivo: '{msg}'")

# 7) estoque NEGATIVO: não bloqueia, grava e avisa
r = regras.registrar_saida(con, "Cachaça", 20, ADMIN, "Evento", unidade="compra")  # 20 L de 12 L
assert regras.saldo(con, "Cachaça")["total"] == -8000
assert any("negativo" in a for a in r["avisos"])
assert any(p["tipo"] == "negativo" for p in regras.alertas_pendentes(con))
ok(f"saída maior que o saldo foi gravada; aviso: {r['avisos'][0][:70]}...")

# 8) regularizar depois: entrada corrige o negativo
regras.registrar_entrada(con, "Cachaça", 10, ADMIN, unidade="compra")
assert regras.saldo(con, "Cachaça")["total"] == 2000
ok("entrada de 10 L regulariza a cachaça: -8000 -> 2000 ml")

# 9) transferência: duas pontas, total preservado
regras.registrar_entrada(con, "Gelo comum", 2, ADMIN, unidade="compra")          # 20000 + 10000
t_antes = regras.saldo(con, "Gelo comum")["total"]
r = regras.transferir(con, "Gelo comum", 1, "Estoque", "Bar", ADMIN, unidade="compra")
s = regras.saldo(con, "Gelo comum")
assert s["por_setor"] == {"Estoque": 25000, "Bar": 5000} and s["total"] == t_antes and len(r["movimentacao_ids"]) == 2
ok(f"transferência de 1 saco de gelo: {s['por_setor']}, total preservado")
deve_falhar(regras.transferir, con, "Gelo comum", 1, "Bar", "Bar", ADMIN)
ok("transferência com origem igual ao destino é recusada")

# 10) ajuste de inventário
r = regras.ajustar_inventario(con, "Gelo comum", 4, ADMIN, setor="Estoque", unidade="compra")  # contou 4 sacos = 20000
assert r["diferenca"] == -5000 and regras.saldo(con, "Gelo comum")["por_setor"]["Estoque"] == 20000
ok("inventário: sistema dizia 25000 g, contagem deu 4 sacos (20000 g): ajuste de -5000 g")
r = regras.ajustar_inventario(con, "Gelo comum", 20000, ADMIN, setor="Estoque")
assert r["movimentacao_ids"] == []
ok("contagem igual ao saldo não grava nada")

# 11) validações: nada pode ser gravado quando dá erro
n = total_mov()
deve_falhar(regras.registrar_entrada, con, "Vodka", 0, ADMIN)
deve_falhar(regras.registrar_entrada, con, "Vodka", -3, ADMIN)
deve_falhar(regras.registrar_entrada, con, "Vodka", "2", ADMIN)
deve_falhar(regras.registrar_entrada, con, "Insumo Inexistente", 1, ADMIN)
deve_falhar(regras.registrar_entrada, con, "Vodka", 1, 999)
deve_falhar(regras.registrar_entrada, con, "Vodka", 1, ADMIN, fornecedor_id=999)
deve_falhar(regras.registrar_entrada, con, "Vodka", 1, ADMIN, setor="Cozinha")
deve_falhar(regras.registrar_entrada, con, "Vodka", 1, ADMIN, unidade="caixa")
deve_falhar(regras.ajustar_inventario, con, "Vodka", -1, ADMIN)
assert total_mov() == n
ok("quantidade zero/negativa/texto, insumo, usuário, fornecedor, setor e unidade inválidos: todos recusados, nada gravado")

# 12) histórico
h = regras.historico(con, "Vodka")
assert [x["tipo"] for x in h][:2] == ["saida", "saida"] and h[0]["usuario"] == "Administrador"
assert len(regras.historico(con, limite=5)) == 5
ok(f"histórico da vodka tem {len(h)} movimentações; última: {h[0]['tipo']} de {h[0]['quantidade']:g} {h[0]['unidade']}")

# 13) invariante: saldo = soma das movimentações, em todos os insumos e setores
div = con.execute("""
    SELECT COUNT(*) FROM saldo_estoque s
    WHERE ABS(s.quantidade - (SELECT SUM(quantidade) FROM movimentacao m
                              WHERE m.insumo_id = s.insumo_id AND m.setor_id = s.setor_id)) > 1e-9""").fetchone()[0]
assert div == 0
ok("saldo_estoque bate com a soma das movimentações em todos os casos")

print("\nTUDO CERTO")
