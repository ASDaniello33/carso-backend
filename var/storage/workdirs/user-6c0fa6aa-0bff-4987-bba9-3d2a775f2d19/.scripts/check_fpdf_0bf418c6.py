# Essayons fpdf2 avec le bon import
try:
    from fpdf import FPDF
    print("fpdf OK (v1)")
except Exception as e:
    print(f"fpdf échoué: {e}")

# Essayons fpdf2
try:
    from fpdf2 import FPDF
    print("fpdf2 OK")
except Exception as e:
    print(f"fpdf2 échoué: {e}")

# Vérifions les modules disponibles
import fpdf
print(f"Module fpdf: {fpdf.__file__}")
print(f"Version: {fpdf.__version__ if hasattr(fpdf, '__version__') else 'inconnue'}")
