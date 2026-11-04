"""Acesso ao banco de dados SQLite do Buteco do Feijão.

Uso nos outros arquivos do projeto:

    import bd
    con = bd.conectar()          # cria o banco na primeira vez e abre a conexão
    for linha in con.execute("SELECT * FROM insumo"):
        print(linha["nome"])

Uso pelo terminal:

    python bd.py                 # cria o banco se ele ainda não existir
    python bd.py --recriar       # APAGA o banco e cria de novo (volta ao estoque fictício)
"""
import sqlite3
import sys
from pathlib import Path

# Os caminhos partem da pasta onde este arquivo está, então funciona
# de qualquer lugar que o programa for aberto (inclusive depois de virar .exe).
PASTA = Path(__file__).resolve().parent
ARQUIVO_BANCO = PASTA / "estoque.db"
ARQUIVO_SCHEMA = PASTA / "banco" / "schema.sql"
ARQUIVO_SEED = PASTA / "banco" / "seed.sql"
PASTA_MIGRACOES = PASTA / "banco" / "migracoes"


def aplicar_migracoes(con):
    """Aplica, em ordem, os arquivos banco/migracoes/NNN_nome.sql que ainda não foram aplicados.

    O número da última migração aplicada fica gravado no próprio banco (PRAGMA user_version),
    então rodar isto várias vezes é seguro, e bancos antigos são atualizados sem perder dados.
    Cada migração roda inteira ou não roda (se der erro, volta tudo).
    """
    atual = con.execute("PRAGMA user_version").fetchone()[0]
    for arquivo in sorted(PASTA_MIGRACOES.glob("[0-9][0-9][0-9]_*.sql")):
        numero = int(arquivo.name[:3])
        if numero <= atual:
            continue
        sql = arquivo.read_text(encoding="utf-8")
        try:
            con.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {numero};\nCOMMIT;")
        except Exception:
            con.rollback()
            raise


def criar_banco(com_dados_ficticios=True):
    """Cria o arquivo estoque.db: monta as tabelas e, se pedido, carrega o estoque fictício."""
    if ARQUIVO_BANCO.exists():
        raise FileExistsError(f"O banco já existe: {ARQUIVO_BANCO}")

    con = sqlite3.connect(ARQUIVO_BANCO)
    try:
        con.executescript(ARQUIVO_SCHEMA.read_text(encoding="utf-8"))
        if com_dados_ficticios:
            con.executescript(ARQUIVO_SEED.read_text(encoding="utf-8"))
        con.commit()
        aplicar_migracoes(con)
    except Exception:
        # se algo der errado no meio, não deixa um banco pela metade
        con.close()
        ARQUIVO_BANCO.unlink(missing_ok=True)
        raise
    finally:
        con.close()


def conectar():
    """Abre uma conexão com o banco (criando-o na primeira vez)."""
    if not ARQUIVO_BANCO.exists():
        criar_banco()

    con = sqlite3.connect(ARQUIVO_BANCO)
    con.row_factory = sqlite3.Row            # permite usar linha["nome"] em vez de linha[0]
    aplicar_migracoes(con)                   # atualiza bancos antigos (não faz nada se já estiver em dia)
    con.execute("PRAGMA foreign_keys = ON")  # tem que ser ligado a cada conexão
    return con


if __name__ == "__main__":
    if "--recriar" in sys.argv:
        ARQUIVO_BANCO.unlink(missing_ok=True)
        criar_banco()
        print(f"Banco recriado com dados fictícios: {ARQUIVO_BANCO}")
    elif ARQUIVO_BANCO.exists():
        print(f"O banco já existe: {ARQUIVO_BANCO}")
    else:
        criar_banco()
        print(f"Banco criado com dados fictícios: {ARQUIVO_BANCO}")
