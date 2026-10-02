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
import subprocess, argparse, random, re, html as H, hashlib, io, math
from pathlib import Path
from html.parser import HTMLParser
import markdown
import yaml

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
        'ink': {'black': [10, 10, 10], 'red': [200, 20, 20], 'alpha_jitter': [0.82, 1.0]},
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

def build_css():
    hp = C('font', 'heading_px', default={})
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
.ch {{ height: 1.32em; vertical-align: -0.19em; }}   /* 逐字图片与文字基线对齐 */
"""

CSS = build_css()

FONT_PATH = SCRIPT_DIR / C('font', 'path', default='KaiXinJiuXiaoLinYuJiuZou-2.ttf')
if not FONT_PATH.exists():
    FONT_PATH = Path.home() / '.local/share/fonts' / C('font', 'path', default='')
CHAR_DIR = SCRIPT_DIR / 'assets' / 'chars'
ASSETS = SCRIPT_DIR / 'assets'
WM_PNG = SCRIPT_DIR / C('watermark', default='assets/cs_watermark.png') if not Path(C('watermark', default='')).is_absolute() else Path(C('watermark'))

_font_cache = {}

def _get_font(size=None):
    from PIL import ImageFont
    size = size or C('glyph', 'font_px', default=192)
    if size not in _font_cache:
        _font_cache[size] = ImageFont.truetype(str(FONT_PATH), size)
    return _font_cache[size]

def render_char_png(token, color, variant, field, gx, gy):
    """把一个字/词渲染成 PNG：弯曲差分烘进 mesh（字随网格弯），
    整体位移单独返回、由 CSS translate 实现（画布小、不裁剪笔画）。
    gx/gy = 字在纸面坐标系里的近似位置。缺字返回 (None,0,0)。按位置量化缓存。"""
    from PIL import Image, ImageDraw
    canvas = int(C('glyph', 'canvas', default=256))
    key = hashlib.md5(f'{token}|{color}|{variant}|{int(gx//40)}|{int(gy//40)}'.encode()).hexdigest()[:10]
    CHAR_DIR.mkdir(parents=True, exist_ok=True)
    path = CHAR_DIR / f'{key}.png'
    s_disp = (1.32 * EM) / canvas
    if path.exists():
        dcx, dcy = field.sample(gx + 14 * s_disp, gy + 18 * s_disp)
        return path, dcx, dcy
    rng = random.Random(key)
    font = _get_font(int(C('glyph', 'font_px', default=192)))
    try:
        wpx = int(font.getlength(token)) + canvas // 10
    except Exception:
        wpx = canvas - 36
    wpx = max(canvas // 2, min(wpx, canvas * 4))
    Hh = canvas
    img = Image.new('RGBA', (wpx, Hh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((canvas // 20, canvas // 32), token, font=font, fill=(0, 0, 0, 255))
    if img.getbbox() is None:        # 字体缺字
        return None, 0.0, 0.0
    dcx, dcy = field.sample(gx + wpx * s_disp / 2, gy + Hh * s_disp / 2)
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
    BIL = getattr(Image, 'Resampling', Image).BILINEAR
    img = img.transform((wpx, Hh), MESH, data, resample=BIL)
    # 每字随机 1/4 区域强仿射：随机象限+随机偏移位置，子图 AFFINE 后羽化贴回
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
        aff = (ct / ffx, st / ffx, -(ct * (cxr + tx0) + st * (cyr + ty0)) / ffx + cxr,
               -st / ffy, ct / ffy, (st * (cxr + tx0) - ct * (cyr + ty0)) / ffy + cyr)
        AFF = getattr(Image, 'AFFINE', getattr(getattr(Image, 'Transform', Image), 'AFFINE'))
        region = region.transform((qw, qh), AFF, aff, resample=BIL)
        from PIL import ImageFilter
        mask = Image.new('L', (qw, qh), 0)
        ImageDraw.Draw(mask).rectangle((10, 10, qw - 10, qh - 10), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(int(qa.get('feather_blur', 8))))
        img.paste(region, (qx, qy), mask)
    # 着色：黑墨 / 红笔
    blk = C('ink', 'black', default=[10, 10, 10])
    red = C('ink', 'red', default=[200, 20, 20])
    solid = tuple(red) if color == 'red' else tuple(blk)
    out = Image.new('RGBA', (wpx, Hh), solid + (0,))
    out.putalpha(img.getchannel('A'))
    out.save(path)
    return path, dcx, dcy

def make_grid_png(path: Path, field, w_disp, h_disp):
    """从畸变场采样绘制整张透明网格纸（字形与网格共用同一份场）"""
    from PIL import Image, ImageDraw
    S = 2
    W, H = int(w_disp) * S, int(h_disp) * S
    img = Image.new('RGBA', (W, H), (255, 255, 255, 0))   # 透明底，露出下面的阴影带和噪点
    d = ImageDraw.Draw(img)
    step = 12
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
    def __init__(self, seed, w=None, h=12000):
        f = C('paper', 'field', default={})
        self.w = w if w else int(C('paper', 'width', default=658))
        self.block = float(f.get('block', 380))
        amp = float(f.get('amp', 20))
        rng = random.Random(str(seed) + 'warp')
        self.nx = int(self.w / self.block) + 2
        self.ny = int(h / self.block) + 2
        self.ox = [[rng.uniform(-amp, amp) for _ in range(self.ny)] for _ in range(self.nx)]
        self.oy = [[rng.uniform(-amp, amp) for _ in range(self.ny)] for _ in range(self.nx)]
        self.dents = []                          # 阴影带凹陷：向内收 + 向下压

    def add_dent(self, cx, half_w, depth, pull):
        """在阴影带位置加一个凹陷：cx 带中心，half_w 半宽，depth 下压深度，pull 向内收拢系数"""
        self.dents.append({'cx': cx, 'hw': half_w, 'depth': depth, 'pull': pull})

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
        for dn in self.dents:                    # 阴影带凹陷：中心向内收、整带向下压，边缘平滑
            t = abs(x - dn['cx']) / dn['hw']
            if t < 1.0:
                wgt = (1 - t * t) ** 2
                dx += (dn['cx'] - x) * dn['pull'] * wgt
                dy += dn['depth'] * wgt
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

    def _newline(self, h=None):
        if self.mode == 'gen':
            self.cy += h if h else EM * float(C('font', 'line_height', default=1.2))
            self.cx = 0.0

    def handle_starttag(self, tag, attrs):
        if tag in ('strong', 'b'):
            self.red += 1
        if tag in ('pre', 'code'):
            self.skip += 1
        if tag in self._BLOCK:
            self._newline(40 if tag in ('h1', 'h2') else 34)
        if self.mode == 'gen':
            self.out.append(self.get_starttag_text() or f'<{tag}>')

    def handle_endtag(self, tag):
        if tag in ('strong', 'b') and self.red:
            self.red -= 1
        if tag in ('pre', 'code') and self.skip:
            self.skip -= 1
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
                    self.out.append(tok)
                    self.cx += EM * 0.45
                continue
            if self.mode == 'collect':
                self.chars.add(tok)
                if self.red:
                    self.red_chars.add(tok)
                continue
            if TYPO_ON and not self.skip and len(tok) == 1 and tok in TYPO and self.rng.random() < TYPO_RATE:
                tok = TYPO[tok]
            color = 'red' if self.red else 'black'
            variant = self.rng.randrange(2)
            adv = len(tok) * EM * (0.55 if len(tok) > 1 else 0.9)
            p, dcx, dcy = render_char_png(tok, color, variant, self.field,
                                          30 + self.cx, 26 + self.cy)
            self.out.append(self._wrap_img(tok, p, dcx, dcy))
            self.cx += adv
            if self.cx > float(C('page', 'wrap_x', default=600)):
                self._newline()

    def _wrap_img(self, tok, p, dcx=0.0, dcy=0.0):
        # 行距小，整体位移限幅防止相邻行压线（弯曲差分已在字形 mesh 里）
        dcx = max(-12.0, min(12.0, dcx))
        dcy = max(-8.0, min(8.0, dcy))
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
            return f'<span style="{style}">{H.escape(tok)}</span>'
        return f'<span style="{style}"><img class="ch" src="{p.as_uri()}"></span>'

def handwrite_html(body, seed, field):
    c = Handwriter(random.Random(str(seed) + 'collect'), mode='collect')
    c.feed(body)
    c.close()
    g = Handwriter(random.Random(str(seed)), mode='gen', red_chars=c.red_chars, field=field)
    g.feed(body)
    g.close()
    return ''.join(g.out) or body

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

def convert(md_path: Path, out_dir: Path, salt: str = '', stamp: bool = True):
    md_path = Path(md_path).resolve()    # 绝对路径：避免与 cwd 叠加导致输入文件找不到
    text = md_path.read_text(encoding='utf-8')
    text = text.replace('->', ' ').replace('=>', ' ')
    text = SYM_RE.sub('', text)
    body = markdown.markdown(text, extensions=['tables', 'fenced_code'])
    body = IMG_RE.sub('', body)
    field = WarpField(str(md_path.name) + salt)
    # 阴影带与凹陷：先于字形渲染生成 —— 带内网格与文字一起向内向下凹陷
    lr = random.Random(str(md_path.name) + salt + 'layout')
    rot = lr.uniform(-3.2, 3.2)
    tx = lr.uniform(-30, 8)
    ty = lr.uniform(0, 16)
    sc = lr.uniform(1.0, 1.025)
    dent_cfg = C('paper', 'dent', default={})
    bands = C('paper', 'bands', default={})
    rng_band = random.Random(str(md_path.name) + salt + 'band')
    band_html = ''
    for _ in range(rng_band.randint(*map(int, bands.get('count', [1, 3])))):
        bw = rng_band.uniform(*bands.get('width', [90, 350]))
        bx = rng_band.uniform(-60, 620)
        g = rng_band.randint(*bands.get('gray', [230, 240]))
        trot = rng_band.uniform(*bands.get('tilt_deg', [-7, 7]))
        field.add_dent(bx + bw / 2, bw / 2,
                       rng_band.uniform(*dent_cfg.get('depth', [14, 22])),
                       float(dent_cfg.get('pull', 0.3)))
        band_html += (f'<div style="position:absolute;z-index:-3;top:-15%;left:{bx:.0f}px;'
                      f'width:{bw:.0f}px;height:130%;'
                      f'background:linear-gradient(90deg, #ffffff 0%, #{g:02x}{g:02x}{g:02x} 45%, #{g:02x}{g:02x}{g:02x} 62%, #ffffff 100%);'
                      f'-webkit-transform:rotate({trot:.1f}deg);transform:rotate({trot:.1f}deg);"></div>')
    ASSETS.mkdir(parents=True, exist_ok=True)
    grid_png = ASSETS / f'grid_{md_path.stem}.png'
    noise_png = ASSETS / f'noise_{md_path.stem}.png'
    make_grid_png(grid_png, field, int(C('paper', 'width', default=658)), 993)   # 第一遍只为数页数，用小贴图
    make_noise_png(noise_png, str(md_path.name) + salt, int(C('paper', 'width', default=658)), 993)
    grid_div = (f'<div style="position:absolute;z-index:-1;top:0;left:0;right:0;bottom:0;'
                f"background-image:url('{grid_png.as_uri()}');"
                f'background-size:100% 100%;background-repeat:no-repeat;"></div>')
    noise_div = (f'<div style="position:absolute;z-index:-2;top:0;left:0;right:0;bottom:0;'
                 f"background-image:url('{noise_png.as_uri()}');"
                 f'background-size:100% 100%;background-repeat:no-repeat;"></div>')
    body = handwrite_html(body, seed=str(md_path.name) + salt, field=field)
    def paper_div(min_h=None):
        mh = f'min-height:{min_h}px;' if min_h else ''
        paper_style = (f'position:relative;overflow:hidden;background-color:#ffffff;{mh}'
                       f'padding:26px 30px;'
                       f'-webkit-transform:rotate({rot:.2f}deg) translate({tx:.0f}px,{ty:.0f}px) scale({sc:.3f});'
                       f'transform:rotate({rot:.2f}deg) translate({tx:.0f}px,{ty:.0f}px) scale({sc:.3f});'
                       f'transform-origin:center top;')
        return f'<div style="{paper_style}">{noise_div}{band_html}{grid_div}{body}</div>'
    def build_html(min_h=None):
        return (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
                f'<title>{md_path.stem}</title><style>{build_css()}</style></head>'
                f'<body>{paper_div(min_h)}</body></html>')
    html_file = md_path.parent / f'.hw_{md_path.stem}.tmp.html'
    pdf_file = out_dir / f'{md_path.stem}（手写版）.pdf'
    tmp_pdf = out_dir / f'.tmp_{md_path.stem}.pdf'
    html_file.write_text(build_html(None), encoding='utf-8')
    m = C('page', 'margins_mm', default={})
    marg = [('--margin-top', str(m.get('top', 16))), ('--margin-bottom', str(m.get('bottom', 18))),
            ('--margin-left', str(m.get('left', 18))), ('--margin-right', str(m.get('right', 18)))]
    cmd_base = ['wkhtmltopdf', '--enable-local-file-access', '--quiet'] + [x for p in marg for x in p]
    subprocess.run(cmd_base + [str(html_file), str(tmp_pdf)], check=True, cwd=str(md_path.parent))
    from pypdf import PdfReader as _R
    n = len(_R(str(tmp_pdf)).pages)
    tmp_pdf.unlink(missing_ok=True)
    make_grid_png(grid_png, field, int(C('paper', 'width', default=658)), n * 993 - 12)
    make_noise_png(noise_png, str(md_path.name) + salt, int(C('paper', 'width', default=658)), n * 993 - 12)
    html_file.write_text(build_html(n * 993 - 12), encoding='utf-8')
    subprocess.run(cmd_base + [str(html_file), str(pdf_file)], check=True, cwd=str(md_path.parent))
    if stamp:
        try:
            stamp_overlay(pdf_file)
        except ImportError:
            print('  (提示: 缺 pypdf/reportlab，水印页码未盖印)')
    html_file.unlink(missing_ok=True)
    return pdf_file

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('inputs', nargs='*', default=['.'], help='md 文件或目录，默认当前目录')
    ap.add_argument('-o', '--out', default=None, help='输出目录（默认读 config.yaml 的 output_dir）')
    ap.add_argument('-c', '--config', default=None, help='配置文件路径（默认脚本同目录 config.yaml）')
    ap.add_argument('--seed-salt', default='', help='随机种子盐：换一批笔误/弯曲/溅点')
    ap.add_argument('--no-stamp', action='store_true', help='跳过水印与页码盖印')
    args = ap.parse_args()
    global CFG, EM, K_GLYPH, TYPO, TYPO_RATE, TYPO_ON, FONT_PATH, WM_PNG, CSS
    if args.config:
        CFG = load_config(args.config)
        EM = C('font', 'em', default=27.6)
        K_GLYPH = C('glyph', 'canvas', default=256) / (1.32 * EM)
        TYPO = {str(k): str(v) for k, v in C('typos', 'pairs', default={}).items()}
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
    for f in files:
        try:
            pdf = convert(f, out_dir, salt=args.seed_salt, stamp=not args.no_stamp)
            print(f'✓ {f.name} -> {pdf}')
        except subprocess.CalledProcessError:
            print(f'✗ {f.name} 转换失败')

if __name__ == '__main__':
    main()
