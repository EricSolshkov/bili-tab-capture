const test=require('node:test');
const assert=require('node:assert/strict');
const {reflow,reconcile,report}=require('../static/score_sequence.js');
const bars=[17,19,22,23,25].map((number,i)=>({number,order:i+1,start:i,end:i+1}));
const row=orders=>({boundaries:Array.from({length:orders.length+1},(_,i)=>i/orders.length),slots:orders.map(order=>({order,skip:false})),confirmed:true,excluded:false});
test('delete duplicate once: all subsequent bar IDs and times follow one sequence',()=>{
  const rows=[row([1,2,3]),row([3,4,5])];
  rows[0].slots[2].skip=true;reflow(rows,bars);
  assert.deepEqual(rows[1].slots.map(s=>s.order),[3,4,5]);
  assert.equal(rows[1].first,22);
  assert.equal(report(rows,bars).complete,true);
  assert.equal(bars[rows[1].slots[0].order-1].start,2);
});
test('middle deletion, restore, reorder and a single downstream anchor',()=>{
  const rows=[row([1,2,3]),row([4,5])];
  rows[0].slots[1].skip=true;reflow(rows,bars);
  assert.deepEqual(rows[1].slots.map(s=>s.order),[3,4]);
  assert.deepEqual(report(rows,bars).missing,[25]);
  rows[0].slots[1].skip=false;reflow(rows,bars);
  assert.equal(report(rows,bars).complete,true);
  rows.reverse();reflow(rows,bars);
  assert.equal(rows[1].first,22);
  reflow(rows,bars,1,4);
  assert.equal(rows[1].slots.at(-1).order,null);
  assert.equal(report(rows,bars).complete,false);
});
test('excluded rows and repeated performances keep stable source images',()=>{
  const rows=[{...row([1,2]),file:'same.png'},{...row([3,4,5]),file:'same.png'}];
  const snapshot=JSON.stringify(rows);
  rows[0].excluded=true;reflow(rows,bars);
  assert.deepEqual(rows[1].slots.map(s=>s.order),[1,2,3]);
  const restored=JSON.parse(snapshot);
  assert.equal(report(restored,bars).complete,true);
  assert.equal(restored[0].file,restored[1].file);
});

test('global edits preserve uncertainty until explicit acceptance',()=>{
  const rows=[row([1,2,3]),{...row([4,5]),confirmed:false}];
  reflow(rows,bars);
  assert.deepEqual(report(rows,bars).pending,[2]);
  assert.equal(report(rows,bars).complete,false);
});


test('deleting the final bar preserves earlier identities and deduplicates overlapping rows',()=>{
  const rows=[row([1,2,3]),row([3,4,5])];
  rows[1].slots[2].skip=true;rows[1].slots[2].reason='手动排除';
  reconcile(rows,bars);
  assert.deepEqual(rows.map(r=>r.slots.map(s=>s.order)),[[1,2,3],[3,4,5]]);
  assert.equal(rows[1].slots[0].skip,true);
  assert.equal(report(rows,bars).covered,4);
  assert.deepEqual(report(rows,bars).missing,[25]);
  assert.deepEqual(report(rows,bars).duplicates,[]);
  rows[1].slots[2].skip=false;reconcile(rows,bars);
  assert.equal(report(rows,bars).complete,true);
});

test('removing an owner transfers its identity to its automatic duplicate',()=>{
  const rows=[row([1,2,3]),row([3,4,5])];
  reconcile(rows,bars);
  rows[0].slots[2].skip=true;rows[0].slots[2].reason='手动排除';
  reconcile(rows,bars);
  assert.equal(rows[1].slots[0].skip,false);
  assert.equal(report(rows,bars).complete,true);
  reconcile(rows,bars);
  assert.equal(report(rows,bars).complete,true);
});

test('a missing unique middle bar is not silently replaced by later bars',()=>{
  const rows=[row([1,2,3]),row([3,4,5])];
  rows[0].slots[1].skip=true;rows[0].slots[1].reason='手动排除';
  reconcile(rows,bars);
  assert.deepEqual(report(rows,bars).missing,[19]);
  assert.equal(rows[1].slots[1].order,4);
  assert.equal(bars[rows[1].slots[1].order-1].start,3);
  rows[0].slots[1].skip=false;reconcile(rows,bars);
  assert.equal(report(rows,bars).complete,true);
});
