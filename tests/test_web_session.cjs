// Browser logic check with stubbed Vue/network; no running server or live model.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'web_chat.html'), 'utf8')
  .match(/<script>\s*([\s\S]*?)<\/script>/)[1];
const storage = new Map();
let counter = 0;
const urls = [];
const requests = [];
function mount(messages, historyFetch) {
  let state;
  const context = {
    URLSearchParams,
    Vue: {ref: value => ({value}), reactive: x => x, nextTick: async () => {},
      createApp: app => ({mount() {state = app.setup();}})},
    localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
    crypto: {randomUUID: () => `id-${++counter}`},
    EventSource: class {
      constructor(url) {urls.push(url); requests.push(this);}
      close() {this.closed = true;}
    },
    fetch: async url => historyFetch && url.startsWith('/investigations') ? historyFetch(url) : ({ok: true, json: async () => url === '/health'
      ? {llm_backend: 'fake', fast_llm: 'fast', quality_llm: 'quality'} : {messages}}),
  };
  vm.runInNewContext(source, context);
  return state;
}
(async () => {
  let state = mount([{role: 'user', content: '제 이름은 현민입니다'}, {role: 'assistant', content: '안녕하세요'}]);
  state.input.value = 'blocked while restoring';
  state.onSubmit();
  assert.equal(urls.length, 0);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(state.log.value[0].text, '제 이름은 현민입니다');
  const initial = storage.get('ai-mini-project.thread-id');
  state.input.value = 'payments-service 확인해줘';
  state.onSubmit();
  state.onSubmit();
  assert.equal(urls.length, 1);
  assert.ok(urls[0].includes(encodeURIComponent(initial)));
  assert.equal(state.sending.value, true);
  state.newConversation();
  assert.equal(storage.get('ai-mini-project.thread-id'), initial);
  requests[0].onmessage({data: JSON.stringify({type: 'RunbookSelection', selections: [
    {system_name: 'payments-service', incident_types: ['cpu-spike'], status: 'needs_manual'},
    {system_name: 'web-01', incident_types: [], status: 'normal'},
    {system_name: 'auth-service', incident_types: [], status: 'insufficient'}]})});
  requests[0].onmessage({data: JSON.stringify({type: 'TaskCompleted', source: 'runbook', target: 'cpu-spike', related_systems: ['payments-service']})});
  requests[0].onmessage({data: JSON.stringify({type: 'TaskFailed', target: 'memory-leak', related_systems: ['cache-service']})});
  requests[0].onmessage({data: JSON.stringify({type: 'ReplanCheck', is_sufficient: false, needs_more_research: false, limitations: ['매뉴얼 미확인']})});
  const steps = state.log.value.find(row => row.kind === 'steps').steps.join(' / ');
  assert.ok(steps.includes('정상·문서 불필요') && steps.includes('자료 부족'));
  assert.ok(steps.includes('cpu-spike (payments-service)'));
  assert.ok(steps.includes('매뉴얼 조회 실패: memory-leak (cache-service)'));
  assert.ok(steps.includes('확인 한계: 매뉴얼 미확인') && !steps.includes('undefined'));
  requests[0].onmessage({data: JSON.stringify({type: 'FinalAnswer', reply: '확인했습니다'})});
  requests[0].onmessage({data: '[DONE]'});
  assert.equal(state.sending.value, false);
  assert.equal(requests[0].closed, true);
  assert.equal(state.log.value.find(row=>row.kind==='steps').done,true);
  state.input.value = 'cache-service 진단';
  state.onSubmit();
  requests[1].onmessage({data: JSON.stringify({type: 'AnswerToken', attempt: 1, delta: '검증하지 않은 초안'})});
  requests[1].onmessage({data: JSON.stringify({type: 'ProcessingError', message: '시간 제한 안내'})});
  requests[1].onmessage({data: '[DONE]'});
  assert.equal(state.sending.value, false);
  assert.ok(state.log.value.some(row => row.text && row.text.includes('시간 제한 안내')));
  assert.ok(!state.log.value.some(row => row.text === '검증하지 않은 초안'));
  state.input.value = '다시 질문';state.onSubmit();
  requests[2].onerror();
  assert.equal(state.sending.value, false);assert.equal(requests[2].closed, true);
  state.input.value = '재입력 가능';state.onSubmit();
  assert.equal(requests.length, 4);
  requests[3].onmessage({data: '[DONE]'});
  state = mount([{role: 'assistant', content: '확인했습니다'}]);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(storage.get('ai-mini-project.thread-id'), initial);
  assert.equal(state.log.value[0].text, '확인했습니다');
  state.newConversation();
  assert.notEqual(storage.get('ai-mini-project.thread-id'), initial);
  assert.equal(state.log.value.length, 0);
  state = mount([]);await new Promise(resolve => setImmediate(resolve));
  state.input.value = 'payments-service 진단';state.onSubmit();
  const reportRequest = requests.at(-1);
  const emit = event => reportRequest.onmessage({data: JSON.stringify(event)});
  emit({type:'AnswerToken',attempt:1,delta:'미확정 가설',validation_status:'pending'});
  const rejected = state.log.value.at(-1);
  assert.equal(rejected.validationStatus,'pending');
  assert.equal(state.validationLabel(rejected.validationStatus),'검증 전 초안');
  emit({type:'Validation',attempt:1,status:'rejected',issues:['CPU 기대값 91'],verdict:'불합격'});
  assert.equal(rejected.validationStatus,'rejected');assert.deepEqual(Array.from(rejected.issues),['CPU 기대값 91']);
  emit({type:'AnswerToken',attempt:2,delta:'고친 가설',validation_status:'pending'});
  emit({type:'Validation',attempt:2,status:'passed',issues:[],verdict:'합격'});
  emit({type:'FinalAnswer',reply:'1) 관측값\n2) 분석\n3) 권장 조치\n4) 미확인 사항\n5) 출처',validation_status:'passed',comparison_text:'CPU: 90% → 70% (-20%p)',report:{sources:[{id:'obs:payments-service'}]}});
  assert.equal(state.log.value.at(-1).validationStatus,'passed');
  assert.equal(state.log.value.at(-1).report.sources[0].id,'obs:payments-service');
  assert.ok(state.log.value.at(-1).comparisonText.includes('-20%p'));
  assert.equal(rejected.validationStatus,'rejected');reportRequest.onmessage({data:'[DONE]'});
  state.input.value='안녕';state.onSubmit();
  requests.at(-1).onmessage({data:JSON.stringify({type:'AnswerToken',delta:'안녕'})});
  assert.ok(!state.log.value.at(-1).validationStatus);
  requests.at(-1).onmessage({data:JSON.stringify({type:'FinalAnswer',reply:'안녕',validation_status:null})});
  assert.equal(state.log.value.at(-1).validationStatus,null);requests.at(-1).onmessage({data:'[DONE]'});
  const rows=[{run_id:'run-one',query:'payments-service 진단',created:'fixed',processing_status:'partial',validation_status:'unverified',is_sample:true}];
  let seenHistory=[];
  state=mount([],async url=>{seenHistory.push(url);return {ok:true,json:async()=>url.startsWith('/investigations?')?{investigations:rows}:{...rows[0],reply:'저장된 관측과 출처',report:{actions:[]}}};});
  await new Promise(resolve=>setImmediate(resolve));
  state.toggleHistory();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(state.investigations.value.length,1);
  assert.ok(!seenHistory[0].includes('thread_id=')); // global by default
  await state.openInvestigation('run-one');
  assert.equal(state.selectedInvestigation.value.reply,'저장된 관측과 출처');
  assert.equal(state.selectedInvestigation.value.validation_status,'unverified');
  const downloadLink=state.downloadUrl('run-one');
  assert.ok(downloadLink.includes('/investigations/run-one/report.md?thread_id='));
  assert.ok(downloadLink.includes(encodeURIComponent(storage.get('ai-mini-project.thread-id'))));
  state.newConversation();assert.equal(state.historyOpen.value,false);
  assert.equal(state.selectedInvestigation.value,null);assert.equal(state.investigations.value.length,0);
  let resolveOld;
  state=mount([],url=>url.startsWith('/investigations/old?')?new Promise(resolve=>{resolveOld=resolve;}):Promise.resolve({ok:true,json:async()=>({run_id:'new',reply:'선택한 새 기록'})}));
  await new Promise(resolve=>setImmediate(resolve));
  const oldDetail=state.openInvestigation('old');await state.openInvestigation('new');
  resolveOld({ok:true,json:async()=>({run_id:'old',reply:'늦게 도착한 기록'})});await oldDetail;
  assert.equal(state.selectedInvestigation.value.run_id,'new');
  const otherSession=state.openInvestigation('old');state.newConversation();
  resolveOld({ok:true,json:async()=>({run_id:'old',reply:'다른 세션의 늦은 기록'})});await otherSession;
  assert.equal(state.selectedInvestigation.value,null);
  state=mount([],async()=>({ok:false}));await new Promise(resolve=>setImmediate(resolve));
  state.toggleHistory();await new Promise(resolve=>setImmediate(resolve));
  assert.ok(state.historyError.value.includes('불러오지 못했습니다'));
  // Phase 9: global/filtered pagination and stale request isolation.
  let resolveList, historyCalls=[];
  const originalThread=storage.get('ai-mini-project.thread-id');
  const rowA={run_id:'a',thread_id:'conversation-A',query:'payments 진단',targets:['payments-service']};
  const rowB={run_id:'b',thread_id:'conversation-B',query:'cache 진단',targets:['cache-service']};
  state=mount([],async url=>{
    historyCalls.push(url);
    const u=new URL(url,'http://test');
    if(u.pathname==='/investigations') {
      if(u.searchParams.get('before')) return {ok:true,json:async()=>({investigations:[rowA,rowB],next_cursor:null})};
      if(u.searchParams.has('thread_id')) return {ok:true,json:async()=>({investigations:[],next_cursor:null})};
      return {ok:true,json:async()=>({investigations:[rowA],next_cursor:'a'})};
    }
    return {ok:true,json:async()=>({...rowA,reply:'original report'})};
  });
  await new Promise(resolve=>setImmediate(resolve));state.toggleHistory();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(state.historyScope.value,'all');assert.equal(state.historyCursor.value,'a');
  await state.loadInvestigations(true);assert.equal(state.investigations.value.length,2);
  assert.ok(historyCalls.some(url=>url.includes('before=a')));
  await state.openInvestigation(rowA);
  assert.ok(historyCalls.at(-1).includes('thread_id=conversation-A'));
  assert.ok(state.downloadUrl('a').includes('thread_id=conversation-A'));
  assert.equal(storage.get('ai-mini-project.thread-id'),originalThread);
  state.setHistoryScope('current');await new Promise(resolve=>setImmediate(resolve));
  assert.ok(historyCalls.at(-1).includes(encodeURIComponent(originalThread)));
  state.newConversation();state.toggleHistory();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(state.investigations.value[0].run_id,'a');assert.equal(state.historyScope.value,'all');
  state=mount([],url=>url.includes('thread_id=') ? Promise.resolve({ok:true,json:async()=>({investigations:[rowB],next_cursor:null})}) : new Promise(resolve=>{resolveList=resolve;}));
  await new Promise(resolve=>setImmediate(resolve));state.toggleHistory();state.setHistoryScope('current');
  await new Promise(resolve=>setImmediate(resolve));
  resolveList({ok:true,json:async()=>({investigations:[rowA],next_cursor:'a'})});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(state.investigations.value[0].run_id,'b');assert.equal(state.historyCursor.value,null);
  state.setHistoryScope('all');state.newConversation();
  resolveList({ok:true,json:async()=>({investigations:[rowA],next_cursor:'a'})});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(state.investigations.value.length,0);assert.equal(state.historyLoading.value,false);
  // Phase 10: update stable rows, separate attempts/stages/events, preserve reading position.
  state=mount([]);await new Promise(resolve=>setImmediate(resolve));
  state.input.value='payments-service 진단';state.onSubmit();
  const liveRequest=requests.at(-1), progressRow=state.log.value.find(r=>r.kind==='steps');
  await new Promise(resolve=>setImmediate(resolve));
  const sendProgress=(id,status,attempt=1)=>liveRequest.onmessage({data:JSON.stringify({type:'Progress',step:{id:`${id}:attempt:${attempt}`,stage_id:id,label:id,status,attempt}})});
  state.logEl.value={scrollTop:100,scrollHeight:1000,clientHeight:400};
  sendProgress('monitoring:payments-service','running');await new Promise(resolve=>setImmediate(resolve));
  assert.equal(state.logEl.value.scrollTop,100);
  sendProgress('monitoring:payments-service','failed');sendProgress('monitoring:payments-service','running',2);
  sendProgress('monitoring:payments-service','completed',2);
  assert.equal(progressRow.steps.length,2);assert.equal(state.stepCount(progressRow.steps),1);
  assert.equal(progressRow.eventCount,4);assert.ok(state.stepText(progressRow.steps[0]).includes('실패'));
  assert.ok(state.stepText(progressRow.steps[1]).includes('시도 2'));assert.equal(state.stepIcon(progressRow.steps[1]),'✓');
  // Legacy completion events must not create duplicate rows on the new stream.
  liveRequest.onmessage({data:JSON.stringify({type:'TaskCompleted',source:'monitoring',target:'payments-service'})});
  assert.equal(progressRow.steps.length,2);
  state.logEl.value={scrollTop:590,scrollHeight:1000,clientHeight:400};
  sendProgress('write','running');await new Promise(resolve=>setImmediate(resolve));
  assert.equal(state.logEl.value.scrollTop,1000);
  liveRequest.onmessage({data:JSON.stringify({type:'ProcessingError',message:'실패 안내'})});
  assert.equal(progressRow.steps.at(-1).status,'interrupted');
  liveRequest.onmessage({data:'[DONE]'});assert.equal(state.sending.value,false);assert.equal(progressRow.done,true);
  const saved=state.savedSteps({progress:[{id:'old',stage_id:'write',label:'작성',status:'running',attempt:1},{id:'next',stage_id:'validate',label:'검증',status:'pending',attempt:1}]});
  assert.equal(saved[0].status,'interrupted');assert.equal(saved[1].status,'skipped');
  assert.ok(state.savedSteps({events:[{type:'TaskFailed',source:'monitoring',target:'cache-service'}]})[0].includes('실패'));
  assert.equal(state.savedSteps({}).length,0);
  state.input.value='로그인이 느려요';state.onSubmit();
  const clarifyRequest=requests.at(-1), clarifySteps=state.log.value.filter(r=>r.kind==='steps').at(-1);
  clarifyRequest.onmessage({data:JSON.stringify({type:'Progress',step:{id:'route:attempt:1',stage_id:'route',label:'요청 분류',status:'completed',attempt:1}})});
  clarifyRequest.onmessage({data:JSON.stringify({type:'RouteDecided',route:'incident'})});
  clarifyRequest.onmessage({data:JSON.stringify({type:'TargetConfirmationRequired'})});
  assert.equal(clarifySteps.incident,false);assert.equal(state.stepCount(clarifySteps.steps),1);
  clarifyRequest.onmessage({data:'[DONE]'});
  const sample='검증 통과\n\n1) 관측값\n- CPU 91% <script>literal</script>\n\n2) 분석\n- 가설\n\n3) 권장 조치\n- 조건 미확인\n\n4) 미확인 사항\n- 원인 확정 불가\n\n5) 출처\n- obs:payments-service';
  const blocks=state.reportBlocks(sample);
  assert.equal(state.reportBlocks(sample,'passed').length,5);
  assert.equal(blocks.length,6);assert.equal(blocks[0].body,'검증 통과');
  assert.equal(blocks[1].title,'1) 관측값');assert.ok(blocks[1].body.includes('<script>literal</script>'));
  assert.equal(blocks[4].body,'- 원인 확정 불가');
  assert.equal(state.reportBlocks('일반 대화 <b>텍스트</b>')[0].body,'일반 대화 <b>텍스트</b>');
  assert.equal(state.reportBlocks('1) 관측값\n일부만 작성된 초안').length,1);
  assert.equal(state.reportBlocks('1) 관측값\n3) 분석\n2) 권장 조치\n4) 미확인 사항\n5) 출처').length,1);
  console.log('PASS: restore, stable thread ID, new conversation, duplicate submit guard, DONE completion, timeout/network error, pending/rejected/passed report/general chat, history list/detail, new-session reset stale-response isolation, comparison rendering and Markdown download URL');
})().catch(error => {console.error(error); process.exitCode = 1;});
