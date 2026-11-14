import pathlib
p = pathlib.Path("a57150c3-abb1-490a-b695-d6a705d58589/test_insertion_image_reelle.pdf")
print("existe:", p.exists(), "taille:", p.stat().st_size if p.exists() else None)
b = p.read_bytes()
print("nb images XObject:", b.count(b"/Image"))
print("contient 'Image non chargee':", b"non charg" in b)
