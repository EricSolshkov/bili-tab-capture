/* Row review is separate from legacy PDF cards; original capture records stay intact. */
(() => {
  const $ = id => document.getElementById(id);
  let rows = [], key = null, sourceId = null, rowMode = true, dirty = false, busy = false;
  let playEnd = null;
  let stringCount = 4;
  let fieldSerial = 0;
  let lastSnapshot = null, undoStack = [], saveTimer = null;
  const sequence = window.ScoreSequence;
  const bars = () => state.barlist.bars;
  function scheduleSave() { clearTimeout(saveTimer); saveTimer = setTimeout(()=>save(), 900); }
  $('scoreStringCount').onchange = () => {
    stringCount = Number($('scoreStringCount').value);
    dirty = true; render(); scheduleSave(); status('弦数已统一修改，编号保持联动。调弦仍需后续确认。');
  };
  const status = text => { $('scoreRowStatus').textContent = text; };
  function button(text, action) {
    const b = document.createElement('button'); b.type = 'button'; b.className = 'secondary-button';
    b.textContent = text; b.onclick = action; return b;
  }
  function field(label, value, change, min, max, step = 1) {
    const wrap = document.createElement('label'); wrap.textContent = label + ' ';
    const input = document.createElement('input'); input.type = 'number'; input.value = value ?? '';
    input.id = `score-row-field-${fieldSerial++}`;
    input.min = min; input.max = max; input.step = step;
    input.oninput = () => change(input.value === '' ? null : Number(input.value));
    wrap.append(input); return wrap;
  }
  function remember() {
    if(lastSnapshot) undoStack.push(lastSnapshot);
    if(undoStack.length>30) undoStack.shift();
  }
  function changed(row, structural=false) {
    remember();
    if(structural) sequence.reconcile(rows,bars());
    else {row.confirmed=true;row.mapping={...row.mapping,kind:'manual',reason:'已手动调整'};}
    dirty=true;status(structural?'已保留时间关联并重新去重；缺失小节会单独提示':'修改已采用，正在自动保存');render();scheduleSave();
  }
  function coverage() {
    const r=sequence.report(rows,bars());
    $('scoreRowCoverage').textContent = `可导出 ${r.covered}/${r.expected} 小节 · 需关注 ${r.pending.length} 行` +
      (r.missing.length ? ` · 缺少 ${r.missing.join('、')}` : '') + (r.duplicates.length ? ` · 重复 ${r.duplicates.join('、')}` : '') +
      (r.outside.length ? ` · 超出列表 ${r.outside.length} 个区间` : '') + (r.ordering.length ? ` · 乱序 ${r.ordering.length}` : '');
    $('exportScoreRows').disabled = busy || !r.complete;
    $('undoScoreRows').disabled = busy || !undoStack.length;
    $('applyScoreRows').disabled = busy || !r.covered || !!(r.duplicates.length+r.outside.length+r.ordering.length);
    $('applyScoreRowsHint').textContent = `将应用 ${r.covered}/${r.expected} 小节。` + (r.complete ? '全部小节已关联。' : `未确认行和缺失小节不会传入排版（需关注 ${r.pending.length} 行，缺少 ${r.missing.join('、') || '无'}）。`) + (r.duplicates.length+r.outside.length+r.ordering.length ? '请先修正重复、越界或乱序。' : '');
  }
  async function replay(number) {
    const bar = state.barlist.bars.find(b => b.number === number);
    if (!bar) { status('该编号不在任务列表中，请先修正首小节号'); return; }
    const video = $('rowVideo'); $('rowPlayback').open = true;
    playEnd = bar.end; video.currentTime = bar.start;
    $('rowOriginalVideo').href = `https://www.bilibili.com/video/${state.barlist.video.bvid}?p=${state.barlist.video.page}&t=${bar.start}`;
    $('rowVideoHint').textContent = `第 ${number} 小节 · ${bar.start.toFixed(3)}–${bar.end.toFixed(3)} 秒；到小节末尾自动暂停。`;
    try { await video.play(); } catch { status('浏览器未能播放缓存视频，可用下方原视频链接回看'); }
    video.scrollIntoView({block:'center', behavior:'smooth'});
  }
  $('rowVideo').ontimeupdate = () => {
    if (playEnd != null && $('rowVideo').currentTime >= playEnd) { $('rowVideo').pause(); playEnd = null; }
  };
  function render() {
    const focused = document.activeElement?.id;
    fieldSerial = 0;
    $('scoreRowList').replaceChildren();
    rows.forEach((row, index) => {
      const slots=sequence.slots(row,bars()), included=slots.filter(s=>!s.skip);
      if ($('scoreRowsPendingOnly').checked && (row.confirmed || row.excluded || !included.length)) return;
      const card = document.createElement('article'); card.className = 'score-row' + (row.excluded ? ' excluded' : '');
      const heading = document.createElement('h3');
      const assigned=included.map(s=>bars()[s.order-1]?.number).filter(n=>n!=null);
      heading.textContent = `谱面行 ${index+1} · ${assigned.length ? `第 ${assigned[0]}–${assigned.at(-1)} 小节` : '无有效小节'} · ${!included.length ? '重复 / 范围外，已略过' : row.confirmed ? (row.mapping?.kind==='automatic'?'自动关联':'已联动') : '需关注'}`;
      card.append(heading);
      const timing=document.createElement('p');timing.className='row-help';
      const m=row.mapping;
      timing.textContent=(m?.sample_start!=null ? `采样 ${m.sample_start.toFixed(3)}–${m.sample_end.toFixed(3)} 秒 · ` : '')+(m?.reason || '已有校对已保留');
      card.append(timing);
      const advanced=document.createElement('details');const advancedTitle=document.createElement('summary');advancedTitle.textContent='高级调整（全局对齐、裁剪、换图）';advanced.append(advancedTitle);
      const controls = document.createElement('div'); controls.className = 'row-controls';
      controls.append(field('从此行对齐到小节', assigned[0], n => { const bar=bars().find(b=>b.number===n); if(!bar)return;remember();sequence.reflow(rows,bars(),index,bar.order);dirty=true;render();scheduleSave(); }, 1, 100000));
      controls.append(field('上裁剪 %', row.top*100, n => { row.top = Math.min(row.bottom-.02, Math.max(0, (n ?? 0)/100)); changed(row); }, 0, 98, .1));
      controls.append(field('下裁剪 %', row.bottom*100, n => { row.bottom = Math.max(row.top+.02, Math.min(1, (n ?? 100)/100)); changed(row); }, 2, 100, .1));
      controls.append(button('重新建议小节线', async () => {
        try {
          const result = await fetchJson(`/api/score_rows/${sourceId}/detect`, {method:'POST', headers:JSON_HEADERS, body:JSON.stringify({...row, stringCount})});
          row.detection = result;
          if (result.lines.length >= 2) { row.boundaries = result.lines; row.slots=result.lines.slice(1).map(()=>({order:null,skip:false})); changed(row,true); } else render();
          status(result.reason + (result.lines.length < 2 ? '；原有标线已保留' : ''));
        } catch (e) { status(e.message); }
      }));
      controls.append(button(row.excluded ? '恢复此行' : '排除此行', () => { row.excluded = !row.excluded; changed(row,true); }));
      controls.append(button('复制为另一段演奏', () => { const copy = structuredClone(row); copy.excluded=false; rows.splice(index+1,0,copy); changed(copy,true); }));
      const trimLeft = button('排除左端小节', () => { included[0].skip=true; included[0].reason='手动排除'; changed(row,true); });
      const trimRight = button('排除右端小节', () => { included.at(-1).skip=true; included.at(-1).reason='手动排除'; changed(row,true); });
      trimLeft.disabled = trimRight.disabled = !included.length;
      controls.append(trimLeft, trimRight);
      if (index > 0) controls.append(button('上移', () => { [rows[index-1], rows[index]] = [row, rows[index-1]]; changed(row,true); }));
      if (index < rows.length-1) controls.append(button('下移', () => { [rows[index+1], rows[index]] = [row, rows[index+1]]; changed(row,true); }));
      advanced.append(controls);card.append(advanced);
      if (row.excluded || !included.length) {
        if(!row.excluded) advanced.append(button('恢复本行所有区间',()=>{slots.forEach(s=>s.skip=false);changed(row,true);}));
        $('scoreRowList').append(card); return;
      }
      const candidates = document.createElement('select'); candidates.setAttribute('aria-label', `谱面行 ${index+1} 候选图`);
      const files = [...new Set([row.file, ...(row.candidates || [])])];
      files.forEach((file, i) => { const o = document.createElement('option'); o.value = file; o.textContent = `候选画面 ${i+1}`; candidates.append(o); });
      candidates.value = row.file;
      candidates.onchange = () => { row.file = candidates.value; changed(row); };
      advanced.append(candidates);
      if (row.detection?.horizontal) {
        const details = document.createElement('details'); const summary = document.createElement('summary');
        summary.textContent = `查看投影统计 · 检测到 ${row.detection.detectedStrings} 根弦线`; details.append(summary);
        for (const [name, axis] of [['横向：每一行像素的线条响应','horizontal'],['竖向：TAB 区域每列的墨迹强度','vertical']]) {
          const label = document.createElement('p'); label.textContent = name; details.append(label);
          const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns,'svg');
          svg.setAttribute('viewBox','0 0 640 100'); svg.setAttribute('role','img'); svg.setAttribute('aria-label',name); svg.classList.add('row-signal');
          svg.setAttribute('preserveAspectRatio','none');
          const points = row.detection[axis], path = document.createElementNS(ns,'polyline');
          path.setAttribute('points', points.map((v,i)=>`${i/(points.length-1)*640},${95-v*90}`).join(' '));
          path.setAttribute('fill','none'); path.setAttribute('stroke','#16746b'); path.setAttribute('stroke-width','1'); svg.append(path);
          const threshold = document.createElementNS(ns,'line'); const y = 95-row.detection[axis+'Threshold']*90;
          for (const [k,v] of Object.entries({x1:0,x2:640,y1:y,y2:y,stroke:'#d85c32','stroke-dasharray':'5 3'})) threshold.setAttribute(k,v);
          svg.append(threshold); details.append(svg);
        }
        card.append(details);
      }
      const viewport = document.createElement('div'); viewport.className = 'row-image-scroll';
      const canvas = document.createElement('div'); canvas.className = 'row-image-canvas';
      const img = document.createElement('img'); img.src = `/api/captures/${state.captureJobId}/${row.file}`; img.alt = `谱面行 ${index+1}`; img.draggable = false;
      img.onerror = () => { row.confirmed = false; row.mappingIssue='image'; status(`谱面行 ${index+1} 图片无法读取，请重新取图`); coverage(); };
      canvas.append(img);
      for (const [top, height] of [[0,row.top],[row.bottom,1-row.bottom]]) {
        const shade = document.createElement('div'); shade.className = 'row-shade'; shade.style.top = `${top*100}%`; shade.style.height = `${height*100}%`; canvas.append(shade);
      }
      for (const [left, width] of [[0,row.boundaries[0]],[row.boundaries.at(-1),1-row.boundaries.at(-1)]]) {
        const shade = document.createElement('div'); shade.className = 'row-shade';
        Object.assign(shade.style, {left:`${left*100}%`, width:`${width*100}%`, right:'auto', top:'0', bottom:'0'}); canvas.append(shade);
      }
      row.boundaries.slice(0,-1).forEach((x, i) => {
        const slot=slots[i], bar=bars()[slot.order-1];
        const label = button(slot.skip ? (bar?`已略过 ${bar.number}`:'范围外') : bar?`第 ${bar.number} 小节 ▶`:'待定位', () => {if(bar)replay(bar.number);});
        label.className = 'row-bar-label'; label.style.left = `${x*100}%`; label.style.width = `${(row.boundaries[i+1]-x)*100}%`;
        label.disabled = !bar; canvas.append(label);
        const toggle=button(slot.skip?'↶':'×',()=>{slot.skip=!slot.skip;slot.reason=slot.skip?'手动排除':'';changed(row,true);});
        toggle.className='row-slot-toggle';toggle.style.left=`${row.boundaries[i+1]*100}%`;
        toggle.setAttribute('aria-label',`谱面行 ${index+1} 区间 ${i+1} ${slot.skip?'恢复':'排除'}`);canvas.append(toggle);
        if(slot.skip){const shade=document.createElement('div');shade.className='row-shade';Object.assign(shade.style,{left:`${x*100}%`,width:`${(row.boundaries[i+1]-x)*100}%`,right:'auto',top:0,bottom:0});canvas.append(shade);}
      });
      row.boundaries.forEach((x, i) => {
        const line = document.createElement('button'); line.type = 'button'; line.className = 'row-boundary';
        line.style.left = `${x*100}%`; line.title = '拖动调整；方向键微调；双击删除内部线';
        line.setAttribute('aria-label', `谱面行 ${index+1} 边界 ${i+1}`);
        function move(value) { row.boundaries[i] = Math.max(i ? row.boundaries[i-1]+.003 : 0, Math.min(i+1 < row.boundaries.length ? row.boundaries[i+1]-.003 : 1, value)); line.style.left = `${row.boundaries[i]*100}%`; row.confirmed = false; dirty = true; }
        line.onpointerdown = e => {
          e.preventDefault(); line.setPointerCapture(e.pointerId);
          let moved = false;
          line.onpointermove = ev => { if (Math.abs(ev.clientX-e.clientX)<2 && !moved) return; moved = true; const rect = canvas.getBoundingClientRect(); move((ev.clientX-rect.left)/rect.width); };
          line.onpointerup = () => { line.onpointermove = null; if (moved) changed(row); };
          line.onpointercancel = () => { line.onpointermove = null; changed(row); };
        };
        line.onkeydown = e => { if (['ArrowLeft','ArrowRight'].includes(e.key)) { e.preventDefault(); move(row.boundaries[i]+(e.key === 'ArrowLeft' ? -.001 : .001)); coverage(); } };
        line.onkeyup = e => { if (['ArrowLeft','ArrowRight'].includes(e.key)) { changed(row); document.querySelector(`[aria-label="谱面行 ${index+1} 边界 ${i+1}"]`)?.focus(); } };
        line.ondblclick = e => { e.stopPropagation(); if (i > 0 && i < row.boundaries.length-1) { row.boundaries.splice(i,1);row.slots[i-1].skip=row.slots[i-1].skip&&row.slots[i].skip;row.slots.splice(i,1); changed(row,true); } };
        canvas.append(line);
      });
      canvas.ondblclick = e => {
        if (e.target !== img) return;
        const rect = canvas.getBoundingClientRect(), x = (e.clientX-rect.left)/rect.width;
        if (x > row.boundaries[0]+.003 && x < row.boundaries.at(-1)-.003 && row.boundaries.every(v => Math.abs(v-x)>.003)) {
          const i=row.boundaries.findIndex(v=>v>x);row.boundaries.splice(i,0,x);row.slots.splice(i,0,{order:null,skip:false});changed(row,true);
        }
      };
      viewport.append(canvas); card.append(viewport);
      const note = document.createElement('p'); note.className = 'row-help';
      note.textContent = row.detection?.staff?.length ?
        (row.detection.detectedStrings !== stringCount ? `检测到 ${row.detection.detectedStrings} 根弦线，与当前 ${stringCount} 弦配置不同；请核对。` : '') + '投影统计建议；请核对首尾边界和小节编号。' :
        (row.detection?.reason || '校对首尾边界和每个区间；修改后需要重新确认。'); card.append(note);
      $('scoreRowList').append(card);
    });
    coverage();
    lastSnapshot=JSON.stringify(rows);
    if (focused?.startsWith('score-row-field-')) $(focused)?.focus({preventScroll:true});
  }
  async function save(exportZip = false) {
    if (busy) { if(!exportZip)scheduleSave(); return; }
    busy = true; coverage();
    const snapshot = JSON.stringify(rows), activeSource = sourceId, savedStrings = stringCount;
    try {
      const res = await fetch(`/api/score_rows/${activeSource}`, {method:'POST', headers:JSON_HEADERS, body:JSON.stringify({rows:JSON.parse(snapshot), stringCount, export:exportZip})});
      if (!res.ok) { const err = await res.json(); throw new Error(err.error || '保存失败'); }
      if (exportZip) {
        const url = URL.createObjectURL(await res.blob()); const a = document.createElement('a'); a.href = url; a.download = `${state.barlist.taskId}-bars.zip`; a.click(); setTimeout(()=>URL.revokeObjectURL(url),10000);
        status('已生成单小节图片和 manifest.json，包含原小节 ID、时间、弦数及裁剪位置。');
      } else if (activeSource === sourceId && savedStrings === stringCount && snapshot === JSON.stringify(rows)) { dirty = false; status('全局关联已保存，可从历史记录恢复'); }
    } catch (e) { status(e.message); }
    finally { busy = false; if (state.barlist) coverage(); }
  }
  $('applyScoreRows').onclick = async () => {
    if(busy)return;
    busy=true;clearTimeout(saveTimer);coverage();
    $('applyScoreRowsHint').textContent='正在应用裁剪和小节顺序…';
    const activeSource=sourceId, snapshot=JSON.stringify(rows);
    try {
      const data=await fetchJson(`/api/score_rows/${activeSource}`,{method:'POST',headers:JSON_HEADERS,body:JSON.stringify({action:'apply',rows:JSON.parse(snapshot),stringCount})});
      if(activeSource!==sourceId)return;
      if(snapshot!==JSON.stringify(rows)){status('校对内容已改变，请重新应用最新结果');return;}
      dirty=false;
      restoreCards(data.images);
      persistSnapshot=null;
      persistStateNow();
      await refreshBarlist();
      rowMode=false;window.updateScoreRows();
      setStatus(els.adjustStatus,`已应用 ${data.coverage.covered}/${data.coverage.expected} 小节的裁剪与顺序，可继续调整或开始排版。`);
      els.adjustPanel.scrollIntoView({block:'start'});
    } catch(e) {status(e.message);$('applyScoreRowsHint').textContent=e.message;}
    finally {busy=false;coverage();}
  };
  $('saveScoreRows').onclick = () => save();
  $('undoScoreRows').onclick=()=>{if(!undoStack.length)return;rows=JSON.parse(undoStack.pop());dirty=true;render();scheduleSave();status('已撤销，后续编号与时间已恢复');};
  $('acceptInferredRows').onclick=()=>{remember();sequence.reconcile(rows,bars());rows.forEach(row=>{if(row.mappingIssue!=='image')row.confirmed=true;});dirty=true;render();scheduleSave();status('已采用当前时间关联，并按小节身份去重');};
  $('autoAssociateRows').onclick=async()=>{
    if(busy)return;busy=true;clearTimeout(saveTimer);coverage();status('正在使用帧时间和高亮位置自动关联…');
    try{const data=await fetchJson(`/api/score_rows/${sourceId}`,{method:'POST',headers:JSON_HEADERS,body:JSON.stringify({action:'auto',stringCount})});remember();rows=data.rows;dirty=false;render();status('已自动关联并略过重复区间；只需关注不确定位置');}
    catch(e){status(e.message);}finally{busy=false;coverage();}
  };
  $('scoreRowsPendingOnly').onchange = render;
  $('exportScoreRows').onclick = () => save(true);
  $('toggleRowMode').onclick = () => { rowMode = !rowMode; window.updateScoreRows(); };
  window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
  window.updateScoreRows = async () => {
    const active = !!state.barlist && !!state.captureJobId && state.stage === 3;
    $('rowModeSwitch').classList.toggle('hidden', !active);
    $('rowWorkspace').classList.toggle('hidden', !active || !rowMode);
    $('adjustPanel').classList.toggle('row-mode', active && rowMode);
    $('toggleRowMode').textContent = rowMode ? '切换到逐图调整 / PDF 排版' : '返回整行谱面校对';
    const nextKey = `${state.sourceId}/${state.captureJobId}`;
    if (!active || nextKey === key) return;
    key = nextKey; sourceId = state.sourceId; dirty = false; rows = [];lastSnapshot=null;undoStack=[];clearTimeout(saveTimer);status('正在按帧时间自动关联小节…');
    $('rowVideo').src = `/api/source_video/${sourceId}`;
    try {
      const data = await fetchJson(`/api/score_rows/${sourceId}`);
      if (key !== nextKey) return;
      rows = data.rows; stringCount = data.stringCount; $('scoreStringCount').value = stringCount; render(); status('自动关联完成；重复区间已略过，无需逐行填写编号。缺少定位证据的行会单独提示。');
    } catch (e) { key = null; status(e.message); }
  };
  window.updateScoreRows();
})();
