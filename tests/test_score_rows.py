import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

import score_rows


def staff_image(strings, invert=False, faint=False):
    im = Image.new('RGB', (640, 160), '#fafafa')
    d = ImageDraw.Draw(im)
    ys = [35 + i*12 for i in range(strings)]
    for y in ys:
        d.line((15, y, 625, y), fill=(205,)*3, width=1)
    for x in [25, 200, 400, 610]:
        d.line((x, ys[0], x, ys[-1]), fill=(160 if faint else 40,)*3, width=2)
        d.point((x, ys[0]+17), fill='white')  # compression dropout
    # Digits and stems must not turn into full-height barlines.
    for x in [70, 280, 490]:
        d.text((x, ys[1]-5), '12', fill='black')
        d.line((x+20, ys[0]-15, x+20, ys[1]+4), fill='black', width=2)
    return ImageOps.invert(im) if invert else im


class ScoreRowTests(unittest.TestCase):
    def test_workspace_api_save_restore_export_and_reject(self):
        import app
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'crops').mkdir(); staff_image(5).save(root/'crops'/'row.png')
            task = dict(taskId='example',instrument=dict(stringCount=4,tuningMidi=[64,59,55,50]),bars=[dict(id='b1',number=1,order=1,start=0,end=1)])
            saved = dict(barlist=task,captures=[dict(file='row.png')])
            app.save_state_json(root,saved)
            source = dict(id='row-test',cache_dir=str(root),barlist=task)
            with patch.dict(app.SOURCES, {'row-test':source}), patch.dict(app.JOBS, {'row-job':dict(cache_dir=str(root))}):
                client = app.app.test_client(); url='/api/score_rows/row-test'
                self.assertEqual(client.get(url).status_code,200)
                row=dict(file='row.png',first=1,boundaries=[0,1],confirmed=True)
                payload=dict(rows=[row],stringCount=5)
                self.assertEqual(client.post(url,json=payload).status_code,200)
                # Legacy card edits must not overwrite saved row calibration.
                self.assertEqual(client.post('/api/save_state',json=dict(job_id='row-job',images=[])).status_code,200)
                restored=client.get(url).json
                self.assertEqual(restored['stringCount'],5)
                self.assertTrue(restored['coverage']['complete'])
                source['barlist'] = {k: v for k, v in task.items() if k != 'instrument'}
                self.assertEqual(client.get(url).status_code, 200)
                self.assertEqual(client.post(url, json=payload).status_code, 200)
                with client.post(url,json={**payload,'export':True}) as response:
                    self.assertEqual(response.status_code,200)
                    with zipfile.ZipFile(io.BytesIO(response.data)) as z:
                        instrument=json.loads(z.read('manifest.json'))['task']['instrument']
                        self.assertEqual(instrument,dict(stringCount=5,tuningMidi=None))
                self.assertEqual(client.post(url,json={**payload,'stringCount':8}).status_code,400)
                self.assertEqual(client.post(url,json={**payload,'rows':[{**row,'file':'../state.json'}]}).status_code,400)
                (root/'crops'/'row.png').unlink()
                self.assertFalse(client.get(url).json['coverage']['complete'])
                self.assertEqual(client.post(url,json={**payload,'export':True}).status_code,400)

    def test_apply_review_to_legacy_pdf_and_restore(self):
        import app
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); crops=root/'crops';crops.mkdir()
            staff_image(5).save(crops/'row.png')
            task=dict(instrument=dict(stringCount=5),bars=[dict(number=i,order=i,start=i*1.6) for i in range(1,6)])
            saved=dict(captures=[dict(file='row.png')],barlist=task,layout=dict(title='preserved'))
            app.save_state_json(root,saved)
            rows=[dict(file='row.png',boundaries=[0,.25,.5,.75,1],top=.1,bottom=.9,confirmed=True,
                       slots=[dict(order=i,skip=(i==2)) for i in range(1,5)]),
                  dict(file='row.png',boundaries=[0,1],first=5,confirmed=False)]
            with patch.dict(app.SOURCES,{'bridge':dict(cache_dir=str(root),barlist=task)}), patch.dict(app.JOBS,{'bridge':dict(crops_dir=str(crops))}), patch.object(app,'RUNS_DIR',root/'runs'):
                client=app.app.test_client()
                result=client.post('/api/score_rows/bridge',json=dict(action='apply',rows=rows))
                self.assertEqual(result.status_code,200,result.json)
                cards=result.json['images']
                self.assertEqual(len(cards),2)
                self.assertEqual(cards[0]['source_file'],'row.png')
                self.assertEqual(cards[0]['crop'],dict(l=0,r=.25,t=.1,b=.9))
                self.assertEqual(cards[1]['measures'],[.75])
                self.assertEqual(cards[1]['t'],task['bars'][2]['start'])
                self.assertEqual(len(set(c['file'] for c in cards)),2)
                restored=app.load_state_json(root)
                self.assertEqual(restored['images'],cards)
                self.assertEqual(restored['captures'],saved['captures'])
                self.assertEqual(restored['layout'],saved['layout'])
                self.assertEqual(result.json['coverage']['missing'],[2,5])
                payload=dict(images=cards)
                final,count=app.prepare_final_strips('bridge',payload,app._parse_layout_payload(payload))
                self.assertEqual(count,3)
                for image in final.glob('*.png'):
                    with Image.open(image) as im:self.assertEqual(im.size,(160,128))
                app.build_pdf_from_images(final,root/'test.pdf')
                self.assertTrue((root/'test.pdf').read_bytes().startswith(b'%PDF'))
                duplicate=[rows[0],rows[0]]
                self.assertEqual(client.post('/api/score_rows/bridge',json=dict(action='apply',rows=duplicate)).status_code,400)

    def test_projection_peaks_four_to_seven_strings(self):
        for count in range(4, 8):
            for inverted, faint in [(False, False), (False, True), (True, False)]:
                with self.subTest(strings=count, inverted=inverted, faint=faint):
                    result = score_rows.suggest_lines(staff_image(count, inverted, faint), count)
                    actual = [round(x*640) for x in result['lines']]
                    self.assertEqual(len(actual), 4, actual)
                    for found, expected in zip(actual, [25, 200, 400, 610]):
                        self.assertLessEqual(abs(found-expected), 2)
                    self.assertEqual(result['detectedStrings'], count)

    def test_no_staff_no_blind_fallback(self):
        im = Image.new('RGB', (640, 120), 'white')
        ImageDraw.Draw(im).line((100, 0, 100, 119), fill='black', width=3)
        self.assertEqual(score_rows.suggest_lines(im)['lines'], [])
        staff = Image.new('RGB', (640, 120), 'white')
        for y in [20,35,50,65]: ImageDraw.Draw(staff).line((5,y,635,y),fill='gray')
        result = score_rows.suggest_lines(staff,4)
        self.assertEqual(result['lines'], [])
        json.dumps(result, allow_nan=False)

    def test_adjacent_grouping_preserves_repeat_occurrences(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            a = staff_image(6)
            b = a.copy(); ImageDraw.Draw(b).rectangle((60, 40, 180, 70), fill='black')
            for name, image in [('a.png',a),('b.png',a),('c.png',b),('d.png',a)]: image.save(root/name)
            saved = dict(captures=[dict(file=f'{c}.png') for c in 'abcd'])
            rows = score_rows.propose_rows(saved, root, 6)
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]['candidates'], ['a.png','b.png'])
            self.assertEqual(rows[2]['file'], 'd.png')

    def test_review_validation_and_zip_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); staff_image(5).save(root/'row.png')
            task = dict(taskId='example',instrument=dict(stringCount=5),bars=[dict(id=f'b{i}',number=i,order=i,start=i,end=i+1) for i in range(1,5)])
            saved = dict(captures=[dict(file='row.png')])
            raw = [dict(file='row.png',first=1,boundaries=[0,.25,.5,.75,1],confirmed=True)]
            rows = score_rows.validate_rows(raw,saved,task,root)
            self.assertTrue(score_rows.row_coverage(rows,task,root)['complete'])
            with zipfile.ZipFile(score_rows.export_bars(rows,task,root)) as z:
                manifest = json.loads(z.read('manifest.json'))
                self.assertEqual([b['id'] for b in manifest['bars']], ['b1','b2','b3','b4'])
                self.assertEqual(manifest['task']['instrument']['stringCount'],5)
                with Image.open(io.BytesIO(z.read('bars/00001_b1.png'))) as image:
                    self.assertEqual(image.size, (160,160))
            rows[0]['confirmed'] = False
            with self.assertRaises(ValueError): score_rows.export_bars(rows,task,root)
            rows[0]['confirmed'] = True
            duplicate = rows + rows
            self.assertEqual(score_rows.row_coverage(duplicate,task,root)['duplicates'], [1,2,3,4])
            rows[0]['slots'] = [dict(order=o,skip=False) for o in [2,3,4,None]]
            report = score_rows.row_coverage(rows,task,root)
            self.assertEqual(report['outside'], [1]); self.assertEqual(report['missing'], [1])
            with self.assertRaises(ValueError): score_rows.validate_rows([{**raw[0],'file':'../state.json'}],saved,task,root)
            with self.assertRaises(ValueError): score_rows.validate_rows([{**raw[0],'boundaries':[.5,.2]}],saved,task,root)

    def test_timestamp_highlight_mapping_and_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            boundaries=[25/640,200/640,400/640,610/640]
            captures=[]
            for name,order,interval in [('a0',1,0),('a1',2,1),('b0',2,0),('b1',3,1)]:
                pixels=np.asarray(staff_image(5)).copy()
                a,b=round(boundaries[interval]*640),round(boundaries[interval+1]*640)
                pixels[:,a:b,2]=np.maximum(pixels[:,a:b,2].astype(int)-70,0)
                Image.fromarray(pixels).save(root/f'{name}.png')
                captures.append(dict(file=f'{name}.png',t=order-.5))
            task=dict(instrument=dict(stringCount=5),bars=[dict(id=f'b{i}',number=i,order=i,start=i-1,end=i) for i in range(1,5)])
            saved=dict(captures=captures)
            rows=[dict(file=f'{prefix}0.png',candidates=[f'{prefix}0.png',f'{prefix}1.png'],boundaries=boundaries,top=0,bottom=1) for prefix in ['a','b']]
            mapped=score_rows.auto_associate(rows,saved,task,root)
            self.assertEqual([r['mapping']['first_order'] for r in mapped],[1,2])
            self.assertTrue(all(r['confirmed'] for r in mapped))
            self.assertEqual([s['skip'] for s in mapped[1]['slots']],[True,True,False])
            self.assertTrue(score_rows.row_coverage(mapped,task,root)['complete'])
            with zipfile.ZipFile(score_rows.export_bars(mapped,task,root)) as z:
                manifest=json.loads(z.read('manifest.json'))
                self.assertEqual([b['id'] for b in manifest['bars']],['b1','b2','b3','b4'])
                self.assertEqual([b['review'] for b in manifest['bars']],['automatic']*4)
            # Legacy instrument hints and new timing-only tasks must locate/deduplicate identically.
            for variant in [dict(task, instrument=dict(stringCount=6)), {'bars': task['bars']}]:
                associated = score_rows.auto_associate(rows, saved, variant, root)
                self.assertTrue(all(r['confirmed'] for r in associated))
                self.assertEqual([s['skip'] for s in associated[1]['slots']], [True, True, False])
                self.assertTrue(score_rows.row_coverage(associated, variant, root)['complete'])
            # Monochrome images alone cannot prove where the active bar is.
            staff_image(5).save(root/'a0.png');staff_image(5).save(root/'a1.png')
            unknown=score_rows.auto_associate([rows[0]],saved,task,root)[0]
            self.assertFalse(unknown['confirmed'])
            self.assertEqual(unknown['mapping']['kind'],'provisional')


if __name__ == '__main__': unittest.main()
