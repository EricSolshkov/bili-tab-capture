import copy
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import app
import barlist
import bili
from tab_extractor import ExtractionOptions, normalize_bilibili_url, probe_bilibili_metadata


def task(strings=6):
    return dict(schema=barlist.SCHEMA, version=1, taskId="synthetic-test", revision=1,
                video=dict(bvid="BV1iR2iB5Ejk", cid="12345", page=2, title="合成回归测试，非真实视频标注"),
                instrument=dict(stringCount=strings, tuningMidi=None, capo=0),
                bars=[dict(id=f"bar-{i+1}", order=i+1, number=i+17, start=float(i), end=float(i+1),
                           timeSignature=[4, 4], quarterBpm=240) for i in range(4)])


class InlineThread:
    def __init__(self, target, args, **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class BarlistTests(unittest.TestCase):
    def test_real_sample_contract(self):
        sample = json.loads((Path(__file__).parent / "fixtures/BV1iR2iB5Ejk.bars-1-8.barlist.json").read_text(encoding="utf-8"))
        normalized = barlist.normalize_task(sample)
        self.assertEqual(len(normalized["bars"]), 8)
        self.assertEqual(normalized["bars"][0]["start"], 0.177)
        self.assertEqual(normalized["bars"][-1]["end"], 12.977)
        self.assertTrue(all(b["quarterBpm"] == 150 and b["timeSignature"] == [4, 4] for b in normalized["bars"]))

    def test_four_to_seven_line_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for strings in range(4, 8):
                video = root / f"{strings}.mp4"
                writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 20, (400, 200))
                self.assertTrue(writer.isOpened())
                frame = np.full((200, 400, 3), 255, np.uint8)
                for index in range(strings):
                    cv2.line(frame, (10, 20 + index * 20), (390, 20 + index * 20), (0, 0, 0), 2)
                for _ in range(20):
                    writer.write(frame)
                writer.release()
                sample = task(strings)
                sample["bars"] = sample["bars"][:1]
                captures, _ = barlist.capture_bars(video, root / f"crops{strings}", barlist.normalize_task(sample), ExtractionOptions())
                self.assertEqual(len(captures), 1)
                decoded = cv2.imread(str(root / f"crops{strings}" / captures[0]["file"]))
                self.assertEqual(decoded.shape[:2], (200, 400))
                for index in range(strings):
                    self.assertLess(float(decoded[20 + index * 20, 100:300].mean()), 30)

    def test_validation_and_instruments(self):
        for strings in range(4, 8):
            normalized = barlist.normalize_task(task(strings))
            self.assertEqual(normalized["instrument"]["stringCount"], strings)
        for mutate in [
            lambda t: t["instrument"].update(stringCount=3),
            lambda t: t["instrument"].update(tuningMidi=[40]),
            lambda t: t["bars"][1].update(id="bar-1"),
            lambda t: t["bars"][1].update(start=0.5),
            lambda t: t["bars"][1].update(order=3),
            lambda t: t["bars"][1].update(end=float("nan")),
            lambda t: t["video"].update(cid="../bad"),
        ]:
            invalid = task()
            mutate(invalid)
            with self.assertRaises(ValueError):
                barlist.normalize_task(invalid)
        with self.assertRaisesRegex(ValueError, "旧版"):
            barlist.normalize_task({"exportedAt": "today"})

    def test_multipart_identity(self):
        response = {"code": 0, "data": {"title": "测试", "duration": 99, "pages": [
            {"page": 1, "cid": 111, "duration": 8}, {"page": 2, "cid": 12345, "duration": 4}]}}
        url = normalize_bilibili_url("https://www.bilibili.com/video/BV1iR2iB5Ejk?p=2&x=other")
        self.assertTrue(url.endswith("?p=2"))
        with patch.object(bili, "get_json", return_value=response):
            self.assertEqual(bili._get_cid(url, None), 12345)
            self.assertEqual(probe_bilibili_metadata(url)[1], 4)
            with self.assertRaisesRegex(ValueError, "cid"):
                bili.resolve_video_part(url, expected_cid=111)
            with self.assertRaises(ValueError):
                bili.resolve_video_part(url.replace("p=2", "p=3"))

    def test_capture_missing_bars_and_end_to_end_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "fixture.mp4"
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 20, (400, 200))
            self.assertTrue(writer.isOpened())
            # First two bars deliberately identical. Third blank. Fourth outside duration.
            for frame_index in range(60):
                frame = np.full((200, 400, 3), 255, np.uint8)
                if frame_index < 40:
                    for y in range(30, 151, 20):
                        cv2.line(frame, (20, y), (380, y), (0, 0, 0), 1)
                    cv2.putText(frame, "5  7  9", (50, 95), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
                writer.write(frame)
            writer.release()
            normalized = barlist.normalize_task(task())
            captures, records = barlist.capture_bars(video, root / "direct", normalized, ExtractionOptions())
            self.assertEqual([c["bar_number"] for c in captures], [17, 18])
            self.assertEqual(len(records), 4)
            self.assertEqual([r["status"] for r in records], ["captured", "captured", "missing", "missing"])
            self.assertIn("超出", records[3]["reason"])
            self.assertFalse(barlist.coverage(normalized, records)["capture_complete"])

            cache = root / "cache"
            def download(url, output, **kwargs):
                target = Path(output) / "video.mp4"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(video, target)
                return target

            meta = app.build_video_metadata(raw_title="测试", source_url="https://www.bilibili.com/video/BV1iR2iB5Ejk?p=2")
            with patch.multiple(app, CACHE_DIR=cache, UPLOADS_DIR=cache/"uploads", PREVIEWS_DIR=cache/"previews", RUNS_DIR=cache/"runs"), \
                 patch.object(app, "probe_bilibili_metadata", return_value=(meta, 3.0)), \
                 patch.object(bili, "resolve_video_part", return_value=({}, {"cid": 12345})), \
                 patch.object(app, "download_bilibili_video", side_effect=download), \
                 patch.object(app.threading, "Thread", InlineThread):
                app.SOURCES.clear()
                app.JOBS.clear()
                client = app.app.test_client()
                response = client.post("/api/barlist/import", json=task())
                self.assertEqual(response.status_code, 200, response.json)
                source = response.json
                self.assertEqual(source["download"]["status"], "done")
                self.assertEqual(source["barlist"], normalized)
                self.assertIn("cid12345", source["cache_dir"])
                imported_file = client.post("/api/import", data={"file": (io.BytesIO(json.dumps(task()).encode()), "task.barlist.json")})
                self.assertEqual(imported_file.status_code, 200, imported_file.json)
                self.assertEqual(imported_file.json["cache_dir"], source["cache_dir"])
                started = client.post("/api/captures", json={"source_id": source["id"]})
                self.assertEqual(started.status_code, 200, started.json)
                job_id = started.json["job_id"]
                job = client.get(f"/api/jobs/{job_id}").json
                self.assertEqual(job["status"], "done", job)
                self.assertEqual(job["coverage"]["captured"], 2)
                self.assertEqual(job["coverage"]["expected"], 4)
                for capture in job["captures"]:
                    with client.get(capture["url"]) as response:
                        self.assertEqual(response.status_code, 200)
                saved = app.load_state_json(Path(source["cache_dir"]))
                self.assertEqual(saved["barlist"]["instrument"]["stringCount"], 6)
                images = app.restore_images(saved)
                images[0]["hidden"] = True
                client.post("/api/save_state", json={"job_id": job_id, "images": images})
                report = client.get(f"/api/barlist/{source['id']}").json["coverage"]
                self.assertEqual(report["captured"], 1)
                restored = client.post("/api/history/restore", json={"kind": "bilibili", "key": Path(source["cache_dir"]).name})
                self.assertEqual(restored.status_code, 200, restored.json)
                self.assertEqual(restored.json["barlist"], normalized)
                self.assertEqual(restored.json["coverage"]["captured"], 1)
                missing_path = Path(source["cache_dir"]) / "crops" / job["captures"][1]["file"]
                missing_path.unlink()
                missing_report = client.get(f"/api/barlist/{source['id']}").json["coverage"]
                self.assertEqual(missing_report["captured"], 0)
                self.assertIn("文件缺失", missing_report["bars"][1]["reason"])
                changed = copy.deepcopy(task())
                changed["instrument"]["stringCount"] = 7
                new_source = client.post("/api/barlist/import", json=changed).json
                self.assertNotEqual(new_source["cache_dir"], source["cache_dir"])
                self.assertTrue((Path(source["cache_dir"])/"state.json").exists())
                app.SOURCES.clear()
                app.JOBS.clear()


if __name__ == "__main__":
    unittest.main()
