# handwrite-notes 手写笔记生成

把 Markdown 渲染成"手机拍照扫描的手写笔记本"风格 PDF：
真实手写字体逐字变形、弯曲网格纸、竖向阴影带、溅点污渍、红笔重点、
形近字笔误、**手写涂改划掉**、连通算法自动修复笔画断裂、右下角扫描
水印虚线框 + 印刷体页码、可选手写签名。单文档 10~40 秒。

## 效果预览

| 彩色笔（多色轮换） | 英文 | 古诗词 |
|---|---|---|
| ![彩色](docs/preview-彩色中文.png) | ![英文](docs/preview-英文.png) | ![诗词](docs/preview-诗词.png) |

涂改细节（错别字划掉补写、连通算法修复前后）：

![涂改细节](docs/preview-涂改细节.png)

所有例子均为程序生成（签名是占位的"张三"，由当前手写字体渲染）。

## 技术链路

```
Markdown
  │ markdown → HTML（图片/箭头剔除，代码永不写错）
  ▼
逐字字形管线（每字每变体只渲一次，落盘 assets/chars 去重，HTML 按路径引用）
  ├ 字体栅格化 192px
  ├ 3×3 MESH 弯曲：纸面弯曲场差分烘进字形，字随网格弯
  ├ 1/4 区域窗口化仿射：位移场随边界平滑归零，接缝不再撕裂
  ├ 连通修复（scipy）：标记连通块 → 断笔端点共线校验 → 锥形桥接
  ├ 墨迹：渗透毛边 → 纸纹穿透 → 笔压渐变
  └ 随机仿射抖动（字号/旋转/剪切/漂移）
  ▼
HTML 拼版（网格纸 + 阴影带 + 溅点污渍做背景层，字形为内容层）
  ▼
wkhtmltopdf 连续渲染成整张长纸再分页
  ├ poppler 自检：背景条/图片对象数不足 → 自动重渲（≤3 次）
  ▼
pypdf + reportlab 盖印：扫描水印虚线框 + 印刷体页码 + 可选手写签名
```

### 为什么这样做

- **字形落盘去重**：同一（字， 颜色， 变体）只渲一次、按路径引用，浏览器按 URL
  缓存解码，唯一图片数降为字形数的 ~1/2，根治大文档时 wkhtmltopdf 随机丢图
- **整篇连续渲染**：版面连贯，天然没有分块造成的半空页
- **窗口化仿射**：位移在区域边界归零，接缝两侧内容连续——断裂从源头消除，
  残余细缝由连通算法按"端点共线"标准桥接（合法分离部件如"心"的点不误焊）

## 快速开始

```bash
bash install.sh                 # 装依赖（改环境前会先询问；--yes 跳过询问）
python3 md2handwrite.py 笔记.md -o 输出目录           # 单文件
python3 md2handwrite.py 笔记.md --sign                # 带手写签名（config 签名内容）
bash generate.sh                # 批量转换 笔记/ 下所有 md（含日志 logs/）
```

更多命令与参数见 [SKILL.md](SKILL.md)（AI 助手直调说明）。

## Markdown 写法约定

- `**加粗**` → 红笔重点（放大不加粗），只标核心句
- 代码块 → 手写体代码，代码与命令**永不出现错别字**
- 正文按 `typos.rate` 概率出现形近字笔误，部分会被"划掉并补写"（手写涂改）
- 图片、箭头符号自动剔除

## 主要配置（config.yaml）

| 参数 | 说明 |
|---|---|
| font.path / em / line_height / line_squeeze | 字体、字号、行距与行距压缩 |
| ink.black / red / pen_palette | 黑墨 / 红笔 / 多色笔轮换（可选） |
| glyph.jitter / quarter_affine / bridge | 整字抖动、区域仿射、连通修复 |
| typos.rate / crossout.* | 笔误概率；涂改（划掉补写/随机涂掉/涂抹比例） |
| signature.text / pages / height_pt / thin_px | 手写签名（默认"张三"，--sign 开启） |
| paper.grid_cell / bands / noise / background_image | 网格、阴影带、杂质、真实纸张照片 |
| watermark / output_dir | 水印图 / 默认输出目录 |

## 字体与许可

默认字体 **开鑫九霄**（个人手写风字体，随库分发）；`fonts/` 另有 5 款开源
手写字体可选，协议见 `fonts/README.md`。项目代码 MIT。
