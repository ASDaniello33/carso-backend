import pathlib
base = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
for f in sorted(base.glob('*.docx'), key=lambda x: x.stat().st_mtime):
    print(f.name, f.stat().st_size, 'octets')
