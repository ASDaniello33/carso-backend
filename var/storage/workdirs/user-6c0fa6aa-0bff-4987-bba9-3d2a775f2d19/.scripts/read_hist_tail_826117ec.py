from pathlib import Path
p = Path('/conversation_history/session_f066471ec0374627b55fbf55e066d188.md')
t = p.read_text(encoding='utf-8', errors='replace')
print(len(t))
print(t[-6000:])
