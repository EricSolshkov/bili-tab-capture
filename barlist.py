"""Versioned barline handoff and ordered, non-deduplicating video capture.

Capture coverage is not proof that the pictured notation matches a bar. That
separate review state is deliberately retained for the future tab2gp pipeline.
"""
import hashlib
import json
import math
import re
from pathlib import Path

SCHEMA = "bili-tab-capture/barlist"
MAX_BARS = 5000
MAX_BYTES = 2 * 1024 * 1024


def number(value, name, minimum=0, maximum=86400, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是数字")
    if not minimum <= value <= maximum or not math.isfinite(value):
        raise ValueError(f"{name} 超出有效范围")
    if integer and value != int(value):
        raise ValueError(f"{name} 必须是整数")
    return int(value) if integer else float(value)


def normalize_task(raw):
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA or raw.get("version") != 1:
        raise ValueError("需要 v1 barlist 任务文件；旧版全部配置备份请在 barline 中重新导出转谱任务")
    if len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
        raise ValueError("barlist 文件超过 2 MiB")
    video = raw.get("video")
    if not isinstance(video, dict) or not re.fullmatch(r"BV[0-9A-Za-z]{10}", str(video.get("bvid", ""))):
        raise ValueError("缺少有效的 video.bvid")
    cid = str(video.get("cid", ""))
    if not re.fullmatch(r"[1-9][0-9]{0,19}", cid):
        raise ValueError("缺少有效的 video.cid（用于区分分P）")
    page = number(video.get("page", 1), "video.page", 1, 10000, True)
    instrument = raw.get("instrument") or {}
    if not isinstance(instrument, dict):
        raise ValueError("instrument 格式错误")
    strings = number(instrument.get("stringCount", 6), "弦数", 4, 7, True)
    tuning = instrument.get("tuningMidi")
    if tuning is not None:
        if not isinstance(tuning, list) or len(tuning) != strings:
            raise ValueError("调弦数组长度必须等于弦数（从高音弦到低音弦）")
        tuning = [number(n, "调弦音高", 0, 127, True) for n in tuning]
    bars = raw.get("bars")
    if not isinstance(bars, list) or not 1 <= len(bars) <= MAX_BARS:
        raise ValueError(f"需要 1–{MAX_BARS} 个小节")
    clean, ids, previous_end, previous_number = [], set(), -1.0, 0
    for index, bar in enumerate(bars):
        if not isinstance(bar, dict):
            raise ValueError("小节格式错误")
        bar_id = bar.get("id")
        if not isinstance(bar_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", bar_id) or bar_id in ids:
            raise ValueError("小节 ID 必须唯一，且只包含字母、数字、下划线或连字符")
        ids.add(bar_id)
        order = number(bar.get("order"), "小节顺序", 1, MAX_BARS, True)
        if order != index + 1:
            raise ValueError("bars 必须按 order 从 1 连续排列；不会自动排序掩盖错误")
        bar_number = number(bar.get("number"), "演奏小节号", 1, 100000, True)
        if bar_number <= previous_number:
            raise ValueError("演奏小节号必须递增；重复演奏也使用新的演奏小节号")
        start = number(bar.get("start"), "小节开始时间")
        end = number(bar.get("end"), "小节结束时间")
        if end <= start or start < previous_end - 1e-6:
            raise ValueError("小节时间必须递增且不能重叠")
        ts = bar.get("timeSignature")
        if not isinstance(ts, list) or len(ts) != 2:
            raise ValueError("小节缺少拍号 [分子, 分母]")
        numerator = number(ts[0], "拍号分子", 1, 32, True)
        denominator = number(ts[1], "拍号分母", 1, 64, True)
        if denominator not in (1, 2, 4, 8, 16, 32, 64):
            raise ValueError("拍号分母必须是 2 的幂")
        bpm = number(bar.get("quarterBpm"), "四分音符 BPM", 1, 1000)
        clean.append(dict(id=bar_id, order=order, number=bar_number, start=start, end=end,
                          timeSignature=[numerator, denominator], quarterBpm=bpm))
        previous_end, previous_number = end, bar_number
    task_id = raw.get("taskId")
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", task_id):
        raise ValueError("缺少有效 taskId")
    result = dict(schema=SCHEMA, version=1, taskId=task_id,
                revision=number(raw.get("revision", 1), "revision", 1, 10**15, True),
                video=dict(bvid=video["bvid"], cid=cid, page=page, title=str(video.get("title", ""))[:500]),
                instrument=dict(stringCount=strings, tuningMidi=tuning,
                                capo=number(instrument.get("capo", 0), "变调夹", 0, 24, True)),
                bars=clean)
    if "instrument" not in raw:
        result.pop("instrument")
    return result


def task_digest(task):
    return hashlib.sha256(json.dumps(task, sort_keys=True).encode()).hexdigest()[:16]


def coverage(task, records, images=None, crops_dir=None):
    """Expected bars never disappear, even if every decode fails or a card is hidden."""
    active = None if images is None or any(i.get("source_file") for i in images) else {i.get("file") for i in images if not i.get("hidden")}
    by_id = {}
    for record in records:
        by_id.setdefault(record.get("bar_id"), []).append(record)
    rows = []
    for bar in task["bars"]:
        matches = by_id.get(bar["id"], [])
        record = matches[0] if matches else {}
        status = record.get("status", "missing")
        if len(matches) > 1:
            status = "conflict"
        elif status == "captured" and crops_dir is not None and not (Path(crops_dir) / str(record.get("file", ""))).is_file():
            status = "missing"
            record = {**record, "reason": "候选图片文件缺失，请重新取图"}
        elif status == "captured" and active is not None and record.get("file") not in active:
            status = "excluded"
        rows.append(dict(bar_id=bar["id"], number=bar["number"], order=bar["order"],
                         start=bar["start"], end=bar["end"], status=status,
                         file=record.get("file"), reason=record.get("reason"),
                         notation_verified=False))
    captured = sum(r["status"] == "captured" for r in rows)
    return dict(expected=len(rows), captured=captured, unresolved=len(rows)-captured,
                capture_complete=captured == len(rows), notation_complete=False, bars=rows)


def capture_bars(video_path, crops_dir, task, options, progress=None):
    import cv2
    from PIL import Image
    from tab_extractor import crop_frame_rgb

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        raise RuntimeError("无法打开视频")
    crops_dir = Path(crops_dir)
    crops_dir.mkdir(parents=True, exist_ok=True)
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    duration = frame_count / fps if fps > 0 else None
    captures, records = [], []
    try:
        for bar in task["bars"]:
            record = dict(bar_id=bar["id"], number=bar["number"], order=bar["order"],
                          status="missing", reason="没有可读取的清晰候选帧", candidates=[])
            records.append(record)
            if duration is not None and bar["end"] > duration + 1e-3:
                record["reason"] = "小节范围超出视频时长；没有截短或移动后续小节"
            else:
                best = None
                for candidate_index, fraction in enumerate((0.2, 0.5, 0.8), 1):
                    target = bar["start"] + (bar["end"] - bar["start"]) * fraction
                    cap.set(cv2.CAP_PROP_POS_MSEC, target * 1000)
                    ok, frame = cap.read()
                    if not ok:
                        continue
                    actual = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
                    # Reject a seek landing outside the requested bar, rather than silently misassigning it.
                    if not math.isfinite(actual) or not bar["start"] <= actual < bar["end"]:
                        continue
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    crop = crop_frame_rgb(rgb, options.crop_y_start_ratio, options.crop_y_end_ratio,
                                          options.crop_x_start_ratio, options.crop_x_end_ratio)
                    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
                    if float(gray.std()) < 3:
                        continue
                    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                    name = f"bar_{bar['order']:05d}_c{candidate_index}.png"
                    Image.fromarray(crop).save(crops_dir / name)
                    item = dict(file=name, t=actual, requested_t=target, sharpness=sharpness,
                                w=crop.shape[1], h=crop.shape[0], bar_id=bar["id"],
                                bar_number=bar["number"], bar_order=bar["order"])
                    record["candidates"].append(item)
                    if best is None or sharpness > best["sharpness"]:
                        best = item
                if best:
                    captures.append(best)
                    record.update(status="captured", file=best["file"], reason=None)
            if progress:
                progress(len(records), len(task["bars"]), len(captures))
    finally:
        cap.release()
    return captures, records
