"""
把 eval CSV 渲染成 PNG（README 截图用）。

用法：
    .\.venv\Scripts\python.exe eval/render_csv_screenshot.py
"""
from __future__ import annotations

import csv
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

REPORTS_DIR = Path("reports")
LATEST_RUN_JSON = REPORTS_DIR / "latest_run.json"
OUT_DIR = Path("reports/screenshots")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def pick_font(size: int) -> ImageFont.FreeTypeFont:
    """找一个能渲染中文的字体（Windows 自带微软雅黑 / SimHei）。"""
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    for c in candidates:
        if Path(c).exists():
            try:
                return ImageFont.truetype(c, size)
            except OSError:
                continue
    return ImageFont.load_default()


def read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open("r", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows:
        return [], []
    return rows[0], rows[1:]


def render_table(
    headers: list[str],
    rows: list[list[str]],
    out_path: Path,
    *,
    title: str,
    max_col_widths: dict[int, int] | None = None,
    title_size: int = 22,
    row_size: int = 14,
    header_size: int = 15,
) -> None:
    """用 Pillow 画一张表格截图。

    列宽策略：
    - 默认按表头最长字符串估算
    - 但允许每列指定最大像素宽（防 got/expected JSON 列爆炸）
    """
    font_title = pick_font(title_size)
    font_header = pick_font(header_size)
    font_row = pick_font(row_size)

    # 列宽估算：每列用最长字符串的字符宽度 * 像素
    tmp = Image.new("RGB", (1, 1))
    d = ImageDraw.Draw(tmp)

    def text_w(s: str, font) -> int:
        bbox = d.textbbox((0, 0), s, font=font)
        return bbox[2] - bbox[0]

    col_widths: list[int] = []
    for ci, h in enumerate(headers):
        max_w = text_w(h, font_header) + 24
        for r in rows:
            if ci < len(r):
                max_w = max(max_w, text_w(r[ci], font_row) + 24)
        cap = (max_col_widths or {}).get(ci)
        if cap:
            max_w = min(max_w, cap)
        col_widths.append(max_w)

    # 行高
    line_h = row_size + 14
    header_h = header_size + 16
    title_h = title_size + 24
    pad = 24

    table_w = sum(col_widths) + pad * 2
    table_h = title_h + header_h + line_h * len(rows) + pad * 2

    img = Image.new("RGB", (table_w, table_h), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    # 标题
    draw.text((pad, pad), title, fill=(20, 20, 30), font=font_title)

    # 表头
    y = pad + title_h
    x = pad
    for ci, h in enumerate(headers):
        draw.rectangle(
            [x, y, x + col_widths[ci], y + header_h],
            fill=(238, 240, 248),
            outline=(180, 185, 200),
        )
        draw.text((x + 12, y + 6), h, fill=(30, 30, 50), font=font_header)
        x += col_widths[ci]

    # 数据行
    for ri, row in enumerate(rows):
        y += header_h if ri == 0 else line_h
        x = pad
        bg = (250, 250, 252) if ri % 2 == 0 else (255, 255, 255)
        for ci, cell in enumerate(row):
            draw.rectangle(
                [x, y, x + col_widths[ci], y + (line_h if ri > 0 else header_h)],
                fill=bg,
                outline=(220, 222, 230),
            )
            # 简单截断：如果内容超长，截断到列宽
            text = cell
            while text and text_w(text, font_row) > col_widths[ci] - 16:
                text = text[:-2]
            if text != cell:
                text = text[:-1] + "…"
            draw.text((x + 8, y + (8 if ri > 0 else 6)), text, fill=(40, 40, 50), font=font_row)
            x += col_widths[ci]

    img.save(out_path)
    print(f"[render] {out_path}  ({table_w}x{table_h})")


def render_summary():
    src = REPORTS_DIR / "20260712-201551" / "eval_summary.csv"
    headers, rows = read_csv(src)
    render_table(
        title="Smart Appointment AI Agent — 评测总览 (eval_summary.csv)",
        headers=headers,
        rows=rows,
        out_path=OUT_DIR / "eval_summary.png",
        max_col_widths={6: 220, 7: 140, 8: 140, 9: 220},
    )


def render_appointment_detail():
    src = REPORTS_DIR / "20260712-201551" / "per_agent" / "appointment.csv"
    headers, rows = read_csv(src)
    # 截短 input / expected / got 列（JSON 串太长）
    if rows:
        for r in rows:
            if len(r) > 3 and r[3]:
                r[3] = r[3][:60] + "…" if len(r[3]) > 60 else r[3]
            if len(r) > 4 and r[4]:
                r[4] = r[4][:80] + "…" if len(r[4]) > 80 else r[4]
            if len(r) > 2 and r[2]:
                r[2] = r[2][:30] + "…" if len(r[2]) > 30 else r[2]
    render_table(
        title="Appointment Agent — 详情 (per_agent/appointment.csv)",
        headers=["case_id", "scenario", "input", "expected.success", "got.matches"],
        rows=[[r[0], r[1], r[2], r[3], r[4] if len(r) > 4 else ""] for r in rows],
        out_path=OUT_DIR / "appointment_detail.png",
        max_col_widths={2: 220, 3: 140, 4: 220},
    )


def render_reflection_detail():
    src = REPORTS_DIR / "20260712-201551" / "per_agent" / "reflection.csv"
    headers, rows = read_csv(src)
    if rows:
        for r in rows:
            if len(r) > 2 and r[2]:
                r[2] = r[2][:40] + "…" if len(r[2]) > 40 else r[2]
            if len(r) > 3 and r[3]:
                r[3] = r[3][:30] + "…" if len(r[3]) > 30 else r[3]
            if len(r) > 4 and r[4]:
                r[4] = r[4][:80] + "…" if len(r[4]) > 80 else r[4]
    render_table(
        title="Reflection Agent — 详情 (per_agent/reflection.csv)",
        headers=["case_id", "scenario", "input", "expected", "got.matches"],
        rows=[[r[0], r[1], r[2], r[3], r[4] if len(r) > 4 else ""] for r in rows],
        out_path=OUT_DIR / "reflection_detail.png",
        max_col_widths={2: 280, 3: 220, 4: 280},
    )


if __name__ == "__main__":
    render_summary()
    render_appointment_detail()
    render_reflection_detail()