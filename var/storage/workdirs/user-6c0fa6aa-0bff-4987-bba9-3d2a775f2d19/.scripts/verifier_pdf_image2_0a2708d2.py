import pathlib
cands = [
    pathlib.Path("../../workdir/6c0fa6aa-0bff-4987-bba9-3d2a775f2d19/a57150c3-abb1-490a-b695-d6a705d58589/test_insertion_image_reelle.pdf"),
    pathlib.Path("../../../workdir/6c0fa6aa-0bff-4987-bba9-3d2a775f2d19/a57150c3-abb1-490a-b695-d6a705d58589/test_insertion_image_reelle.pdf"),
]
for p in cands:
    try:
        b = p.read_bytes()
        print("OK", p, len(b))
        print("  /Image count:", b.count(b"/Image"))
        print("  /Subtype /Image:", b.count(b"/Subtype /Image"))
        print("  'non charg':", b"non charg" in b)
    except Exception as e:
        print("KO", p, type(e).__name__, str(e)[:120])
