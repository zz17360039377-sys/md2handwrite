---
name: handwrite-notes
description: 把 Markdown 笔记渲染成仿真实手写扫描风格的 PDF（大字手写体、逐字局部仿射变形、弯曲网格纸、红笔重点、喷漆噪点污渍、扫描水印与页码）。当用户要求把 md 笔记转成手写风格 PDF、生成手写感笔记、模拟手写扫描件时使用。
---

# 手写笔记生成（Handwrite Notes）

把 Markdown 笔记渲染成"拍照扫描的手写笔记本"风格 PDF：
大字手写字体（逐字局部仿射变形，随机 1/4 区域强变形）、低频弯曲网格纸、
竖向阴影带、喷漆溅点与污渍杂质层、红笔重点（md 中 **加粗** 即标红，放大不加粗）、
少量形近字笔误、右下角水印虚线框 + 正常字体页码。所有参数集中在 config.yaml。

## 使用步骤

1. 确认依赖（只需一次）：`bash install.sh`
   需要：wkhtmltopdf（sudo apt install wkhtmltopdf）、Python 包 pypdf / reportlab / pillow / pyyaml
2. 把要转的 `.md` 笔记放进 `笔记/` 目录（或记住文件路径）
3. 运行一键脚本：`bash 一键生成.sh`
   或手动指定：`python3 md2handwrite.py 笔记/xxx.md`（可传多个文件或整个目录）
4. 输出在 `手写版/` 目录，文件名 = 原名 + （手写版）.pdf

## Markdown 写法约定

- `**加粗**` → 红笔重点（放大、不加粗），只标真正核心的句子
- ``` 代码块 → 手写体代码，**代码和命令永不出现错别字**
- 图片、箭头、框线符号会被自动去除
- 正文会按小概率出现形近字笔误（模拟真人手写），由 config.yaml 的 typos 控制

## config.yaml 常用参数

| 参数 | 说明 |
|---|---|
| font.path / font.em | 手写字体文件 / 正文字号 |
| font.line_height | 行距倍数 |
| ink.red / ink.black | 红笔与黑墨 RGB |
| glyph.jitter.* | 整字随机仿射：字号、旋转、缩放、剪切范围 |
| glyph.quarter_affine.* | 每字随机 1/4 区域强仿射的强度开关与幅度 |
| paper.field.block / amp | 整页网格弯曲场的块大小与幅度（越大越皱） |
| paper.bands.* | 竖向阴影带数量、宽度、灰度、倾斜 |
| paper.noise.spray.* | 喷漆溅点：每页簇数、点数、散布半径 |
| paper.noise.stains.* | 污渍数量、大小、圆环渍概率 |
| typos.rate / pairs | 错别字概率与形近字映射表 |
| page.margins_mm | PDF 页边距 |
| watermark | 右下角水印图片路径（留空则不盖印） |
| output_dir | 输出目录 |

改完 config.yaml 保存，重新运行即可生效；改 typos/字体后如无变化，删除 assets/chars/ 清空字形缓存。

## 依赖与故障

- `As = 1.0/w` 除零报错：字形网格退化，已内置限幅；如再遇到，调小 quarter_affine 幅度
- 页码/水印不出现：确认 pypdf、reportlab 已安装，且 assets/cs_watermark.png 存在
- 换字体：替换 fonts/ 下的 ttf 或改 config.yaml 的 font.path，然后删除 assets/chars/ 缓存

## 字体许可

**默认随包字体：小赖字体 SC（XiaolaiSC，SIL OFL 开源协议，可自由再分发）**，来源 [npm @proj-airi/font-xiaolai](https://www.npmmirror.com/package/@proj-airi/font-xiaolai)。
想用其他手写字体（如商业字体）：放入 fonts/ 并改 config.yaml 的 font.path——**非开源字体已被 .gitignore 排除，切勿提交到 git**。项目代码 MIT。
