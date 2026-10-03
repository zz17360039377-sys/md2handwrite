---
name: handwrite-notes
description: 把 Markdown 笔记渲染成仿真实手写扫描风格的 PDF（手写字体逐字变形、弯曲网格纸、红笔重点、噪点污渍、扫描水印页码，可选手写签名）。当用户要把 md/笔记转成手写风格 PDF、生成手写版笔记、模拟手写扫描件、给作业附手写笔记时使用。
---

# 手写笔记生成（handwrite-notes）

把 Markdown 渲染成"拍照扫描的手写笔记本"风格 PDF：手写字体逐字变形（弯曲烘入 +
1/4 区域强仿射 + 墨迹纹理/渗透/笔压）、弯曲网格纸、竖向阴影带、溅点污渍、
红笔重点（md `**加粗**` 标红放大不加粗）、少量形近字笔误（代码永不写错）、
右下角扫描水印虚线框 + 印刷体页码、可选手写签名。参数全部在 config.yaml。

## AI 调用速查（照抄即可）

```bash
# 0) 依赖只需装一次（wkhtmltopdf + python3 包）
bash /home/zz/Desktop/ROS/class/handwrite-notes/install.sh

# 1) 转换任意路径的 md（推荐：明确输出目录）
cd /home/zz/Desktop/ROS/class/handwrite-notes
python3 md2handwrite.py /绝对路径/笔记.md -o /绝对路径/输出目录

# 2) 要手写签名（签名内容改 config.yaml 的 signature.text，默认"张三"）
python3 md2handwrite.py /绝对路径/笔记.md -o 输出目录 --sign

# 3) 换一批随机笔误/弯曲/噪点（同一 md 生成不同样子）
python3 md2handwrite.py 笔记.md --seed-salt v2

# 4) 批量转换本目录 笔记/ 下全部 md（带完整日志 logs/）
bash generate.sh
```

- 不传 `-o` 时输出到 **当前目录** 的 `手写版/`；文件名 = 原名 +（手写版）.pdf
- 转换后自检：`pdftotext 输出.pdf -` 应该几乎为空（只有页码数字）——
  提取出正文说明出了回退 bug；`pdfinfo` 看页数是否合理
- 单文件约 10~40 秒（随长度）；日志里"缺字跳过">0 表示字体缺该字（已自动跳过）

## 工作原理（v2.1，2026-10）

- 字形按（字， 颜色， 变体）**落盘去重**到 `assets/chars/`，HTML 按文件路径引用；
  wkhtmltopdf 按 URL 缓存解码，唯一图片数 = 字形数/3 左右
- 整篇**连续渲染**成一张长纸再分页（版面连贯，无半空页）；渲染后用 poppler
  自检背景条/图片对象数，**丢图自动重渲（≤3 次）**
- 历史坑（已根治，勿走回头路）：分块渲染会造成半空页；字形不做去重会在
  大文档时随机丢图（背景条消失）；缺字回退原字会变成"?"页（现在缺字=跳过+日志）

## Markdown 写法约定

- `**加粗**` → 红笔重点（放大不加粗），只标核心句
- ``` 代码块 → 手写体代码，代码与命令永不出现错别字
- 图片、箭头符号自动去除；正文按 typos.rate 概率出形近字笔误

## config.yaml 常用参数

| 参数 | 说明 |
|---|---|
| font.path / font.em | 手写字体文件 / 正文字号（px） |
| font.line_height / font.line_squeeze | 行距倍数 / 行距压缩量（em，越大越挤） |
| signature.text / pages / height_pt / thin_px | 手写签名内容 / 页码 / 高度 / 笔画变细 |
| ink.red / ink.black | 红笔与黑墨 RGB（**加粗**标红） |
| glyph.jitter.* / glyph.quarter_affine.* | 整字随机仿射 / 1/4 区域强变形幅度 |
| paper.grid_cell / field / bands / noise | 网格间距、弯曲场、阴影带、溅点污渍 |
| typos.rate / pairs | 错别字概率与形近字映射 |
| watermark / output_dir | 水印图路径（留空不盖印）/ 默认输出目录 |

改完保存重跑即生效；改字体后删 `assets/chars/` 清缓存。

## 签名（--sign）

- 用当前手写字体走**与正文完全相同的字形管线**渲染（同款墨迹），第一页右上角
  随机大小/位置/角度，默认高度 ≈ 正文 1.25 倍
- 换名字/页码/粗细：config.yaml → signature.text / pages / height_pt / thin_px
- 默认关闭；单次开启加 `--sign`，或 config 里 `signature.enabled: true`

## 故障排查

- 现象"?"页：已根治（缺字跳过）。若再现 = 字体文件损坏，换 fonts/ 下字体
- 背景条缺失/大片空白：看日志是否触发"丢图…重渲"；连续 3 次失败再报
- 页码/水印缺失：确认 pypdf、reportlab 已装，assets/cs_watermark.png 存在
- 依赖报错：重跑 install.sh

## 纸张背景与字体

- `papers/`：真实纸张照片。config `paper.background_image` 指向即用；留空用合成网格纸
- `fonts/`：6 款可选手写字体；默认 **开鑫九霄**（个人手写风字体，已随库分发），
  换字体改 font.path 并清 assets/chars/
- 项目代码 MIT；第三方字体各自协议见 fonts/README.md
