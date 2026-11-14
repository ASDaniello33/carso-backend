from pathlib import Path
p = Path('.sandbox/runner.py')
print(p.read_text(encoding='utf-8', errors='replace'))
