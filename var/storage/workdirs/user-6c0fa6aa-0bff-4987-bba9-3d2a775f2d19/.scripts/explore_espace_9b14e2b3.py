import pathlib
cwd = pathlib.Path.cwd()
print("cwd:", cwd)
for p in sorted(cwd.rglob('*')):
    print(" -", p.relative_to(cwd), p.stat().st_size if p.is_file() else "<dir>")
try:
    import PIL
    from PIL import Image
    print("PIL OK", PIL.__version__)
except Exception as e:
    print("PIL KO:", e)
