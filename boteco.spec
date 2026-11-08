# Receita do PyInstaller para gerar o executável.
#
# Uso:  python -m PyInstaller --noconfirm boteco.spec
# Sai:  dist/BotecoDoFeijao.exe
#
# Só entram no .exe os arquivos do programa. O estoque.db e a chave_secreta.txt
# ficam de fora de propósito: são criados ao lado do .exe na primeira vez que ele abre.

a = Analysis(
    ["app.py"],
    datas=[("templates", "templates"), ("static", "static"), ("banco", "banco")],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="BotecoDoFeijao",
    console=True,   # a janela preta mostra o endereço do sistema; fechá-la desliga o servidor
    upx=False,      # compressão UPX costuma disparar alarme falso de antivírus
)
