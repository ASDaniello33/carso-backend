try:
    import qrcode
    import qrcode.image.svg
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=0)
    qr.add_data('https://www.carso.mg/')
    qr.make(fit=True)
    img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    try:
        s = img.to_string().decode('utf-8')
    except Exception:
        s = img.to_string()
    print("VERSION:", qr.version)
    print("SVG_DEBUT")
    print(s)
    print("SVG_FIN")
except ImportError as e:
    print("PAS_DE_QRCODE:", e)
except Exception as e:
    print("ERREUR:", type(e).__name__, e)
