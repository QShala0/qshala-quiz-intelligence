"""Build the QShala School master deck (backend/app/services/export/templates/qshala_school_master.pptx).

Input: the designer's template.pptx (artwork lives in the slide master / layouts). Output: the same deck with
its sample slides removed and the theme fonts set to Causten Round (Bold for headings), so placeholders, new text
boxes and anything left unstyled inherit the brand font instead of Aptos/Calibri.

    python -m backend.scripts.build_school_master path/to/template.pptx
"""
import re
import sys
from pathlib import Path

from pptx import Presentation
from pptx.opc.constants import RELATIONSHIP_TYPE as RT

OUT = Path(__file__).resolve().parents[1] / "app" / "services" / "export" / "templates" / "qshala_school_master.pptx"
HEADING = "Causten Round Bold"
BODY = "Causten Round"


def build(src: Path, out: Path = OUT) -> Path:
    prs = Presentation(str(src))
    ids = prs.slides._sldIdLst
    for sld in list(ids):
        prs.part.drop_rel(sld.rId)
        ids.remove(sld)

    theme = prs.slide_master.part.part_related_by(RT.THEME)
    xml = theme.blob.decode("utf-8")
    for tag, face in (("majorFont", HEADING), ("minorFont", BODY)):
        xml = re.sub(
            rf"(<a:{tag}>)<a:latin[^>]*/>(<a:ea[^>]*/>)(<a:cs[^>]*/>)",
            rf'\1<a:latin typeface="{face}"/><a:ea typeface="{face}"/><a:cs typeface="{face}"/>', xml, count=1)
    assert xml.count(f'typeface="{HEADING}"') >= 3 and xml.count(f'typeface="{BODY}"') >= 3, "theme fonts not replaced"
    theme._blob = xml.encode("utf-8")

    # Style guide: title 44pt, content 28pt.
    m = prs.slide_master._element
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main", "p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
    for path, size in (("p:txStyles/p:titleStyle/a:lvl1pPr/a:defRPr", "4400"), ("p:txStyles/p:bodyStyle/a:lvl1pPr/a:defRPr", "2800")):
        for el in m.findall(path, ns):
            el.set("sz", size)

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return out


if __name__ == "__main__":
    print(build(Path(sys.argv[1])))
