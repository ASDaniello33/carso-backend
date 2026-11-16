import pathlib
print('CWD files:', [p.name for p in pathlib.Path('.').iterdir()][:40])
try:
    import openpyxl
    print('openpyxl OK', openpyxl.__version__)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Test'
    ws['A1'] = 10
    ws['A2'] = 5
    ws['A3'] = '=SUM(A1:A2)'
    wb.create_sheet('Feuil 2')['A1'] = "='Test'!A3"
    wb.save('test_openpyxl.xlsx')
    print('saved', pathlib.Path('test_openpyxl.xlsx').exists())
except Exception as e:
    print('OPENPYXL FAIL', type(e).__name__, e)
try:
    import classeur
    print('classeur OK', [n for n in dir(classeur) if not n.startswith('_')])
except Exception as e:
    print('CLASSEUR FAIL', type(e).__name__, e)
