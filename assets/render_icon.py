import sys
from PIL import Image, ImageDraw
S=4; N=512*S
def render(size):
    im=Image.new("RGBA",(N,N),(0,0,0,0)); d=ImageDraw.Draw(im)
    d.rounded_rectangle([0,0,N-1,N-1],radius=112*S,fill="#1f9d55")
    W=(255,255,255,255)
    def seg(a,b,w):
        a=(a[0]*S,a[1]*S); b=(b[0]*S,b[1]*S); w=w*S
        d.line([a,b],fill=W,width=int(w))
        for p in (a,b): d.ellipse([p[0]-w/2,p[1]-w/2,p[0]+w/2,p[1]+w/2],fill=W)
    c=220*S; r=128*S; w=44*S
    d.ellipse([c-r-w/2,c-r-w/2,c+r+w/2,c+r+w/2],outline=W,width=w)
    seg((318,318),(412,412),60)
    for a,b in [((177,156),(220,210)),((263,156),(220,210)),((220,210),(220,292)),((182,224),(258,224)),((182,260),(258,260))]:
        seg(a,b,30)
    return im.resize((size,size),Image.LANCZOS)

def render_padded(size):
    # macOS 風: 1024 中 824 の本体＋余白
    inner = round(size * 824 / 1024)
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    body = render(inner)
    off = (size - inner) // 2
    im.paste(body, (off, off), body)
    return im

if __name__ == "__main__":
    # 使い方: render_icon.py web <出力dir> 32 180 512   /   render_icon.py iconset <xxx.iconset>
    mode, out = sys.argv[1], sys.argv[2]
    if mode == "web":
        for s in map(int, sys.argv[3:]):
            render(s).save(f"{out}/icon-{s}.png")
    else:
        for s in (16, 32, 128, 256, 512):
            render_padded(s).save(f"{out}/icon_{s}x{s}.png")
            render_padded(s * 2).save(f"{out}/icon_{s}x{s}@2x.png")
