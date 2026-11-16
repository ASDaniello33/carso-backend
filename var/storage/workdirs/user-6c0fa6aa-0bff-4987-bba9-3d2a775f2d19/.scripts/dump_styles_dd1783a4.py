import pathlib, zipfile, re

tpl = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\organisations\13382ac8-d3ef-4cb1-a1ed-9d6c5da306d0\Model_offre_technique_FID.docx')
with zipfile.ZipFile(str(tpl)) as z:
    st = z.read('word/styles.xml').decode('utf8', 'ignore')
print('STYLE IDS:', re.findall(r'w:styleId="([^"]+)"', st))
for sid in re.findall(r'w:styleId="([^"]+)"', st):
    m = re.search(r'<w:style [^>]*w:styleId="%s".*?</w:style>' % re.escape(sid), st, re.S)
    if m:
        s = m.group(0)
        sz = re.findall(r'<w:sz w:val="(\d+)"', s)
        fonts = re.findall(r'w:ascii="([^"]+)"', s)
        col = re.findall(r'<w:color w:val="([^"]+)"', s)
        jc = re.findall(r'<w:jc w:val="([^"]+)"', s)
        print('STYLE', sid, '| sz:', sz, '| fonts:', fonts, '| color:', col, '| jc:', jc)
