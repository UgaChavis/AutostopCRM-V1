// Editor attempts retain their exact request until receipt and readback agree.
const editorAttempts=new Map(),editorDrafts=new Map();
let editorIntent=0,editorSession=null,editorTransition=false,editorAccessDenied=false,editorFileSequence=0;
function editorAttempt(){return editorAttempts.get(sessionIdentity())}
function editorMutationBusy(){return Boolean(requesting||editorAttempt())}
function editorAllowed(){return Boolean(diagram?.can_edit&&diagramSession===sessionIdentity()&&sessionIdentity()&&!editorAccessDenied)}
function editorHasDraft(){return Boolean(dirty||draft)}
function editorSnapshot(){
  if(!selection)return null;
  const values={};
  for(const field of E){if(!field.name||field.type==='file')continue;values[field.name]=field.type==='checkbox'?field.checked:field.value}
  const object=draft||(selection.type==='element'?diagram.elements:diagram.relations).find(item=>item.id===selection.id);
  return{selection:{...selection},object:object&&structuredClone(object),draft:draft&&structuredClone(draft),dirty,editingVersion,anchorTouched,values};
}
function editorInternal(action){const prior=editorTransition;editorTransition=true;try{return action()}finally{editorTransition=prior}}
function editorRestore(snapshot){
  const {selection:prior}=snapshot,object=snapshot.draft||(prior.type==='element'?diagram.elements:diagram.relations).find(item=>item.id===prior.id);
  if(!object)return false;
  if(prior.type==='relation'&&!diagram.elements.some(item=>item.id===object.from)||prior.type==='relation'&&!diagram.elements.some(item=>item.id===object.to))return false;
  editorInternal(()=>select(prior.type,prior.id,object,Boolean(snapshot.draft),false));
  for(const field of E){if(!Object.hasOwn(snapshot.values,field.name)||field.type==='file')continue;if(field.type==='checkbox')field.checked=snapshot.values[field.name];else field.value=snapshot.values[field.name]}
  dirty=snapshot.dirty;editingVersion=snapshot.editingVersion;anchorTouched=snapshot.anchorTouched;const attempt=editorAttempt();if(attempt)attempt.intent=editorIntent;return true;
}
function editorDiscardAllowed(discard=false){
  if(editorTransition)return true;
  if(editorMutationBusy()){notice('Сначала проверьте результат текущей записи. Исходный запрос сохранён.',true);return false}
  return discard||!editorHasDraft()||confirm('Есть несохранённые изменения. Отменить их?');
}
function editorMaySelect(type,id,created=false){
  if(editorTransition)return true;
  if(editorHasDraft()&&selection?.type===type&&selection.id===id&&!created)return false;
  const allowed=editorDiscardAllowed();if(!allowed)$('jump').value=draft?'':selection?`${selection.type}:${selection.id}`:'';return allowed;
}
function editorMayClose(discard=false){const allowed=editorDiscardAllowed(discard);if(!allowed)$('jump').value=draft?'':selection?`${selection.type}:${selection.id}`:'';return allowed}
function editorDidSelect(){dirty=false;editorIntent++;editorSession=sessionIdentity();syncEditorControls()}
function editorDidClose(){dirty=false;editorIntent++;editorSession=sessionIdentity();syncEditorControls()}
function syncEditorControls(){
  const allowed=editorAllowed(),attempt=editorAttempt(),busy=Boolean(requesting||attempt),pending=Boolean(attempt&&!attempt.running);
  document.querySelectorAll('[data-create]').forEach(button=>button.disabled=!allowed||busy);
  for(const id of ['expandCanvas','remove','cancel','closePanel','jump','modeToggle'])$(id).disabled=(id==='expandCanvas'||id==='remove'? !allowed:false)||busy;
  $('routeAuto').disabled=!allowed||busy||selection?.type!=='relation';
  $('save').disabled=!allowed||!selection||requesting||Boolean(attempt?.running)||(!pending&&!editorHasDraft());$('save').textContent=pending?'Проверить запись':'Сохранить';
  $('reloadSchema').disabled=!allowed||requesting||Boolean(attempt?.running);$('reloadSchema').textContent=attempt?'Проверить запись':'Перечитать актуальную схему';
  if(pending){$('reloadSchema').hidden=false;$('panel').classList.add('is-open')}
  $('actions').hidden=!allowed||!selection;$('panel').classList.toggle('is-readonly',!allowed);
  if(selection)$('panelHint').textContent=allowed?'Изменения сохраняются после нажатия «Сохранить»':'Доступен просмотр свойств';
  else if(attempt)$('panelHint').textContent='Проверьте результат записи исходного запроса.';
  for(const field of E){if(!field.matches('input,select,textarea'))continue;field.disabled=!allowed||!selection||busy||Boolean(field.closest('#nodeFields')?.hidden)||Boolean(field.closest('#edgeFields')?.hidden)}
  if(diagram)$('mode').textContent=editorAccessDenied?'Нет доступа':allowed?'Владелец · редактирование':'Просмотр';
}
function syncEditorState(){
  const token=sessionIdentity();
  if(editorSession!==null&&editorSession!==token){
    const snapshot=editorSnapshot();if(snapshot&&(editorHasDraft()||editorAttempts.has(editorSession)))editorDrafts.set(editorSession,snapshot);
    editorInternal(()=>close(true));$('editor').reset();editorAccessDenied=false;
    refreshToolStatuses();
  }
  editorSession=token;
  if(editorAllowed()&&!selection&&editorDrafts.has(token)){
    const restored=editorRestore(editorDrafts.get(token));if(restored){editorDrafts.delete(token);notice('Ваш несохранённый текст восстановлен.')}else notice('Выбранный объект удалён. Исходный черновик сохранён в этой сессии; перечитайте схему.',true);
  }
  syncEditorControls();
  if(!requesting&&selection&&!draft&&!dirty){
    const item=(selection.type==='element'?diagram?.elements:diagram?.relations)?.find(item=>item.id===selection.id);
    if(!item){editorInternal(()=>close(true));notice('Выбранный объект удалён после внешней правки.',true)}
  }
}
function invalidateEditorAccess(){editorAccessDenied=true;if(diagram){diagram.can_edit=false;diagram.can_edit_tool_status=false}syncEditorControls();refreshToolStatuses()}
function acceptEditorAccess(){editorAccessDenied=false;syncEditorState()}
function validateEditorDiagram(value){
  if(!value||!Number.isInteger(value.version)||value.version<0||value.schema_version!=='autostopcrm.manager-structure.v1'||typeof value.can_edit!=='boolean'||!Number.isFinite(value.canvas?.width)||!Number.isFinite(value.canvas?.height)||!Array.isArray(value.elements)||!Array.isArray(value.relations)||!value.elements.every(item=>item&&typeof item.id==='string'&&['x','y','width','height'].every(field=>Number.isFinite(item[field])))||!value.relations.every(item=>item&&typeof item.id==='string'&&typeof item.from==='string'&&typeof item.to==='string'&&typeof item.path==='string'&&Number.isFinite(item.label_x)&&Number.isFinite(item.label_y)))throw new Error('Получена неполная схема. Предыдущее состояние сохранено.');
}
function editorEqual(actual,expected){
  if(actual===expected)return true;
  if(Array.isArray(expected))return Array.isArray(actual)&&actual.length===expected.length&&expected.every((value,index)=>editorEqual(actual[index],value));
  if(expected&&typeof expected==='object')return Boolean(actual&&typeof actual==='object'&&!Array.isArray(actual)&&Object.keys(actual).length===Object.keys(expected).length&&Object.entries(expected).every(([key,value])=>Object.hasOwn(actual,key)&&editorEqual(actual[key],value)));
  return false;
}
function editorReceiptValid(attempt,receipt){
  if(!receipt||typeof receipt!=='object'||!Number.isInteger(receipt.version)||receipt.version<=attempt.body.expected_version||typeof receipt.deduplicated!=='boolean')return false;
  if(attempt.body.operation==='layout_element')return receipt.accepted_element?.id===attempt.body.element.id&&['x','y','width','height'].every(field=>Number.isFinite(receipt.accepted_element[field]));
  if(['upsert_relation','layout_relation'].includes(attempt.body.operation)){
    const relation=receipt.accepted_relation;
    if(!(relation?.id===attempt.body.relation.id&&typeof relation.from==='string'&&typeof relation.to==='string'&&['exchange','event'].includes(relation.kind)&&['forward','reverse','both','none'].includes(relation.direction)&&typeof relation.path==='string'&&Number.isFinite(relation.label_x)&&Number.isFinite(relation.label_y)))return false;
    const normalized=new Set(['path','from_anchor','to_anchor','label_x','label_y']);
    return Object.entries(attempt.body.relation).every(([field,value])=>Object.hasOwn(relation,field)&&(normalized.has(field)||editorEqual(relation[field],value)));
  }
  return true;
}
function editorReadbackMatches(attempt){
  const change=attempt.body,receipt=attempt.receipt;
  if(diagramSession!==attempt.token||!Number.isInteger(diagram?.version)||diagram.version<receipt.version)return null;
  if(change.element){
    const actual=diagram.elements.find(item=>item.id===change.element.id);if(!actual)return false;
    const expected={...change.element,...(change.operation==='layout_element'?receipt.accepted_element:{})};
    return Object.entries(expected).every(([field,value])=>editorEqual(actual[field],value));
  }
  if(change.relation){const actual=diagram.relations.find(item=>item.id===change.relation.id);return editorEqual(actual,receipt.accepted_relation)}
  if(change.operation==='remove_element')return !diagram.elements.some(item=>item.id===change.id);
  if(change.operation==='remove_relation')return !diagram.relations.some(item=>item.id===change.id);
  if(change.operation==='set_canvas')return editorEqual(diagram.canvas,change.canvas);
  return false;
}
async function mutate(change){
  syncEditorState();if(requesting||!editorAllowed())return false;
  const token=sessionIdentity();let attempt=editorAttempts.get(token);
  if(!attempt){
    if(draft&&change.element&&diagram.elements.concat(diagram.relations).some(item=>item.id===change.element.id)){notice('Такой ID уже существует. Выберите новый ID для нового модуля.',true);return false}
    if(draft&&change.relation&&diagram.elements.concat(diagram.relations).some(item=>item.id===change.relation.id)){notice('Такой ID уже существует. Выберите новый ID для новой связи.',true);return false}
    attempt={token,intent:editorIntent,body:structuredClone({...change,expected_version:dirty?editingVersion:diagram.version,idempotency_key:crypto.randomUUID()}),receipt:null,running:false};editorAttempts.set(token,attempt);
  }
  requesting=true;attempt.running=true;syncEditorControls();let phase=attempt.receipt?'read':'apply';
  try{
    if(!attempt.receipt){const receipt=await api('/api/manager_structure/apply',attempt.body);if(!editorReceiptValid(attempt,receipt))throw new Error('Ответ записи неполный. Проверьте запись; исходный запрос сохранён.');attempt.receipt=receipt}
    phase='read';await readDiagram();
    if(token!==sessionIdentity())return false;
    const matched=editorReadbackMatches(attempt);if(matched===null)throw new Error('Не удалось перечитать запись. Исходный запрос сохранён.');
    editorAttempts.delete(token);
    if(!matched){if(selection)dirty=true;notice('Запись подтверждена, но объект уже изменён после неё. Ваш текст оставлен; перечитайте свойства перед новой правкой.',true);$('reloadSchema').hidden=false;render();populateJump();return false}
    if(attempt.intent!==editorIntent)return true;
    dirty=false;draft=null;const id=attempt.body.element?.id||attempt.body.relation?.id;
    populateJump();render();refreshToolStatuses();
    editorInternal(()=>{if(id){const type=attempt.body.element?'element':'relation',item=(type==='element'?diagram.elements:diagram.relations).find(entry=>entry.id===id);if(item)select(type,id,item);else close(true)}else close(true)});
    notice('Сохранено');return true;
  }catch(error){
    const definite=phase==='apply'&&error.status>=400&&error.status<500&&![408,429].includes(error.status);if(definite)editorAttempts.delete(token);
    if(token!==sessionIdentity()||attempt.intent!==editorIntent)return false;
    const conflict=error.code==='manager_structure_version_conflict';if(conflict&&selection)dirty=true;
    if(error.status===401||error.status===403)invalidateEditorAccess();
    notice(conflict?'Схема изменилась. Ваш текст оставлен в форме; перечитайте свойства перед новой правкой.':editorAttempts.has(token)?'Ответ записи не подтверждён. Проверьте запись; исходный запрос сохранён.':error.message,true);
    $('reloadSchema').hidden=!conflict&&!editorAttempts.has(token);if(editorAttempts.has(token))$('panel').classList.add('is-open');return false;
  }finally{attempt.running=false;requesting=false;syncEditorState()}
}
async function reloadEditorSchema(){
  if(editorAttempt()){await mutate(editorAttempt().body);return}
  if(!editorDiscardAllowed())return;
  const intent=editorIntent,token=sessionIdentity(),prior=selection&&{...selection};
  try{
    const accepted=await readDiagram();if(!accepted||token!==sessionIdentity()||intent!==editorIntent)return;
    dirty=false;draft=null;populateJump();render();
    editorInternal(()=>{const item=prior&&(prior.type==='element'?diagram.elements:diagram.relations).find(entry=>entry.id===prior.id);if(item)select(prior.type,prior.id,item);else close(true)});
    $('reloadSchema').hidden=true;notice('Актуальная схема загружена.');syncEditorState();
  }catch(error){if(error.status===401||error.status===403)invalidateEditorAccess();notice('Не удалось перечитать схему. Ваш текст оставлен в форме.',true)}
}
window.addEventListener('storage',event=>{if(event.key===null||event.key==='kanban-operator-session')syncEditorState()});
window.addEventListener('focus',syncEditorState);
window.addEventListener('beforeunload',event=>{if(editorHasDraft()||editorAttempts.size){event.preventDefault();event.returnValue=''}});
