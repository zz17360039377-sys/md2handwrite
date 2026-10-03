#!/usr/bin/env python3
"""把 markdown 笔记批量转成手写风格 PDF。 v1.0

手写感 = 手写字体逐字渲染 + 每个字的局部仿射畸变（3x3 网格 mesh 扭曲）
+ 大块随机畸变网格纸 + 阴影带凹陷 + 喷漆噪点污渍 + 整纸仿射
+ 后处理盖印：虚线框水印 + 正常字体页码
全部渲染参数集中在 config.yaml，修改保存后重新运行即可生效。
依赖：wkhtmltopdf（系统）、pypdf reportlab pillow pyyaml（Python）
用法: python3 md2handwrite.py [md文件或目录] [-o 输出目录] [-c 配置文件]
      [--seed-salt 文本]  换一批笔误/弯曲/溅点（同一文件不同 salt 结果不同）
      [--no-stamp]        跳过水印与页码盖印
"""
import subprocess, argparse, random, re, html as H, hashlib, io, math, sys, time, traceback
from pathlib import Path
from html.parser import HTMLParser
import markdown
import yaml

T0 = time.time()

def log(msg, level="INFO"):
    """带时间戳的分级日志：INFO / WARN / ERROR（generate.sh 会同步写入日志文件）"""
    print(f"[{time.strftime('%H:%M:%S')}] [{level:>5}] +{time.time()-T0:5.1f}s {msg}", flush=True)

SCRIPT_DIR = Path(__file__).resolve().parent

def _deep_merge(base, extra):
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base

def load_config(path=None):
    cfg_path = Path(path) if path else SCRIPT_DIR / 'config.yaml'
    cfg = {}
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8')) or {}
    # 兜底默认值（config.yaml 缺项时使用）
    return _deep_merge({
        'font': {'path': 'KaiXinJiuXiaoLinYuJiuZou-2.ttf', 'em': 27.6, 'line_height': 1.2,
                 'heading_px': {'h1': 38.4, 'h2': 32.4, 'h3': 27.6, 'h4': 26.4, 'code': 24}},
        'ink': {'black': [10, 10, 10], 'red': [160, 15, 15],
                'alpha_jitter': [0.74, 1.0],
                'texture_strength': 0.85, 'texture_sigma': 60, 'texture_blur': 1,
                'pressure': 0.26},
        'glyph': {'canvas': 256, 'font_px': 192,
                  'jitter': {'size': [0.94, 1.06], 'red_boost': 1.16, 'dy_em': [-0.05, 0.05],
                             'rotate_deg': [-2.0, 2.0], 'scale_x': [0.88, 1.12],
                             'scale_y': [0.90, 1.10], 'skew_deg': [-2.0, 2.0]},
                  'quarter_affine': {'enabled': True, 'scale': [0.65, 1.4], 'rotate_deg': [-12, 12],
                                     'shift_x': [-14, 14], 'shift_y': [-12, 12], 'feather_blur': 8},
                  'bend_clamp_ratio': 0.45},
        'paper': {'width': 658, 'grid_cell': 24, 'grid_line': '#d9d9d9',
                  'field': {'block': 380, 'amp': 20},
                  'bands': {'count': [1, 3], 'width': [90, 350], 'gray': [230, 240], 'tilt_deg': [-7, 7]},
                  'noise': {'spray': {'clusters_per_page': [1, 2], 'dots': [45, 90],
                                      'radius': [50, 130], 'gray': [172, 214], 'dot_size': [1.5, 5.5]},
                            'strips_enabled': False,
                            'stains': {'count': [1, 2], 'radius': [60, 160], 'ring_prob': 0.4}}},
        'typos': {'enabled': True, 'rate': 0.008, 'pairs': {}},
        'page': {'margins_mm': {'top': 16, 'bottom': 18, 'left': 18, 'right': 18}, 'wrap_x': 600},
        'watermark': 'assets/cs_watermark.png',
        'page_number_font': 'Helvetica',
        'output_dir': '手写版',
    }, cfg)

CFG = load_config()

def C(*keys, default=None):
    d = CFG
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d

def R(key_path, default):
    """取形如 glyph.jitter.size 的随机区间"""
    v = C(*key_path.split('.'), default=default)
    lo, hi = (v if isinstance(v, (list, tuple)) and len(v) == 2 else (default, default))
    return random.Random, lo, hi

SYM_RE = re.compile(r'[\u2190-\u21FF\u2500-\u257F\u26A0\u26A1\uFE0F\u2713-\u2718]')  # 箭头/框线/警告/对勾
IMG_RE = re.compile(r'<img[^>]*/?>')

EM = C('font', 'em', default=27.6)
K_GLYPH = C('glyph', 'canvas', default=256) / (1.32 * EM)   # 字形画布 px 与显示 px 的换算
TYPO = {str(k): str(v) for k, v in C('typos', 'pairs', default={}).items()}
TYPO_RATE = float(C('typos', 'rate', default=0.008))
TYPO_ON = bool(C('typos', 'enabled', default=True))
_PEN_PALETTE = [tuple(x) for x in C('ink', 'pen_palette', default=[])]   # 多色笔：遇标题换笔
_co = C('typos', 'crossout', default={}) or {}
CO_TYPO = float(_co.get('typo_ratio', 0.35))     # 错别字被划掉并补写正确字的比例
CO_RANDOM = float(_co.get('random_rate', 0.002)) # 正常字被随机涂掉的比例
CO_SCRIB = float(_co.get('scribble_ratio', 0.3)) # 划掉方式中"来回涂抹"的比例

def build_css():
    hp = C('font', 'heading_px', default={})
    ls = min(0.17, max(0.0, float(C('font', 'line_squeeze', default=0.15))))   # 行距压缩量（em）
    return f"""
body {{
    font-family: 'KXJXLYJZ', 'Zhi Mang Xing', 'Xiaolai SC', 'Ma Shan Zheng', 'LXGW WenKai', serif;
    font-size: {EM}px;
    line-height: {C('font', 'line_height', default=1.2)};
    color: #000000;              /* 纯黑墨水 */
    background: #ffffff;         /* PDF 白页，纸张层在 .paper 上 */
}}
h1, h2, h3, h4, strong, b {{ font-weight: normal; }}   /* 一律不加粗，重点靠放大+红笔 */
h1 {{ font-size: {hp.get('h1', 38.4)}px; text-align: center; }}
h2 {{ font-size: {hp.get('h2', 32.4)}px; margin-top: 14px; }}
h3 {{ font-size: {hp.get('h3', EM)}px; margin-top: 10px; }}
h4 {{ font-size: {hp.get('h4', EM)}px; margin-top: 10px; }}
p  {{ margin: 4px 0; }}
code {{
    font-family: 'KXJXLYJZ', 'Zhi Mang Xing', 'Xiaolai SC', 'Ma Shan Zheng', 'LXGW WenKai', serif;
    font-size: {hp.get('code', 24)}px; color: #000;
}}
pre {{ padding: 2px 0 2px 16px; line-height: 1.35; }}
pre code {{ font-size: {hp.get('code', 24)}px; }}
ul, ol {{ list-style: none; padding-left: 0; margin: 2px 0; }}   /* 不要列表圆点 */
li {{ margin: 1px 0; }}
p, li, pre, h1, h2, h3, h4 {{ page-break-inside: avoid; }}   /* 段落整体跨页，避免文字被页边切半 */
.ch {{ height: 1.32em; vertical-align: {(0.19 - ls):.3f}em; margin: -{ls:.3f}em 0; }}   /* 负外边距收紧行盒：边框盒位置不变（字位不动），仅行距变小 */
"""

CSS = build_css()

def _resolve_font(rel):
    cand = [SCRIPT_DIR / rel, SCRIPT_DIR / 'fonts' / Path(rel).name,
            Path.home() / '.local/share/fonts' / Path(rel).name, Path(rel)]
    for c in cand:
        if c.exists():
            return c
    return SCRIPT_DIR / rel

FONT_PATH = _resolve_font(C('font', 'path', default='KaiXinJiuXiaoLinYuJiuZou-2.ttf'))
CHAR_DIR = SCRIPT_DIR / 'assets' / 'chars'
ASSETS = SCRIPT_DIR / 'assets'
WM_PNG = SCRIPT_DIR / C('watermark', default='assets/cs_watermark.png') if not Path(C('watermark', default='')).is_absolute() else Path(C('watermark'))
SIGN_PAGES = {int(p) for p in C('signature', 'pages', default=[1])}
SIGN_H = float(C('signature', 'height_pt', default=62))               # 签名高度（pt），宽度按原图比例
SIGN_OVERRIDE = False                                                 # --sign 命令行强制开启

_font_cache = {}

def _get_font(size=None):
    from PIL import ImageFont
    size = size or C('glyph', 'font_px', default=192)
    if size not in _font_cache:
        _font_cache[size] = ImageFont.truetype(str(FONT_PATH), size)
    return _font_cache[size]

_session_char_files = []      # 本次运行生成的字形 PNG（一次性文件，转换结束后统一清理）
_STRIKE_PNGS = [[], []]       # 手绘涂改素材：[划掉横线, 来回涂抹]

def _make_strike_pngs():
    """手绘涂改笔画：波浪线（笔压中粗两端细）+ 来回涂抹，墨迹纹理/渗透与字形同款"""
    from PIL import Image, ImageDraw, ImageChops, ImageFilter
    rng = random.Random('strike')
    ink = tuple(C('ink', 'black', default=[10, 10, 10]))
    W, H = 340, 100

    def stroke(d, y0, amp, thick):
        pts, n, ph = [], 26, rng.uniform(0, 6.28)
        freq = rng.uniform(4.5, 7)
        for i in range(n + 1):
            t = i / n
            x = 6 + t * (W - 12)
            y = y0 + amp * math.sin(ph + t * freq) + rng.uniform(-1.2, 1.2)
            pts.append((x, y))
        for i in range(len(pts) - 1):
            t = i / (len(pts) - 1)
            w = max(1.4, thick * (0.5 + 0.5 * math.sin(math.pi * t)))   # 笔压：中间重
            d.line([pts[i], pts[i + 1]], fill=ink + (255,), width=int(round(w)))
            r = w / 2
            if i == 0 or i == len(pts) - 2:
                x, y = pts[i if i == 0 else i + 1]
                d.ellipse([x - r, y - r, x + r, y + r], fill=ink + (255,))

    def one(kind):
        img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if kind == 'scribble':                    # 来回涂抹 3~4 笔
            y = rng.uniform(H * 0.3, H * 0.42)
            for _ in range(rng.randint(3, 4)):
                stroke(d, y, rng.uniform(2, 5), rng.uniform(5, 7.5))
                y += rng.uniform(H * 0.12, H * 0.2)
        else:                                     # 单/双横线划掉
            stroke(d, H * 0.48, rng.uniform(2.5, 5), rng.uniform(5.5, 7.5))
            if rng.random() < 0.45:
                stroke(d, H * 0.48 + rng.uniform(9, 17), rng.uniform(2, 4), rng.uniform(4.5, 6))
        a = img.getchannel('A')
        a = a.filter(ImageFilter.MaxFilter(3))    # 渗透毛边
        g = Image.effect_noise((W, H), 60).filter(ImageFilter.GaussianBlur(1))
        lut = [int(255 - max(0, i - 128) / 128 * 255 * 0.75) for i in range(256)]
        a = ImageChops.multiply(a, g.point(lut))  # 纸纹穿透
        img.putalpha(a)
        return img

    strikes = []
    for i in range(4):
        f = CHAR_DIR / f'strike_{i}.png'
        if not f.exists():
            one('strike').save(f)
        strikes.append(f)
    scr = []
    for i in range(3):
        f = CHAR_DIR / f'scribble_{i}.png'
        if not f.exists():
            one('scribble').save(f)
        scr.append(f)
    return strikes, scr

def _bridge_strokes(img, gap_px, min_area):
    """连通算法修复断笔：alpha 二值化 → scipy 标记连通块 → 对间距 ≤ gap_px 的组件对
    求最近点对，端点各自的主方向与连线共线（|cos|>0.72）才认定是断笔 → 画锥形桥接。
    合法的分离部件（如"心"的点）方向不共线，不会被误焊。"""
    import numpy as np
    from scipy import ndimage
    a = np.asarray(img.getchannel('A')).astype(np.uint8)
    mask = a > 90
    lab, n = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
    if n <= 1:
        return
    dt = ndimage.distance_transform_edt(mask)
    comps = []
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        ys, xs = np.where(lab[sl] == i)
        if len(ys) < min_area:
            continue
        comps.append((i, ys.astype(np.int32) + sl[0].start, xs.astype(np.int32) + sl[1].start))
    if len(comps) <= 1:
        return
    H, W = mask.shape
    out = a.copy()
    for ai in range(len(comps)):
        ia, ya, xa = comps[ai]
        for bi in range(ai + 1, len(comps)):
            ib, yb, xb = comps[bi]
            # bbox 间距预筛
            gy = max(0, max(int(ya.min()), int(yb.min())) - min(int(ya.max()), int(yb.max())))
            gx = max(0, max(int(xa.min()), int(xb.min())) - min(int(xa.max()), int(xb.max())))
            if gy > gap_px or gx > gap_px:
                continue
            # 最近点对（子采样加速）
            ya2, xa2, yb2, xb2 = ya[::2], xa[::2], yb[::2], xb[::2]
            d2 = (ya2[:, None] - yb2[None, :]) ** 2 + (xa2[:, None] - xb2[None, :]) ** 2
            k = int(np.argmin(d2))
            r_, c_ = divmod(k, len(yb2))
            pa = (int(ya2[r_]), int(xa2[r_]))
            pb = (int(yb2[c_]), int(xb2[c_]))
            dist = float(np.sqrt(d2.min()))
            if dist > gap_px or dist < 0.5:
                continue
            uy, ux = (pb[0] - pa[0]) / dist, (pb[1] - pa[1]) / dist
            ok = True
            for (pyy, pxx, yy, xx) in ((pa[0], pa[1], ya, xa), (pb[0], pb[1], yb, xb)):
                # 端点邻域主方向（PCA）需与连线共线
                sel = (np.abs(yy - pyy) <= 6) & (np.abs(xx - pxx) <= 6)
                if sel.sum() < 3:
                    ok = False
                    break
                vy = yy[sel] - pyy
                vx = xx[sel] - pxx
                cov = np.array([[np.mean(vy * vy), np.mean(vy * vx)],
                                [np.mean(vy * vx), np.mean(vx * vx)]])
                ev, evec = np.linalg.eigh(cov)
                v = evec[:, -1]
                if abs(v[0] * uy + v[1] * ux) < 0.60:
                    ok = False
                    break
            if not ok:
                continue
            # 锥形桥接：宽度取两端笔画宽度的较小者，中间略粗（笔压）
            wmax = float(np.clip(min(dt[pa] * 2, dt[pb] * 2), 1.6, 7.0))
            steps = max(2, int(dist * 2))
            for t in range(steps + 1):
                tt = -0.25 + 1.5 * t / steps            # 两端外延 25%：盖住略倾斜的断崖全宽
                tc = min(max(tt, 0.0), 1.0)
                yy = pa[0] + (pb[0] - pa[0]) * tt
                xx = pa[1] + (pb[1] - pa[1]) * tt
                r = max(0.8, wmax * (0.35 + 0.4 * math.sin(math.pi * tc))) / 2
                y0, y1 = int(yy - r), int(yy + r + 1)
                x0, x1 = int(xx - r), int(xx + r + 1)
                if 0 <= y0 and y1 < H and 0 <= x0 and x1 < W:
                    out[y0:y1, x0:x1] = np.maximum(out[y0:y1, x0:x1], 255)
    if (out != a).any():
        from PIL import Image as _I
        img.putalpha(_I.fromarray(out))

def render_char_png(token, color, variant, field, gx, gy):
    """把一个字/词渲染成 PNG：弯曲差分烘进 mesh（字随网格弯），
    整体位移单独返回、由 CSS translate 实现（画布小、不裁剪笔画）。
    gx/gy = 字在纸面坐标系里的近似位置。缺字返回 (None,0,0)。
    按 (字,颜色,变体) 落盘去重：同一字形只渲一次，多处按路径引用
    （浏览器按 URL 缓存解码，唯一图片数大减），规避大图量随机丢图。"""
    from PIL import Image, ImageDraw, ImageChops, ImageFilter
    canvas = int(C('glyph', 'canvas', default=256))
    key = hashlib.md5(f'{token}|{color}|{variant}|{FONT_PATH.name}'.encode()).hexdigest()[:12]
    CHAR_DIR.mkdir(parents=True, exist_ok=True)
    path = CHAR_DIR / f'{key}.png'
    s_disp = (1.32 * EM) / canvas
    rng = random.Random(key)
    font = _get_font(int(C('glyph', 'font_px', default=192)))
    try:
        wpx = int(font.getlength(token)) + canvas // 10
    except Exception:
        wpx = canvas - 36
    wpx = max(canvas // 2, min(wpx, canvas * 4))
    Hh = canvas
    dcx, dcy = field.sample(gx + wpx * s_disp / 2, gy + Hh * s_disp / 2)
    if path.exists():                    # 已有同一字形：直接按路径复用（整体位移仍按当前位置单独计算）
        try:
            bb = Image.open(path).getbbox()
            disp_w = ((bb[2] - bb[0]) if bb else wpx) * s_disp + EM * 0.05
            return path, dcx, dcy, disp_w
        except Exception:
            pass
    img = Image.new('RGBA', (wpx, Hh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((canvas // 20, canvas // 32), token, font=font, fill=(0, 0, 0, 255))
    if img.getbbox() is None:        # 字体缺字
        return None, 0.0, 0.0, 0.0
    # mesh：每点取所在位置场偏移与字中心偏移的差分（弯曲），内部再加少量随机（局部仿射）
    m = 3
    cellw = wpx / m
    maxo = cellw * float(C('glyph', 'bend_clamp_ratio', default=0.45))
    pts = [[(round(i * wpx / m), round(j * Hh / m)) for j in range(m + 1)] for i in range(m + 1)]
    for i in range(m + 1):
        for j in range(m + 1):
            u, v = pts[i][j]
            dx, dy = field.sample(gx + u * s_disp, gy + v * s_disp)
            ox = max(-maxo, min(maxo, (dx - dcx) * K_GLYPH))
            oy = max(-maxo, min(maxo, (dy - dcy) * K_GLYPH))
            if 0 < i < m and 0 < j < m:
                ox += rng.uniform(-4, 4)
                oy += rng.uniform(-3, 3)
            pts[i][j] = (int(u + ox), int(v + oy))
    data = []
    for i in range(m):
        for j in range(m):
            x0, y0 = pts[i][j]
            x1, y1 = pts[i + 1][j + 1]
            sx0, sy0 = round(i * wpx / m), round(j * Hh / m)
            sx1, sy1 = round((i + 1) * wpx / m), round((j + 1) * Hh / m)
            quad = (sx0, sy0, sx0, sy1, sx1, sy1, sx1, sy0)   # UL, LL, LR, UR
            data.append(((x0, y0, x1, y1), quad))
    MESH = getattr(Image, 'MESH', getattr(getattr(Image, 'Transform', Image), 'MESH'))
    BIL = getattr(Image, 'Resampling', Image).BICUBIC
    img = img.transform((wpx, Hh), MESH, data, resample=BIL)
    # 每字随机 1/4 区域强仿射：以"窗口化 MESH 形变"实现 —— 位移场随接近区域边界平滑归零，
    # 边界恒等 → 接缝两侧内容连续，不会再出现仿射贴回造成的笔画断裂
    qa = C('glyph', 'quarter_affine', default={})
    if qa.get('enabled', True):
        qw, qh = wpx // 2, Hh // 2
        sxr = qa.get('shift_x', [-14, 14]); syr = qa.get('shift_y', [-12, 12])
        qx = max(0, min(wpx - qw, rng.randint(0, 1) * qw + rng.randint(int(sxr[0]), int(sxr[1]))))
        qy = max(0, min(Hh - qh, rng.randint(0, 1) * qh + rng.randint(int(syr[0]), int(syr[1]))))
        region = img.crop((qx, qy, qx + qw, qy + qh))
        th = math.radians(rng.uniform(*qa.get('rotate_deg', [-12, 12])))
        st, ct = math.sin(th), math.cos(th)
        ffx, ffy = rng.uniform(*qa.get('scale', [0.65, 1.4])), rng.uniform(*qa.get('scale', [0.65, 1.4]))
        tx0, ty0 = rng.uniform(*sxr), rng.uniform(*syr)
        cxr, cyr = qw / 2, qh / 2
        # 正向位移场 d(q) = (A⁻¹ - I)(q - t)：区域内点被仿射推到的新位置相对原位的偏移
        a11, a12, a21, a22 = ct * ffx, -st * ffy, st * ffx, ct * ffy
        tcx = -(ct * (cxr + tx0) + st * (cyr + ty0)) / ffx + cxr
        tcy = (st * (cxr + tx0) - ct * (cyr + ty0)) / ffy + cyr
        m = 4
        band = 0.42 * min(qw, qh)               # 边界过渡带宽度
        data = []
        for j in range(m + 1):
            for i in range(m + 1):
                bx, by = i * qw / m, j * qh / m
                dx = (a11 - 1) * (bx - tcx) + a12 * (by - tcy)
                dy = a21 * (bx - tcx) + (a22 - 1) * (by - tcy)
                db = min(bx, by, qw - bx, qh - by)
                w = (min(1.0, db / band)) ** 1.5
                data.append((bx - dx * w, by - dy * w))
        mesh_data = []
        for j in range(m):
            for i in range(m):
                k = j * (m + 1) + i
                ul, ll = data[k], data[k + m + 1]
                lr, ur = data[k + m + 2], data[k + 1]
                mesh_data.append(((int(i * qw / m), int(j * qh / m), int((i + 1) * qw / m), int((j + 1) * qh / m)),
                                  (ul[0], ul[1], ll[0], ll[1], lr[0], lr[1], ur[0], ur[1])))
        MESH = getattr(Image, 'MESH', getattr(getattr(Image, 'Transform', Image), 'MESH'))
        BIL = getattr(Image, 'Resampling', Image).BILINEAR
        region = region.transform((qw, qh), MESH, mesh_data, resample=BIL)
        img.paste(region, (qx, qy))             # 边界恒等 → 硬贴也无缝
    # 抗断裂闭合：细笔接缝在重采样中最易掉到可见阈值以下 —— 先膨胀后腐蚀，
    # 缝隙 ≤ close_px 被焊回，笔画宽度不变；发丝连接恢复为实线
    from PIL import ImageFilter
    cl = int(C('glyph', 'close_px', default=2))
    if cl > 0:
        A0 = img.getchannel('A').filter(ImageFilter.MaxFilter(1 + 2 * cl)).filter(ImageFilter.MinFilter(1 + 2 * cl))
        img.putalpha(A0)
    # 连通修复：仿射/弯折可能在笔画交叉处留下断缝 —— 标记连通块，
    # 找到"端点相对、方向共线、间距 ≤ gap_px"的断笔，画锥形桥接（在墨纹前画，质感一致）
    br = C('glyph', 'bridge', default={})
    if br.get('enabled', True):
        try:
            _bridge_strokes(img, float(br.get('gap_px', 12)), int(br.get('min_area', 6)))
        except Exception as e:
            import traceback
            log(f"连通修复失败（忽略）: {e}", "WARN")
            traceback.print_exc()
    # 墨迹入纸：渗透毛边 + 纸纹穿透 + 笔压渐变（让字"长"在纸上而不是漂在上面）
    ink_cfg = C('glyph', 'ink', default={})
    A = img.getchannel('A')
    bp = int(ink_cfg.get('bleed_px', 1))
    if bp:                                   # 渗透：笔画轻微洇开
        A = A.filter(ImageFilter.MaxFilter(1 + 2 * bp))
    ts = float(ink_cfg.get('texture_strength', 0.75))
    if ts > 0:                               # 纸纹穿透：笔画内部墨色不均
        g = Image.effect_noise((wpx, Hh), int(ink_cfg.get('texture_sigma', 60)))
        g = g.filter(ImageFilter.GaussianBlur(int(ink_cfg.get('texture_blur', 1))))
        lut = [int(255 - max(0, i - 128) / 128 * 255 * ts) for i in range(256)]
        A = ImageChops.multiply(A, g.point(lut))
    pg = float(ink_cfg.get('pressure', 0.2))
    if pg > 0:                               # 笔压：斜向渐变，一笔内轻重不同
        grad = Image.linear_gradient('L').rotate(90).resize((wpx, Hh))
        lut2 = [int(255 - i / 255 * 255 * pg) for i in range(256)]
        A = ImageChops.multiply(A, grad.point(lut2))
    img.putalpha(A)
    # 着色：黑墨 / 红笔
    blk = C('ink', 'black', default=[10, 10, 10])
    red = C('ink', 'red', default=[200, 20, 20])
    solid = tuple(color)
    out = Image.new('RGBA', (wpx, Hh), solid + (0,))
    out.putalpha(img.getchannel('A'))
    bbox = img.getbbox()                             # 墨迹实际左右边界
    disp_w = ((bbox[2] - bbox[0]) if bbox else wpx) * s_disp + EM * 0.05
    out.save(path)
    if path not in _session_char_files:
        _session_char_files.append(path)
    return path, dcx, dcy, disp_w

def make_grid_png(path: Path, field, w_disp, h_disp, seed: str = ''):
    """从畸变场采样绘制整张透明网格纸（字形与网格共用同一份场）"""
    from PIL import Image, ImageDraw
    S = 2
    W, H = int(w_disp) * S, int(h_disp) * S
    img = Image.new('RGBA', (W, H), (255, 255, 255, 0))   # 透明底，露出下面的阴影带和噪点
    d = ImageDraw.Draw(img)
    step = 3
    cell = int(C('paper', 'grid_cell', default=24))
    line = C('paper', 'grid_line', default='#d9d9d9')
    for bx in range(0, int(w_disp) + 1, cell):
        pts = []
        for Y in range(0, H + 1, step):
            dx, dy = field.sample(bx, Y / S)
            pts.append((bx * S + dx * S, Y + dy * S))
        d.line(pts, fill=line, width=2)
    for by in range(0, int(h_disp) + 1, cell):
        pts = []
        for X in range(0, W + 1, step):
            dx, dy = field.sample(X / S, by)
            pts.append((X + dx * S, by * S + dy * S))
        d.line(pts, fill=line, width=2)
    img.save(path)

def make_noise_png(path: Path, seed: str, w_disp, h_disp):
    """扫描杂质层：喷漆溅点 + 污渍。位于最底层，永远不会遮挡文字和网格线"""
    from PIL import Image, ImageDraw
    rng = random.Random(str(seed) + 'noise')
    S = 2
    W, H = int(w_disp) * S, int(h_disp) * S
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))   # 透明底
    d = ImageDraw.Draw(img)
    # 高斯噪点颗粒：默认关闭（config.yaml noise.grain_enabled 开启）
    if C('paper', 'noise', 'grain_enabled', default=False):
        sigma = float(C('paper', 'noise', 'gauss_sigma', default=25))
        contrast = float(C('paper', 'noise', 'grain_contrast', default=1.2))
        a_k = float(C('paper', 'noise', 'grain_alpha', default=4.0))
        grain = Image.effect_noise((W, H), sigma)                # 灰度噪声，中心 128
        lut_g = [max(0, min(255, int(255 - (i - 128) * contrast))) for i in range(256)]
        lut_a = [max(0, min(255, int((128 - i) * a_k))) for i in range(256)]
        grain_rgba = grain.point(lut_g).convert('RGBA')
        grain_rgba.putalpha(grain.point(lut_a))
        img = Image.alpha_composite(img, grain_rgba)
    stains = C('paper', 'noise', 'stains', default={})
    for _ in range(rng.randint(*map(int, stains.get('count', [1, 2])))):
        sx, sy = rng.uniform(0, W), rng.uniform(0, H)
        r = rng.uniform(*stains.get('radius', [60, 160]))
        g = rng.randint(172, 205)
        stain = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(stain)
        for _ in range(rng.randint(18, 32)):
            ex = sx + rng.uniform(-0.8, 0.8) * r
            ey = sy + rng.uniform(-0.8, 0.8) * r
            er = r * rng.uniform(0.25, 0.75)
            a = int(rng.uniform(38, 62))
            sd.ellipse((ex - er, ey - er * rng.uniform(0.6, 1.2),
                        ex + er, ey + er * rng.uniform(0.6, 1.2)), fill=(g, g, g, a))
        for _ in range(rng.randint(6, 12)):
            ex = sx + rng.uniform(-0.35, 0.35) * r
            ey = sy + rng.uniform(-0.35, 0.35) * r
            er = r * rng.uniform(0.12, 0.35)
            a = int(rng.uniform(60, 88))
            sd.ellipse((ex - er, ey - er * rng.uniform(0.6, 1.2),
                        ex + er, ey + er * rng.uniform(0.6, 1.2)), fill=(g, g, g, a))
        if rng.random() < float(stains.get('ring_prob', 0.4)):
            rr = r * rng.uniform(0.8, 1.1)
            rw = rng.uniform(5, 12)
            sd.ellipse((sx - rr, sy - rr * 0.8, sx + rr, sy + rr * 0.8),
                       outline=(g - 8, g - 8, g - 8, 70), width=int(rw))
        img = Image.alpha_composite(img, stain)
    img.save(path)

class WarpField:
    """整页连贯的低频畸变场 + 阴影带凹陷：网格和字形共用同一份场，字随纸一起弯、
    阴影带处纸面向内向下凹陷，字体与网格一起贴合并变形"""
    def __init__(self, seed, w=None, h=12000, tilt_deg=0.0):
        f = C('paper', 'field', default={})
        self.w = w if w else int(C('paper', 'width', default=658))
        self.block = float(f.get('block', 380))
        amp = float(f.get('amp', 20))
        self.tilt = math.radians(tilt_deg)     # 整页微倾：网格与文字一起斜（剪切式，无裁剪）
        rng = random.Random(str(seed) + 'warp')
        self.nx = int(self.w / self.block) + 2
        self.ny = int(h / self.block) + 2
        self.ox = [[rng.uniform(-amp, amp) for _ in range(self.ny)] for _ in range(self.nx)]
        self.oy = [[rng.uniform(-amp, amp) for _ in range(self.ny)] for _ in range(self.nx)]
        self.dents = []                          # 阴影带凹陷：向内收 + 向下压

    def add_dent(self, cx, half_w, depth, pull, tilt_deg=0.0, y0=None, y1=None):
        """在阴影带位置加一个凹陷：cx 带中心，half_w 半宽，depth 下压深度，
        pull 向内收拢系数，tilt_deg 与阴影带一致的随机倾角，
        y0/y1 凹陷作用的页面 y 范围（阴影带只在所属页面生效）"""
        self.dents.append({'cx': cx, 'hw': half_w, 'depth': depth, 'pull': pull,
                           'rot': math.radians(tilt_deg), 'y0': y0, 'y1': y1})

    def sample(self, x, y):
        fx, fy = x / self.block, y / self.block
        i = max(0, min(int(fx), self.nx - 2))
        j = max(0, min(int(fy), self.ny - 2))
        ux = max(0.0, min(fx - i, 1.0))
        uy = max(0.0, min(fy - j, 1.0))
        dx = (self.ox[i][j] * (1 - ux) * (1 - uy) + self.ox[i + 1][j] * ux * (1 - uy)
              + self.ox[i][j + 1] * (1 - ux) * uy + self.ox[i + 1][j + 1] * ux * uy)
        dy = (self.oy[i][j] * (1 - ux) * (1 - uy) + self.oy[i + 1][j] * ux * (1 - uy)
              + self.oy[i][j + 1] * (1 - ux) * uy + self.oy[i + 1][j + 1] * ux * uy)
        dx += y * math.tan(self.tilt)          # 整体倾斜：随 y 线性水平偏移
        for dn in self.dents:                    # 阴影带凹陷：沿带轴（含随机倾角）向内收、向下压
            F = 60.0                             # 凹陷只在所属页面 y 范围内生效，边缘 60px 渐入渐出
            if dn['y0'] is not None:
                if y < dn['y0'] - F or y > dn['y1'] + F:
                    continue
                wy = max(0.0, min(1.0, (y - (dn['y0'] - F)) / F, ((dn['y1'] + F) - y) / F))
            else:
                wy = 1.0
            ct, st = math.cos(dn['rot']), math.sin(dn['rot'])
            u = (x - dn['cx']) * ct + y * st            # 旋到带轴向
            t = abs(u) / dn['hw']
            if t < 1.0:
                wgt = (1 - t * t) ** 2 * wy
                dx += -u / dn['hw'] * dn['pull'] * dn['hw'] * wgt * ct
                dy += -u / dn['hw'] * dn['pull'] * dn['hw'] * wgt * st + dn['depth'] * wgt
        return dx, dy

class Handwriter(HTMLParser):
    """collect 模式收集字符集；gen 模式逐字输出带局部畸变的字形图片，并追踪近似光标"""
    _ORIGINS = ('left bottom', 'center bottom', 'right bottom',
                'center center', 'left center')
    _BLOCK = {'p', 'h1', 'h2', 'h3', 'h4', 'li', 'pre', 'blockquote', 'tr', 'ul', 'ol', 'table', 'div'}

    def __init__(self, rng, mode='gen', red_chars=None, field=None):
        super().__init__(convert_charrefs=True)
        self.out, self.rng, self.mode = [], rng, mode
        self.red = 0
        self.skip = 0
        self.field = field
        self.cx, self.cy = 0.0, 0.0
        self.chars = set()
        self.red_chars = red_chars if red_chars is not None else set()
        self.stats = {'glyphs': 0, 'lines': 0, 'fallback': 0}
        self._seen = set()          # 去重后的唯一字形数
        self._pen = 0               # 当前笔（多色模式：每遇 h2 换一支）

    def _newline(self, h=None):
        if self.mode == 'gen':
            self.out.append('<br>')      # 换行由我方显式控制：行宽留余量，浏览器永不自行折行
            self.cy += (h if h else EM * float(C('font', 'line_height', default=1.2))) + self.rng.uniform(-2.5, 2.5)
            self.cx = self.rng.uniform(0, 6)
            self.stats['lines'] = self.stats.get('lines', 0) + 1

    def handle_starttag(self, tag, attrs):
        if tag in ('strong', 'b'):
            self.red += 1
        if tag in ('pre', 'code'):
            self.skip += 1
        if tag == 'h2':
            self._pen += 1          # 换一支笔
        if tag in self._BLOCK:
            self._newline(66 if tag == 'h1' else 57 if tag == 'h2' else 12)   # 空行：标题行盒按 1.32×字号折算
        if self.mode == 'gen':
            self.out.append(self.get_starttag_text() or f'<{tag}>')

    def handle_endtag(self, tag):
        if tag in ('strong', 'b') and self.red:
            self.red -= 1
        if tag in ('pre', 'code') and self.skip:
            self.skip -= 1
        if tag == 'p':
            self.cy += 4               # p { margin: 4px 0 }
        if self.mode == 'gen':
            self.out.append(f'</{tag}>')

    def handle_startendtag(self, tag, attrs):
        if self.mode == 'gen':
            self.out.append(self.get_starttag_text() or f'<{tag}/>')

    def handle_data(self, data):
        tokens = re.findall(r'[A-Za-z0-9]+|\s+|.', data, re.S)
        for tok in tokens:
            if tok.isspace():
                if self.mode == 'gen':
                    self.out.append(f'<span style="display:inline-block;width:{EM * 0.30:.1f}px"></span>')
                    self.cx += EM * 0.30
                continue
            if self.mode == 'collect':
                self.chars.add(tok)
                if self.red:
                    self.red_chars.add(tok)
                continue
            strike = None
            if not self.skip and len(tok) == 1:
                if TYPO_ON and tok in TYPO and self.rng.random() < TYPO_RATE:
                    if self.rng.random() < CO_TYPO:      # 划掉错字，后面补写正确的
                        if not any(_STRIKE_PNGS):
                            _STRIKE_PNGS[:] = [list(x) for x in _make_strike_pngs()]
                        strike = self.rng.choice(_STRIKE_PNGS[1] if self.rng.random() < CO_SCRIB else _STRIKE_PNGS[0])
                        self.stats['crossed'] = self.stats.get('crossed', 0) + 1
                    tok = TYPO[tok]
                elif self.rng.random() < CO_RANDOM:      # 正常字随机涂掉（不补写）
                    if not any(_STRIKE_PNGS):
                        _STRIKE_PNGS[:] = [list(x) for x in _make_strike_pngs()]
                    strike = self.rng.choice(_STRIKE_PNGS[1] if self.rng.random() < CO_SCRIB * 1.5 else _STRIKE_PNGS[0])
                    self.stats['crossed'] = self.stats.get('crossed', 0) + 1
            if self.red:
                color = tuple(C('ink', 'red', default=[200, 20, 20]))
            elif _PEN_PALETTE:
                color = _PEN_PALETTE[self._pen % len(_PEN_PALETTE)]
            else:
                color = tuple(C('ink', 'black', default=[10, 10, 10]))
            variant = self.rng.randrange(int(C('glyph', 'variants', default=3)))
            if self.mode == 'gen' and self.cx > 575:      # 提前折行：行宽留余量，浏览器不会抢先折行
                self._newline()
            p, dcx, dcy, disp_w = render_char_png(tok, color, variant, self.field,
                                                  30 + self.cx, 26 + self.cy)
            self._seen.add((tok, color, variant))
            self.out.append(self._wrap_img(tok, p, dcx, dcy, strike=strike))
            self.cx += disp_w + EM * 0.04      # 精确推进：消除词粘连
            self.stats['glyphs'] = self.stats.get('glyphs', 0) + 1

    def _wrap_img(self, tok, p, dcx=0.0, dcy=0.0, strike=None):
        # 行距小，整体位移限幅防止相邻行压线（弯曲差分已在字形 mesh 里）；下限收紧防左缘裁字
        dcx = max(-8.0, min(14.0, dcx))
        dcy = max(-9.0, min(9.0, dcy))
        j = C('glyph', 'jitter', default={})
        r = self.rng
        size = r.uniform(*j.get('size', [0.94, 1.06]))
        if self.red:
            size *= float(j.get('red_boost', 1.16))
        dy = r.uniform(*j.get('dy_em', [-0.05, 0.05]))
        rot = r.uniform(*j.get('rotate_deg', [-2.0, 2.0]))
        sx = r.uniform(*j.get('scale_x', [0.88, 1.12]))
        sy = r.uniform(*j.get('scale_y', [0.90, 1.10]))
        skx = r.uniform(*j.get('skew_deg', [-2.0, 2.0]))
        ls = r.uniform(0.0, 0.01)
        a0, a1 = C('ink', 'alpha_jitter', default=[0.82, 1.0])
        alpha = r.uniform(a0, a1)
        blk = C('ink', 'black', default=[10, 10, 10])
        red = C('ink', 'red', default=[200, 20, 20])
        col = red if self.red else blk
        color = f'rgba({col[0]},{col[1]},{col[2]},{alpha:.2f})'
        origin = r.choice(self._ORIGINS)
        tf = (f'translate({dcx:.1f}px,{dcy:.1f}px) translateY({dy:.3f}em) '
              f'rotate({rot:.2f}deg) skewX({skx:.2f}deg) scale({sx:.3f},{sy:.3f})')
        style = (f'display:inline-block;font-size:{size:.3f}em;'
                 f'transform-origin:{origin};-webkit-transform:{tf};transform:{tf};'
                 f'letter-spacing:{ls:.3f}em;color:{color};')
        if p is None:
            # 字体缺字：绝不把原字写进 HTML —— wkhtmltopdf 无系统中文字体时会渲染成“?”
            self.stats['fallback'] = self.stats.get('fallback', 0) + 1
            if self.stats['fallback'] <= 8:
                log(f"缺字跳过 {tok!r}（字体无此字，原样输出会变成问号方块）", "WARN")
            return ''
        if strike:
            style += 'position:relative;'
            sl, st_, sw = self.rng.uniform(-16, -7), self.rng.uniform(33, 46), self.rng.uniform(118, 136)
            return (f'<span style="{style}"><img class="ch" src="{p.as_uri()}">'
                    f'<img class="strike" src="{strike.as_uri()}" '
                    f'style="position:absolute;left:{sl:.0f}%;top:{st_:.0f}%;width:{sw:.0f}%"></span>')
        return f'<span style="{style}"><img class="ch" src="{p.as_uri()}"></span>'

def handwrite_html(body, seed, field):
    c = Handwriter(random.Random(str(seed) + 'collect'), mode='collect')
    c.feed(body)
    c.close()
    g = Handwriter(random.Random(str(seed)), mode='gen', red_chars=c.red_chars, field=field)
    g.feed(body)
    g.close()
    out = ''.join(g.out)
    if not out.strip():
        # 输出为空说明解析链路坏了：直接报错，绝不把原始文本静默传给 wkhtmltopdf（那会整页渲染成问号）
        raise RuntimeError('手写化输出为空：HTML 解析异常，中止而不是回退原始文本')
    st = dict(g.stats)
    st['unique'] = len(g._seen)
    return out, st

def _render_signature_img():
    """签名：逐字走完整字形管线（与正文同款墨迹纹理/笔压/扭曲），轻微错落，看起来就是笔记里的字"""
    from PIL import Image
    text = C('signature', 'text', default='张三')
    rng = random.Random('sig-' + text)
    field = WarpField('sig-' + text)
    s_disp = (1.32 * EM) / 256
    imgs, advs, dys = [], [], []
    x = 0.0
    for ch in text:
        p, _, _, disp_w = render_char_png(ch, tuple(C('ink', 'black', default=[10, 10, 10])),
                                          rng.randrange(int(C('glyph', 'variants', default=3))),
                                          field, 30 + x / s_disp, 30)
        im = Image.open(p)
        imgs.append(im)
        advs.append(disp_w / s_disp)          # 原生像素步进
        dys.append(rng.uniform(-5, 9))        # 轻微高低错落
        x += disp_w / s_disp
    H = 286
    W = int(sum(advs)) + 24
    out = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    cx = 12
    for im, adv, dy in zip(imgs, advs, dys):
        out.alpha_composite(im, (int(cx), int(H - im.height - 12 + dy)))
        cx += adv
    bb = out.getbbox()
    out = out.crop(bb) if bb else out
    thin = int(C('signature', 'thin_px', default=6))
    if thin > 0:                         # 签名笔画变细：对 alpha 腐蚀（比正文墨迹瘦一圈）
        from PIL import ImageFilter
        n = max(1, thin // 2)
        out.putalpha(out.getchannel('A').filter(ImageFilter.MinFilter(1 + 2 * n)))
    return out

def stamp_overlay(pdf_file: Path):
    """逐页盖印：虚线框水印（右下角、压字无所谓）+ 正常字体页码"""
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import Color
    reader = PdfReader(str(pdf_file))
    w = float(reader.pages[0].mediabox.width)
    h = float(reader.pages[0].mediabox.height)
    m = C('page', 'margins_mm', default={})
    margin = float(m.get('bottom', 18)) * 72 / 25.4
    wm_h = 42.0
    wm_w = wm_h * 419 / 125
    wm_x = w - margin - 12.0 - wm_w
    wm_y = margin + 7.5
    sig_im = None
    if SIGN_OVERRIDE or C('signature', 'enabled', default=False):
        sig_im = _render_signature_img()
        sig_w = SIGN_H * sig_im.width / sig_im.height
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h))
    for i in range(len(reader.pages)):
        if WM_PNG.exists():
            c.drawImage(str(WM_PNG), wm_x, wm_y, wm_w, wm_h)
            c.setStrokeColor(Color(0.65, 0.65, 0.65))
            c.setLineWidth(1.5)
            c.setDash(4, 3)
            c.rect(wm_x - 6, wm_y - 6, wm_w + 12, wm_h + 12)
            c.setFont(C('page_number_font', default='Helvetica'), 13)
            c.setFillColorRGB(0.1, 0.1, 0.1)
            c.drawRightString(wm_x - 16, wm_y + 14, str(i + 1))
        # 手写签名：第一页右上角，随机大小/位置/角度，像随手签的
        if sig_im and (i + 1) in SIGN_PAGES:
            from reportlab.lib.utils import ImageReader
            _srng = random.Random(str(pdf_file) + '-sig')
            _h = SIGN_H * _srng.uniform(0.9, 1.12)
            _wd = _h * sig_im.width / sig_im.height
            c.saveState()
            c.translate(w - margin - _wd - _srng.uniform(4, 26),
                        h - margin - _h - _srng.uniform(6, 30))
            c.rotate(_srng.uniform(-5, 3))
            c.drawImage(ImageReader(sig_im), 0, 0, _wd, _h, mask='auto')
            c.restoreState()
        c.showPage()
    c.save()
    buf.seek(0)
    overlay = PdfReader(buf)
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        page.merge_page(overlay.pages[i])
        writer.add_page(page)
    with open(pdf_file, 'wb') as f:
        writer.write(f)



def _pdf_image_stats(pdf_path):
    """用 poppler 统计 (图像对象总数, 宽>1000 的整页背景数)：检测 wkhtmltopdf 是否丢图"""
    try:
        out = subprocess.run(['pdfimages', '-list', str(pdf_path)],
                             capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return None, None           # 无 pdfimages 时跳过自检
    rows = [l.split() for l in out.splitlines()[2:] if l.strip()]
    return len(rows), sum(1 for r in rows if len(r) > 4 and r[3].isdigit() and int(r[3]) > 1000)


def convert(md_path: Path, out_dir: Path, salt: str = '', stamp: bool = True):
    md_path = Path(md_path).resolve()    # 绝对路径：避免与 cwd 叠加导致输入文件找不到
    if not FONT_PATH.exists():
        log(f"配置字体不存在: {FONT_PATH}，将回退到系统已装手写字体", "WARN")
    log(f"开始转换: {md_path.name}")
    text = md_path.read_text(encoding='utf-8')
    text = text.replace('->', ' ').replace('=>', ' ')
    text = SYM_RE.sub('', text)
    if not text.strip():
        log("输入内容为空，跳过", "WARN")
        return None
    lr = random.Random(str(md_path.name) + salt + 'layout')
    tilt = lr.uniform(-0.5, 0.5)           # 整页倾斜：由弯曲场剪切实现（网格与文字一起斜，无裁剪）
    field = WarpField(str(md_path.name) + salt, tilt_deg=tilt)
    rot = lr.uniform(-0.35, 0.35)          # 容器仅微旋（大角度会让底部文字平移出纸面被裁）
    tx = lr.uniform(-18, 8)
    ty = lr.uniform(0, 14)
    sc = lr.uniform(1.0, 1.02)
    dent_cfg = C('paper', 'dent', default={})
    bands = C('paper', 'bands', default={})
    rng_band = random.Random(str(md_path.name) + salt + 'band')
    band_data = []
    # 真实纸张照片背景（AI 生成或实拍）：设置后跳过合成网格与阴影带，文字直接写在照片纸上
    bg_img = C('paper', 'background_image')
    use_bg = bool(bg_img)
    # 每一页独立随机 0~2 条阴影带 + 对应凹陷：位置/宽度/角度/灰度/渐变全部独立
    for k in range(60):                     # 最多按 60 页准备
        for _ in range(rng_band.randint(*map(int, bands.get('count', [0, 2])))):
            bw = rng_band.uniform(*bands.get('width', [90, 350]))
            bx = rng_band.uniform(-60, 620)
            g = rng_band.randint(*bands.get('gray', [230, 240]))
            trot = rng_band.uniform(*bands.get('tilt_deg', [-7, 7]))
            depth = rng_band.uniform(*dent_cfg.get('depth', [3, 4]))
            pull = float(dent_cfg.get('pull', 0.05))
            y0, y1 = k * 993, k * 993 + 993
            field.add_dent(bx + bw / 2, bw / 2, depth, pull, trot, y0 - 40, y1 + 40)
            band_data.append({'k': k, 'x': bx, 'w': bw, 'g': g, 'tilt': trot,
                              's1': rng_band.uniform(0.35, 0.5), 's2': rng_band.uniform(0.55, 0.75)})
    ASSETS.mkdir(parents=True, exist_ok=True)
    grid_png = ASSETS / f'grid_{md_path.stem}.png'
    noise_png = ASSETS / f'noise_{md_path.stem}.png'
    make_grid_png(grid_png, field, 658, 993, seed=str(md_path.name) + salt)   # 第一遍只为数页数，用小贴图
    make_noise_png(noise_png, str(md_path.name) + salt, int(C('paper', 'width', default=658)), 993)
    grid_div = '' if use_bg else (
        f'<div style="position:absolute;z-index:-1;top:0;left:0;right:0;bottom:0;'
        f"background-image:url('{grid_png.as_uri()}');"
        f'background-size:100% 100%;background-repeat:no-repeat;"></div>')
    noise_div = (f'<div style="position:absolute;z-index:-2;top:0;left:0;right:0;bottom:0;'
                 f"background-image:url('{noise_png.as_uri()}');"
                 f'background-size:100% 100%;background-repeat:no-repeat;"></div>')
    body = markdown.markdown(text, extensions=['tables', 'fenced_code'])
    body = IMG_RE.sub('', body)
    body, hw_stats = handwrite_html(body, seed=str(md_path.name) + salt, field=field)
    log(f"字形 {hw_stats['glyphs']} 个（唯一图片 {hw_stats.get('unique', '-')}），"
        f"行 {hw_stats['lines']}，缺字跳过 {hw_stats.get('fallback', 0)}，"
        f"涂改 {hw_stats.get('crossed', 0)} 处")
    def paper_div(min_h=None, band_html=''):
        mh = f'min-height:{min_h}px;' if min_h else ''
        bg_css = (f"background-image:url('{Path(str(bg_img)).resolve().as_uri()}');"
                  f'background-size:100% 100%;background-repeat:no-repeat;') if use_bg \
            else 'background-color:#ffffff;'
        paper_style = (f'position:relative;overflow:hidden;{bg_css}{mh}'
                       f'padding:26px 30px;'
                       f'-webkit-transform:rotate({rot:.2f}deg) translate({tx:.0f}px,{ty:.0f}px) scale({sc:.3f});'
                       f'transform:rotate({rot:.2f}deg) translate({tx:.0f}px,{ty:.0f}px) scale({sc:.3f});'
                       f'transform-origin:center top;')
        return f'<div style="{paper_style}">{noise_div}{band_html}{grid_div}{body}</div>'
    def build_html(min_h=None, band_html=''):
        return (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
                f'<title>{md_path.stem}</title><style>{build_css()}</style></head>'
                f'<body>{paper_div(min_h, band_html)}</body></html>')
    html_file = md_path.parent / f'.hw_{md_path.stem}.tmp.html'
    out_dir = Path(out_dir).resolve()      # 绝对化：wkhtmltopdf 的 cwd 是 md 所在目录，相对路径会写错位置
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_file = out_dir / f'{md_path.stem}（手写版）.pdf'
    m = C('page', 'margins_mm', default={})
    cmd_base = ['wkhtmltopdf', '--enable-local-file-access', '--quiet',
                '--margin-top', str(m.get('top', 16)), '--margin-bottom', str(m.get('bottom', 18)),
                '--margin-left', str(m.get('left', 18)), '--margin-right', str(m.get('right', 18))]
    from pypdf import PdfReader as _R
    # 整篇连续渲染（版面连贯，无半空页）；字形落盘去重后唯一图片数低；
    # 万一仍丢图（背景条/字形对象数不足）自动重渲，最多 3 次
    for attempt in range(3):
        html_file.write_text(build_html(None), encoding='utf-8')
        subprocess.run(cmd_base + [str(html_file), str(pdf_file)], check=True, cwd=str(md_path.parent))
        n = len(_R(str(pdf_file)).pages)
        log(f"首遍渲染 {n} 页")
        make_grid_png(grid_png, field, 658, n * 993 - 12, seed=str(md_path.name) + salt)
        make_noise_png(noise_png, str(md_path.name) + salt, int(C('paper', 'width', default=658)), n * 993 - 12)
        # 阴影带 div：每页各自的随机带（只生成实际存在页面的）
        band_html = ''.join(
            f'<div style="position:absolute;z-index:-3;top:{b["k"] * 993 - 15:.0f}px;left:{b["x"]:.0f}px;'
            f'width:{b["w"]:.0f}px;height:1023px;'
            f'background:linear-gradient(90deg, #ffffff 0%, #{b["g"]:02x}{b["g"]:02x}{b["g"]:02x} {b["s1"] * 100:.0f}%, #{b["g"]:02x}{b["g"]:02x}{b["g"]:02x} {b["s2"] * 100:.0f}%, #ffffff 100%);'
            f'-webkit-transform:rotate({b["tilt"]:.1f}deg);transform:rotate({b["tilt"]:.1f}deg);"></div>'
            for b in band_data if b['k'] < n)
        html_file.write_text(build_html(n * 993 - 12, band_html), encoding='utf-8')
        subprocess.run(cmd_base + [str(html_file), str(pdf_file)], check=True, cwd=str(md_path.parent))
        rows, wide = _pdf_image_stats(pdf_file)
        need_wide = 1 if use_bg else 2
        if rows is not None and (wide < need_wide or rows < hw_stats.get('unique', 0) * 1.5):
            log(f"第 {attempt + 1} 次渲染丢图（图像对象 {rows}，背景条 {wide}），重渲…", "WARN")
            continue
        log(f"渲染 {n} 页，图像对象 {rows}，背景条 {wide}")
        break
    else:
        raise RuntimeError(f'{md_path.name} 重渲 3 次仍丢图，中止')
    if stamp:
        try:
            stamp_overlay(pdf_file)
            log("水印与页码盖印完成")
        except ImportError:
            log("缺 pypdf/reportlab，水印页码未盖印", "WARN")
    html_file.unlink(missing_ok=True)
    grid_png.unlink(missing_ok=True)
    noise_png.unlink(missing_ok=True)
    # 清理本次生成的字形 PNG（按需重渲即可，防止 assets/chars 无限膨胀）
    removed = 0
    for p in set(_session_char_files):
        try:
            p.unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
    _session_char_files.clear()
    if removed:
        log(f"已清理字形临时文件 {removed} 个")
    return pdf_file

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('inputs', nargs='*', default=['.'], help='md 文件或目录，默认当前目录')
    ap.add_argument('-o', '--out', default=None, help='输出目录（默认读 config.yaml 的 output_dir）')
    ap.add_argument('-c', '--config', default=None, help='配置文件路径（默认脚本同目录 config.yaml）')
    ap.add_argument('--seed-salt', default='', help='随机种子盐：换一批笔误/弯曲/溅点')
    ap.add_argument('--no-stamp', action='store_true', help='跳过水印与页码盖印')
    ap.add_argument('--sign', action='store_true', help='在 signature.pages 指定页盖手写签名（assets/signature.png）')
    args = ap.parse_args()
    global SIGN_OVERRIDE
    SIGN_OVERRIDE = args.sign
    global CFG, EM, K_GLYPH, TYPO, TYPO_RATE, TYPO_ON, FONT_PATH, WM_PNG, CSS, CO_TYPO, CO_RANDOM, CO_SCRIB, _PEN_PALETTE
    if args.config:
        CFG = load_config(args.config)
        EM = C('font', 'em', default=27.6)
        K_GLYPH = C('glyph', 'canvas', default=256) / (1.32 * EM)
        TYPO = {str(k): str(v) for k, v in C('typos', 'pairs', default={}).items()}
        _PEN_PALETTE = [tuple(x) for x in C('ink', 'pen_palette', default=[])]   # 多色笔：遇标题换笔
        _co = C('typos', 'crossout', default={}) or {}
        CO_TYPO = float(_co.get('typo_ratio', 0.35))
        CO_RANDOM = float(_co.get('random_rate', 0.002))
        CO_SCRIB = float(_co.get('scribble_ratio', 0.3))
        TYPO_RATE = float(C('typos', 'rate', default=0.008))
        TYPO_ON = bool(C('typos', 'enabled', default=True))
        FONT_PATH = SCRIPT_DIR / C('font', 'path', default='')
        CSS = build_css()
    out_dir = Path(args.out) if args.out else SCRIPT_DIR / C('output_dir', default='手写版')
    out_dir.mkdir(exist_ok=True)
    files = []
    for inp in args.inputs:
        p = Path(inp)
        if p.is_dir():
            files += sorted(p.glob('*.md'))
        elif p.suffix == '.md':
            files.append(p)
    ok = 0
    for f in files:
        try:
            pdf = convert(f, out_dir, salt=args.seed_salt, stamp=not args.no_stamp)
            log(f"✓ {f.name} -> {pdf}")
            ok += 1
        except Exception:
            log(f"✗ {f.name} 转换失败，堆栈如下", "ERROR")
            traceback.print_exc()
    log(f"=== 结束: 成功 {ok}/{len(files)}，总耗时 {time.time()-T0:.1f}s ===")

if __name__ == '__main__':
    main()
