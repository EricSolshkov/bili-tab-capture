"""Staff-aware suggestions and reviewed row-to-bar crops. No notation inference."""
import io
import json
import zipfile
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def _groups(values, gap=1):
    return np.split(values, np.where(np.diff(values) > gap)[0] + 1) if len(values) else []


def _peak_threshold(profile):
    """Otsu separation of the observed projection distribution, not fixed ink ratios."""
    values = np.asarray(profile, dtype=np.float32)
    if not len(values) or float(np.ptp(values)) < 1e-6:
        return float('inf')
    lo, hi = float(values.min()), float(values.max())
    scaled = np.round((values-lo)/(hi-lo)*255).astype(np.uint8)
    threshold, _ = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return lo + (threshold+.5)/255*(hi-lo)


def suggest_lines(image, strings=None):
    """Require equally spaced staff lines and ink across every inter-string gap.

    Prefer the bottom matching staff (TAB beneath notation). Do not fall back to
    full-image vertical projections when there is no credible staff.
    """
    rgb = np.asarray(image.convert("RGB"))
    gray = rgb.max(axis=2).astype(np.uint8)  # suppress pale coloured playhead backgrounds
    if np.median(gray) < 128:
        gray = 255 - rgb.min(axis=2).astype(np.uint8)
    h, w = gray.shape
    if min(h, w) < 12:
        return dict(lines=[], staff=[], confidence="low", reason="图片过小")
    mask = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV, 15, 5)
    horizontal = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, max(15, w // 14)), np.uint8))
    support = (horizontal > 0).mean(axis=1)
    horizontal_threshold = _peak_threshold(support)
    # Split the lower Otsu class once more: faint staff peaks otherwise get
    # assigned to the background when a video border dominates the histogram.
    faint_threshold = _peak_threshold(support[support <= horizontal_threshold])
    if np.isfinite(faint_threshold):
        horizontal_threshold = faint_threshold
    ys = [int(round(np.average(g, weights=support[g]))) for g in _groups(np.flatnonzero(support > horizontal_threshold))]
    candidates = []
    for count in [4, 5, 6, 7]:
        for i in range(len(ys) - count + 1):
            staff = np.array(ys[i:i + count])
            gaps = np.diff(staff)
            spacing = float(np.median(gaps))
            if spacing >= 3 and np.max(abs(gaps - spacing)) <= max(1.5, spacing * .18):
                # Avoid claiming a subset of a larger staff as a different instrument.
                before = i > 0 and abs(ys[i] - ys[i-1] - spacing) <= spacing * .22
                after = i + count < len(ys) and abs(ys[i+count] - ys[i+count-1] - spacing) <= spacing * .22
                if not before and not after:
                    candidates.append((staff[-1], staff, spacing))
    if not candidates:
        return dict(lines=[], staff=[], confidence="low", reason="未找到可信的等间距弦线，请手动标线或收紧谱面区域")
    _, staff, spacing = max(candidates, key=lambda c: c[0])
    # A small horizontal neighbourhood tolerates antialiasing and slight skew;
    # assess each gap separately so note stems/digits cannot win by total ink.
    ink = cv2.dilate(1.0-gray.astype(np.float32)/255.0, np.ones((1, 3), np.uint8))
    gap_scores = []
    for a, b in zip(staff[:-1], staff[1:]):
        pad = max(1, int(spacing * .18))
        gap_scores.append(np.quantile(ink[a+pad:b-pad+1], .25, axis=0))
    # Robust projection: lower-tail strength across the staff and across gaps
    # suppresses letters/stems before class separation, so a dark stem cannot
    # raise the threshold above a faint but continuous barline.
    continuity = np.quantile(ink[staff[0]:staff[-1]+1], .1, axis=0)
    projection = np.minimum(continuity, np.min(gap_scores, axis=0))
    vertical_threshold = _peak_threshold(projection)
    supported = projection > vertical_threshold
    hits = np.flatnonzero(supported)
    peaks = []
    for group in _groups(hits, max(2, int(spacing * .45))):
        if len(group) > max(8, spacing * 1.4):
            continue  # broad glyph/background, not a thin separator
        x = int(round(group.mean()))
        if 2 <= x <= w - 2:
            best = int(group[np.argmax(projection[group])])
            variation = float(np.std(ink[staff[0]:staff[-1]+1, best]))
            peaks.append((x, variation))
    # Aligned T/A/B glyphs can create a vertical peak too. Their intensity
    # varies far more along y than real rules; reject robust variance outliers.
    variations = np.array([v for _, v in peaks])
    limit = float('inf')
    if len(variations) >= 4:
        median = float(np.median(variations))
        mad = float(np.median(abs(variations-median)))
        limit = median + 4 * max(1.4826*mad, 1/255)
    lines = [round(x/w, 6) for x, variation in peaks if variation <= limit]
    mismatch = strings is not None and strings != len(staff)
    return dict(lines=lines, staff=[int(y) for y in staff], detectedStrings=len(staff), confidence="suggested",
                horizontal=[round(float(x), 3) for x in support],
                vertical=[round(float(x), 3) for x in projection],
                horizontalThreshold=float(horizontal_threshold) if np.isfinite(horizontal_threshold) else 1.0,
                verticalThreshold=float(vertical_threshold) if np.isfinite(vertical_threshold) else 1.0,
                reason=(f"横向峰值为 {len(staff)} 根弦线，与配置 {strings} 不同；请核对弦数。" if mismatch else "") +
                       "投影统计建议；请核对首尾边界和小节编号")


def fingerprint(image):
    gray = np.asarray(image.convert("RGB")).max(axis=2).astype(np.uint8)
    if np.median(gray) < 128:
        gray = 255 - gray
    return cv2.resize((gray < 150).astype(np.float32), (320, 96), interpolation=cv2.INTER_AREA)


def propose_rows(saved, crops_dir, strings):
    """Only coalesce adjacent near-identical views. Never remove capture records."""
    rows = []
    anchor = None
    for capture in saved.get("captures", []):
        record = next((r for r in saved.get("bar_records", []) if r.get("file") == capture["file"]), {})
        alternatives = [c["file"] for c in record.get("candidates", [])] or [capture["file"]]
        path = Path(crops_dir) / capture["file"]
        if not path.is_file():
            continue
        with Image.open(path) as image:
            fp = fingerprint(image)
            same = anchor is not None and float(np.abs(fp-anchor).sum() / max(1, np.maximum(fp, anchor).sum())) < .075
            if same:
                rows[-1]["candidates"].extend(alternatives)
                continue
            detection = suggest_lines(image, strings)
            lines = detection["lines"]
            rows.append(dict(file=capture["file"], candidates=alternatives,
                             first=None, boundaries=lines if len(lines) >= 2 else [0, 1],
                             top=0, bottom=1, confirmed=False, excluded=False,
                             detection=detection))
            anchor = fp
    return rows


def active_interval(image, boundaries, staff):
    """Locate the coloured playhead/highlight statistically inside the TAB band."""
    if not staff or len(boundaries) < 2:
        return None
    pixels = np.asarray(image.convert('RGB'), dtype=np.float32)
    band = pixels[max(0, staff[0]):min(len(pixels), staff[-1]+1)]
    if not len(band):
        return None
    chroma = np.median((band.max(2)-band.min(2))/255, axis=0)
    baseline = float(np.median(chroma))
    mad = float(np.median(abs(chroma-baseline)))
    threshold = max(_peak_threshold(chroma), baseline+4*max(1/255, 1.4826*mad))
    signal = np.where(chroma > threshold, chroma-baseline, 0)
    regions = [(i, a, b) for i, (a,b) in enumerate(zip(boundaries,boundaries[1:]))]
    if boundaries[0] > .02:
        regions.insert(0, (-1, 0, boundaries[0]))
    if boundaries[-1] < .98:
        regions.append((len(boundaries)-1, boundaries[-1], 1))
    scores = [float(np.mean(signal[round(a*image.width):round(b*image.width)])) for _,a,b in regions]
    if not scores or not np.isfinite(scores).all():
        return None
    ranking = np.argsort(scores)
    best = int(ranking[-1]); runner = scores[ranking[-2]] if len(ranking)>1 else 0
    if scores[best] <= 2/255 or scores[best] < runner*2:
        return None
    return regions[best][0]


def row_slots(row, task):
    """Legacy first-number rows and the new single global bar-order sequence."""
    if isinstance(row.get('slots'), list):
        return row['slots']
    by_number = {b['number']: b['order'] for b in task['bars']}
    return [dict(order=by_number.get(row['first']+i) if row.get('first') is not None else None, skip=False)
            for i in range(len(row['boundaries'])-1)]


def auto_associate(rows, saved, task, crops_dir):
    """Timestamp + moving highlight votes establish visible bar offsets.

    Repeated notation is never deduplicated by its appearance alone. Two views
    compete only when independent time evidence maps them to the same bar ID.
    Unsupported rows receive a provisional sequence, not a claim of certainty.
    """
    metadata = {c['file']: c for r in saved.get('bar_records', []) for c in r.get('candidates', [])}
    metadata.update({c['file']: c for c in saved.get('captures', [])})
    owners = {}
    next_order = 1
    for row in rows:
        row['excluded'] = False
        row['confirmed'] = False
        row['slots'] = []
        votes, evidence, times = [], [], []
        with Image.open(Path(crops_dir)/row['file']) as representative:
            anchor = fingerprint(representative)
            detection = row.get('detection') or suggest_lines(representative, task['instrument']['stringCount'])
            row['detection'] = detection
            for name in dict.fromkeys([row['file'], *row.get('candidates', [])]):
                sample = metadata.get(name, {})
                t = sample.get('t')
                if not isinstance(t, (int, float)):
                    continue
                bar = next((b for b in task['bars'] if b['start'] <= t < b['end']), None)
                if not bar or not (Path(crops_dir)/name).is_file():
                    continue
                times.append(t)
                with Image.open(Path(crops_dir)/name) as image:
                    fp = fingerprint(image)
                    if float(abs(fp-anchor).sum()/max(1, np.maximum(fp, anchor).sum())) > .12:
                        continue  # this candidate crossed a page/scroll change
                    interval = active_interval(image, row['boundaries'], detection.get('staff'))
                if interval is not None:
                    offset = bar['order']-interval
                    votes.append(offset)
                    evidence.append(dict(t=t, bar_id=bar['id'], interval=interval, offset=offset))
        count = len(row['boundaries'])-1
        mode, support = Counter(votes).most_common(1)[0] if votes else (next_order, 0)
        strong = support >= 2 and support/max(1,len(votes)) >= .8 and detection.get('detectedStrings') == task['instrument']['stringCount']
        row['mapping'] = dict(kind='automatic' if strong else 'provisional', support=support,
                              samples=len(votes), evidence=evidence, first_order=mode,
                              sample_start=min(times) if times else None, sample_end=max(times) if times else None,
                              reason='帧时间与高亮位置一致' if strong else '定位证据不足或相互冲突，需一次全局校准')
        row['confirmed'] = strong
        for i in range(count):
            order = mode+i
            valid = 1 <= order <= len(task['bars'])
            slot = dict(order=order if valid else None, skip=not valid, reason='' if valid else '任务范围外')
            if valid and strong and order in owners:
                slot.update(skip=True, reason='相邻画面重复')
            elif valid and strong:
                owners[order] = (row, i)
            row['slots'].append(slot)
        included = [s['order'] for s in row['slots'] if s['order'] and not s['skip']]
        row['first'] = task['bars'][included[0]-1]['number'] if included else None
        next_order = max(next_order, mode+count)
    # A weak extra screenshot needs no user action when both its sampled bars
    # and all proposed intervals already have independent, reliable sources.
    for row in rows:
        if row['confirmed']:
            continue
        sampled = set()
        for name in row.get('candidates', []):
            t = metadata.get(name, {}).get('t')
            if isinstance(t,(int,float)):
                sampled.update(b['order'] for b in task['bars'] if b['start'] <= t < b['end'])
        proposed = {s['order'] for s in row['slots'] if s['order'] is not None}
        if sampled and proposed and (sampled | proposed) <= owners.keys():
            row['excluded'] = True
            row['mapping']['kind'] = 'redundant'
            row['mapping']['reason'] = '此时段与候选区间均已有可靠图片来源，自动略过'
    return rows


def validate_rows(rows, saved, task, crops_dir):
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ValueError("谱面行格式错误")
    allowed = {c["file"] for r in saved.get("bar_records", []) for c in r.get("candidates", [])}
    allowed.update(c["file"] for c in saved.get("captures", []))
    clean = []
    for row in rows:
        if not isinstance(row, dict) or row.get("file") not in allowed:
            raise ValueError("谱面行引用了不属于此任务的图片")
        bounds = row.get("boundaries", [])
        if not isinstance(bounds, list) or not 2 <= len(bounds) <= 1000:
            raise ValueError("每行至少需要首尾两条边界")
        from barlist import number
        bounds = [number(x, "小节线位置", 0, 1) for x in bounds]
        if any(b-a < .002 for a, b in zip(bounds, bounds[1:])):
            raise ValueError("小节线必须从左到右排列且不能重叠")
        top = number(row.get("top", 0), "上边界", 0, 1)
        bottom = number(row.get("bottom", 1), "下边界", 0, 1)
        if bottom-top < .02:
            raise ValueError("谱面行高度太小")
        first = row.get("first")
        if first is not None:
            first = number(first, "首小节号", 1, 100000, True)
        slots = row_slots(row, task)
        if len(slots) != len(bounds)-1:
            raise ValueError('小节关联数与图片区间数不一致')
        slots = [dict(order=number(s['order'], '小节序号', 1, len(task['bars']), True) if s.get('order') is not None else None,
                      skip=s.get('skip') is True, reason=str(s.get('reason',''))[:100]) for s in slots]
        mapping = row.get('mapping') or {'kind':'manual', 'reason':'旧版校对已保留'}
        clean.append(dict(file=row["file"], candidates=[f for f in row.get("candidates", []) if f in allowed], slots=slots,
                          mapping={k:mapping[k] for k in ('kind','reason','support','samples','first_order','sample_start','sample_end') if k in mapping},
                          first=first, boundaries=bounds, top=top, bottom=bottom,
                          confirmed=row.get("confirmed") is True, excluded=row.get("excluded") is True))
    return clean


def row_coverage(rows, task, crops_dir):
    expected = {b["number"] for b in task["bars"]}
    counts, pending, outside, ordering = {}, [], [], []
    previous = None
    for index, row in enumerate(rows):
        if row["excluded"]:
            continue
        slots = [s for s in row_slots(row, task) if not s.get('skip')]
        if not slots:
            continue
        if not row["confirmed"] or not (Path(crops_dir) / row["file"]).is_file():
            pending.append(index + 1)
            continue
        numbers = [task['bars'][s['order']-1]['number'] if s.get('order') else None for s in slots]
        for n in numbers:
            if n is None:
                outside.append(index+1)
                continue
            if previous is not None and n <= previous:
                ordering.append(index+1)
            previous = n
            counts[n] = counts.get(n, 0) + 1
            if n not in expected:
                outside.append(n)
    missing = sorted(expected - counts.keys())
    duplicates = sorted(n for n, count in counts.items() if count > 1)
    return dict(missing=missing, duplicates=duplicates, outside=outside, pending=pending, ordering=ordering,
                complete=bool(rows) and not any((missing, duplicates, outside, pending, ordering)),
                covered=len(expected & counts.keys()), expected=len(expected))


def export_bars(rows, task, crops_dir):
    report = row_coverage(rows, task, crops_dir)
    if not report["complete"]:
        raise ValueError("仍有待确认、缺失、重复、越界或乱序小节，请先修正覆盖情况")
    bars = {b["number"]: b for b in task["bars"]}
    manifest = dict(schema="bili-tab-capture/bar-images", version=1, task=task, bars=[])
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for row in rows:
            if row["excluded"]:
                continue
            with Image.open(Path(crops_dir) / row["file"]) as image:
                w, h = image.size
                for offset, (a, b) in enumerate(zip(row["boundaries"], row["boundaries"][1:])):
                    slot = row_slots(row, task)[offset]
                    if slot.get('skip'):
                        continue
                    bar = task['bars'][slot['order']-1]
                    box = (round(a*w), round(row["top"]*h), round(b*w), round(row["bottom"]*h))
                    if box[2] <= box[0] or box[3] <= box[1]:
                        raise ValueError("小节裁剪尺寸不足一个像素")
                    buf = io.BytesIO()
                    image.crop(box).save(buf, format="PNG")
                    filename = f"bars/{bar['order']:05d}_{bar['id']}.png"
                    archive.writestr(filename, buf.getvalue())
                    manifest["bars"].append(dict(**bar, file=filename, source=row["file"], box=list(box),
                                                 review=row.get('mapping',{}).get('kind','manual')))
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    output.seek(0)
    return output


def layout_cards(rows, task, crops_dir):
    """Bridge reviewed intervals to legacy cards; retain contiguous runs and unique file IDs."""
    import uuid
    report = row_coverage(rows, task, crops_dir)
    if not report['covered'] or any(report[k] for k in ('duplicates', 'outside', 'ordering')):
        raise ValueError('没有可应用的小节，或存在重复、越界、乱序，请先修正')
    batch = Path(crops_dir) / ('review_' + uuid.uuid4().hex)
    batch.mkdir()
    cards = []
    for row in rows:
        if row['excluded'] or not row['confirmed'] or not (Path(crops_dir)/row['file']).is_file():
            continue
        slots = row_slots(row, task)
        i = 0
        while i < len(slots):
            if slots[i].get('skip'):
                i += 1
                continue
            end = i + 1
            while end < len(slots) and not slots[end].get('skip'):
                end += 1
            filename = batch / f'row_{len(cards):04d}.png'
            with Image.open(Path(crops_dir)/row['file']) as image:
                w, h = image.size
                image.save(filename)
            bounds = row['boundaries']
            if round(bounds[end]*w) <= round(bounds[i]*w) or round(row['bottom']*h) <= round(row['top']*h):
                raise ValueError('裁剪尺寸不足一个像素')
            cards.append(dict(file=filename.relative_to(crops_dir).as_posix(), source_file=row['file'], w=w, h=h,
                              t=task['bars'][slots[i]['order']-1]['start'], hidden=False,
                              crop=dict(l=bounds[i], r=bounds[end], t=row['top'], b=row['bottom']),
                              measures=bounds[i+1:end]))
            i = end
    return cards, report
