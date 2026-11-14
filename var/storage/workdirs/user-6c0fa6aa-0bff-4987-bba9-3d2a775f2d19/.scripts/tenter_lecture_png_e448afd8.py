import pathlib, base64
cands = [
    pathlib.Path(r"C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\workdir\53722ea6-b785-4c9c-9301-b33654ad2054\e35a578e-f4a5-4760-a4f4-3c94491497e2\Modern Clean T-Shirt Design.png"),
    pathlib.Path("/storage/workdir/53722ea6-b785-4c9c-9301-b33654ad2054/e35a578e-f4a5-4760-a4f4-3c94491497e2/Modern Clean T-Shirt Design.png"),
    pathlib.Path("../../53722ea6-b785-4c9c-9301-b33654ad2054/e35a578e-f4a5-4760-a4f4-3c94491497e2/Modern Clean T-Shirt Design.png"),
]
for c in cands:
    try:
        b = c.read_bytes()
        print("OK", c, len(b), b[:8])
    except Exception as e:
        print("KO", c, type(e).__name__, e)
