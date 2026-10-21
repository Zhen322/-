# -*- coding: utf-8 -*-
"""Extract text (paragraphs + tables in order) from a .docx using stdlib only."""
import sys
import zipfile
import xml.etree.ElementTree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

def text_of_paragraph(p):
    parts = []
    for t in p.iter(W + 't'):
        parts.append(t.text or '')
    return ''.join(parts)

def walk(elem, out, depth=0):
    for child in elem:
        tag = child.tag
        if tag == W + 'p':
            txt = text_of_paragraph(child)
            # detect heading style
            style = ''
            pPr = child.find(W + 'pPr')
            if pPr is not None:
                ps = pPr.find(W + 'pStyle')
                if ps is not None:
                    style = ps.get(W + 'val', '')
            if style.lower().startswith('heading') or style.startswith('1') or style.startswith('2'):
                out.append(f"[{style}] {txt}")
            else:
                out.append(txt)
        elif tag == W + 'tbl':
            out.append('--- TABLE START ---')
            for row in child.findall(W + 'tr'):
                cells = []
                for tc in row.findall(W + 'tc'):
                    ctxt = ' '.join(text_of_paragraph(p) for p in tc.findall(W + 'p'))
                    cells.append(ctxt.strip())
                out.append(' | '.join(cells))
            out.append('--- TABLE END ---')
        else:
            walk(child, out, depth + 1)

def main(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read('word/document.xml')
    root = ET.fromstring(xml)
    body = root.find(W + 'body')
    out = []
    walk(body, out)
    print('\n'.join(out))

if __name__ == '__main__':
    main(sys.argv[1])
