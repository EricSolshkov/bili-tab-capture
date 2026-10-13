/* One global sequence for labels, playback, coverage and exports. Also tested in Node. */
(function(root) {
  function slots(row, bars) {
    if (Array.isArray(row.slots)) return row.slots;
    row.slots = row.boundaries.slice(1).map((_, i) => ({order: bars.find(b=>b.number===row.first+i)?.order ?? null, skip:false}));
    return row.slots;
  }
  function reflow(rows, bars, startIndex=0, firstOrder=1) {
    let next = firstOrder;
    for (let index=startIndex; index<rows.length; index++) {
      const row=rows[index];
      if (row.excluded) continue;
      for (const slot of slots(row,bars)) {
        if (slot.skip) continue;
        slot.order = next <= bars.length ? next : null;
        next++;
      }
      row.first = bars[(row.slots.find(s=>!s.skip)?.order ?? 0)-1]?.number ?? null;
      row.mapping = {...row.mapping, kind:'linked', reason:'编辑后按全局演奏顺序联动'};
      // A user sequence edit does not certify a missing image or uncertain geometry.
      if (row.mappingIssue === 'image') row.confirmed = false;
    }
    return next-1;
  }
  function reconcile(rows, bars) {
    // Timeline identities, not visible rectangle counts, determine the sequence.
    // Automatic duplicates can take over when their previous owner is removed.
    const eligible = slot => !slot.skip || slot.reason === '相邻画面重复';
    const owners=new Set();
    for(const row of rows) {
      if(row.excluded) continue;
      for(const slot of slots(row,bars)) {
        if(!eligible(slot) || slot.order==null) continue;
        const original=slot.order;
        slot.skip=owners.has(original);
        slot.reason=slot.skip?'相邻画面重复':'';
        owners.add(original);
      }
      row.first=bars[(row.slots.find(s=>!s.skip)?.order??0)-1]?.number??null;
    }
  }
  function report(rows,bars) {
    const seen=new Map(), pending=[], outside=[], ordering=[];
    let previous=0;
    rows.forEach((row,index)=>{
      if (row.excluded) return;
      const active=slots(row,bars).filter(s=>!s.skip);
      if (!active.length) return;
      if (!row.confirmed) {pending.push(index+1);return;}
      active.forEach(s=>{
        const bar=bars[s.order-1];
        if(!bar){outside.push(index+1);return;}
        if(s.order<=previous) ordering.push(index+1);
        previous=s.order;
        seen.set(bar.number,(seen.get(bar.number)||0)+1);
      });
    });
    const missing=bars.filter(b=>!seen.has(b.number)).map(b=>b.number);
    const duplicates=[...seen].filter(([,n])=>n>1).map(([n])=>n);
    return {covered:seen.size, expected:bars.length,pending,outside,ordering,missing,duplicates,
      complete:!pending.length&&!outside.length&&!ordering.length&&!missing.length&&!duplicates.length};
  }
  const api={slots,reflow,reconcile,report};
  if(typeof module!=='undefined') module.exports=api;
  else root.ScoreSequence=api;
})(typeof window==='undefined'?{}:window);
