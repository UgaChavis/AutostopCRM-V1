const toolStates={not_commissioned:['#f07f79','Ещё не введена'],temporarily_unavailable:['#e8c86c','Временно не работает'],working:['#65d9a0','Работает нормально']};
let toolBundle=null,toolBundleSession=null,toolBundleRequest=null,toolDialogModule=null,toolDialogIntent=0,toolOpenerId=null,toolDialogCatalogKey='',toolDialogSession='',toolDialogNodeKey='',toolDialogFocusRevision=0;
const toolCards=new Map(),toolAttempts=new Map(),toolDrafts=new Map();
const toolCatalogRetry=textElement('button','Повторить загрузку');
toolCatalogRetry.id='toolCatalogRetry';
toolCatalogRetry.type='button';
toolCatalogRetry.hidden=true;
$('toolCatalogMessage').after(toolCatalogRetry);
toolCatalogRetry.addEventListener('click',retryToolDialog);
function sessionIdentity(){try{return localStorage.getItem('kanban-operator-session')||''}catch(_error){return ''}}
function textElement(tag,text,className=''){const element=document.createElement(tag);element.textContent=text;if(className)element.className=className;return element}
    // @include manager_documentation.js
function moduleDocumentRef(id){return id==='A2'?'AGENTS.md':/^(?:A[1-5]|B[1-4]|C[2-7]|D[1-5]|E(?:[1-9]|1[0-5])|F[1-5]|G1|H[12]|I[12]|J1|M[12])$/.test(id)?`docs/agent/modules/${id}.md`:''}
function instructionMetadata(target,reference,bundle){target.replaceChildren(documentLink(reference,reference,bundle.source_revision),document.createTextNode(` · Manager ${bundle.source_revision.slice(0,12)} · каталог ${bundle.content_hash.slice(0,12)}`));target.title=`Manager ${bundle.source_revision} · ${bundle.content_hash}`}
function cardMessage(card,message,error=false){card.error.textContent=message;card.error.classList.toggle('is-error',error)}
function toolDialogMessage(message){$('toolCatalogMessage').textContent=message;$('toolCatalogMessage').hidden=!message}
function catalogKey(value){return JSON.stringify([value?.schema_version,value?.content_hash,value?.source_revision])}
function moduleInstructionKey(node){return JSON.stringify([node.id,node.title,node.description||'',node.instruction||node.description||''])}
function dialogLinkKey(anchor){return JSON.stringify([anchor.textContent,anchor.href.replace(/^(https:\/\/github\.com\/UgaChavis\/AutostopManager\/blob\/)[a-f\d]{40}\//,'$1')])}
function captureToolDialogView(){
  const view={details:[...toolCards].filter(([_id,card])=>card.article.querySelector('details')?.open).map(([id])=>id),focus:null};
  const active=document.activeElement;
  if(!$('toolDialog').contains(active))return view;
  if(active.id==='closeToolDialog'||active.id==='toolCatalogRetry'){view.focus={id:active.id};return view}
  const article=active.closest('.tool-card'),card=article&&toolCards.get(article.dataset.toolId);
  if(card){
    for(const field of ['select','save','conflict'])if(active===card[field]){view.focus={toolId:article.dataset.toolId,field};return view}
    if(active.tagName==='SUMMARY'){view.focus={toolId:article.dataset.toolId,field:'summary'};return view}
  }
  if(active.tagName==='A'){
    const container=article||active.closest('#toolDialogMeta,#toolDialogInstruction');
    if(container){
      const key=dialogLinkKey(active),links=[...container.querySelectorAll('a[href]')].filter(link=>dialogLinkKey(link)===key);
      view.focus={toolId:article?.dataset.toolId,container:article?null:container.id,linkKey:key,index:links.indexOf(active)};
    }
  }
  return view;
}
function restoreToolDialogView(view,restoreFocus){
  for(const id of view.details){const details=toolCards.get(id)?.article.querySelector('details');if(details)details.open=true}
  if(!restoreFocus||!view.focus)return;
  const focus=view.focus,card=focus.toolId&&toolCards.get(focus.toolId);
  let target=focus.id?$(focus.id):focus.field==='summary'?card?.article.querySelector('summary'):focus.field?card?.[focus.field]:null;
  if(focus.linkKey){
    const container=focus.toolId?card?.article:$(focus.container);
    target=container&&[...container.querySelectorAll('a[href]')].filter(link=>dialogLinkKey(link)===focus.linkKey)[focus.index];
  }
  if(target&&!target.hidden&&!target.disabled&&target.getClientRects().length)target.focus({preventScroll:true});
}
function toolIntentKey(id,token=sessionIdentity()){return `${token}\n${id}`}
function canEditToolStatuses(){return Boolean(diagram?.can_edit_tool_status&&diagramSession===sessionIdentity()&&diagramSession)}
async function catalogBundle(){const key=catalogKey(diagram?.catalog_metadata),token=sessionIdentity();if(toolBundle&&toolBundleSession===token&&catalogKey(toolBundle)===key)return toolBundle;if(!toolBundleRequest||toolBundleRequest.key!==key||toolBundleRequest.token!==token){const request={key,token};request.promise=api('/api/manager_structure/tool_catalog').then(async bundle=>{if(token!==sessionIdentity()||toolBundleRequest!==request)throw new Error('Каталог или сессия изменились. Откройте окно заново.');if(catalogKey(diagram?.catalog_metadata)!==key){toolBundleRequest=null;return catalogBundle()}if(catalogKey(bundle)!==key){await readDiagram();if(token!==sessionIdentity()||toolBundleRequest!==request)throw new Error('Каталог или сессия изменились. Откройте окно заново.');if(catalogKey(diagram?.catalog_metadata)===key)throw new Error('Версия каталога не совпала со схемой. Перечитайте состояние.');toolBundleRequest=null;return catalogBundle()}toolBundle=bundle;toolBundleSession=token;return bundle}).finally(()=>{if(toolBundleRequest===request)toolBundleRequest=null});toolBundleRequest=request}return toolBundleRequest.promise}
function closeToolDialog(){
  const id=toolOpenerId;
  toolDialogIntent++;
  toolDialogModule=null;
  toolOpenerId=null;
  toolCards.clear();
  $('toolDialog').close();
  $('toolDialogScroll').removeAttribute('aria-busy');
  for(const name of ['toolDialogMeta','toolDialogPurpose','toolDialogInstruction','toolCards'])$(name).replaceChildren();
  toolDialogMessage('');
  toolCatalogRetry.hidden=true;
  toolCatalogRetry.disabled=true;
  const opener=id&&diagram.elements.some(node=>node.id===id)&&document.querySelector(`.node[data-id="${id}"]`);
  (opener||$('diagram')).focus({preventScroll:true});
}
$('closeToolDialog').addEventListener('click',closeToolDialog);$('toolDialog').addEventListener('cancel',event=>{event.preventDefault();closeToolDialog()});
$('toolDialog').addEventListener('focusin',()=>{toolDialogFocusRevision++});
$('toolDialog').addEventListener('keydown',event=>{if(event.key==='Tab')toolDialogFocusRevision++});
$('toolDialog').addEventListener('pointerdown',()=>{toolDialogFocusRevision++});
$('toolDialog').addEventListener('keydown',event=>{if(event.key!=='Tab')return;const controls=[...$('toolDialog').querySelectorAll('a[href],button:not(:disabled),select:not(:disabled),summary,[tabindex="0"]')].filter(element=>!element.hidden&&element.getClientRects().length),first=controls[0],last=controls.at(-1);if(!first)return;if(event.shiftKey&&(document.activeElement===first||!controls.includes(document.activeElement))){event.preventDefault();last.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}});
function updateToolCard(id,card){const record=diagram.tool_statuses?.[id],state=record?.state||'not_commissioned',[color,label]=toolStates[state]||toolStates.not_commissioned,attempt=toolAttempts.get(toolIntentKey(id)),allowed=canEditToolStatuses();card.dot.style.background=color;card.label.textContent=label;card.meta.textContent=record?`${record.updated_by} · ${record.updated_at}`:'Ручной отметки пока нет';card.pending=Boolean(attempt?.running||card.reading);if(attempt)card.select.value=attempt.body.tool_status.state;else if(!card.dirty&&!card.pending)card.select.value=state;card.select.disabled=card.pending||Boolean(attempt)||!allowed;card.select.hidden=!allowed;card.save.hidden=!allowed;card.save.disabled=card.pending||(!card.dirty&&!attempt)||!allowed;card.conflict.hidden=!allowed||(!attempt&&!card.versionConflict);card.conflict.disabled=card.pending||!allowed;card.conflict.textContent=attempt?'Проверить запись':'Перечитать состояние'}
function sameToolRecord(actual,expected){return Boolean(actual&&expected&&actual.state===expected.state&&actual.updated_at===expected.updated_at&&actual.updated_by===expected.updated_by)}
async function saveToolStatus(id,card){if(card.pending||!canEditToolStatuses())return;const intent=toolDialogIntent,token=sessionIdentity(),key=toolIntentKey(id,token);let attempt=toolAttempts.get(key);if(attempt?.running)return;if(!attempt){attempt={body:{operation:'set_tool_status',tool_status:{operation_id:id,state:card.select.value},expected_version:diagram.version,idempotency_key:crypto.randomUUID()},running:false};toolAttempts.set(key,attempt)}attempt.running=true;card.pending=true;cardMessage(card,'Проверка записи…');updateToolCard(id,card);let phase='apply';try{attempt.receipt=await api('/api/manager_structure/apply',attempt.body);phase='read';await readDiagram();if(token!==sessionIdentity())return;if(diagramSession!==token||diagram.version<attempt.receipt.version)throw new Error('Не удалось перечитать запись. Повторная проверка сохранит исходный запрос.');const actual=diagram.tool_statuses?.[id];if(!sameToolRecord(actual,attempt.receipt.tool_status)){toolAttempts.delete(key);throw new Error('Отметка изменилась после записи. Актуальное состояние прочитано; ваш выбор оставлен.')}toolAttempts.delete(key);toolDrafts.delete(key);const visible=toolDialogSession===token&&toolCards.get(id);if(visible){visible.dirty=false;visible.versionConflict=false;cardMessage(visible,'Сохранено')}if(intent!==toolDialogIntent)return;card.dirty=false;card.versionConflict=false;cardMessage(card,'Сохранено');render();refreshToolStatuses()}catch(error){if(phase==='apply'&&error.status>=400&&error.status<500&&![408,429].includes(error.status))toolAttempts.delete(key);if(intent!==toolDialogIntent||token!==sessionIdentity())return;card.versionConflict=error.code==='manager_structure_version_conflict';cardMessage(card,card.versionConflict?'Схема изменилась. Ваш выбор оставлен; перечитайте состояние перед повторной записью.':toolAttempts.has(key)?'Ответ не подтверждён. Проверьте запись; будет повторён тот же запрос.':error.message,true)}finally{attempt.running=false;card.pending=false;if(token===sessionIdentity())refreshToolStatuses()}}
async function rereadToolStatus(id,card){
  if(card.pending||!canEditToolStatuses())return;
  if(toolAttempts.has(toolIntentKey(id))){await saveToolStatus(id,card);return}
  const intent=toolDialogIntent,token=sessionIdentity();
  card.reading=true;
  cardMessage(card,'Перечитывание состояния…');
  updateToolCard(id,card);
  try{
    await readDiagram();
    if(intent!==toolDialogIntent||token!==sessionIdentity())return;
    card.versionConflict=false;
    cardMessage(card,'Актуальное состояние прочитано; подтвердите сохранение вашего выбора.');
    refreshToolStatuses();
    render();
  }catch(error){
    if(intent===toolDialogIntent&&token===sessionIdentity())cardMessage(card,error.message,true);
  }finally{
    card.reading=false;
    if(intent===toolDialogIntent&&token===sessionIdentity())refreshToolStatuses();
  }
}
function toolInstruction(tool){const text=textElement('div','','tool-mini-instruction');instructionLinks(text,tool.instruction_text,tool.instruction_ref,toolBundle.source_revision);return text}
function toolReference(tool){const text=textElement('p','','tool-technical');text.append(documentLink(tool.instruction_ref,tool.instruction_ref,toolBundle.source_revision));return text}
function buildToolCards(module){$('toolCards').replaceChildren();toolCards.clear();const byId=new Map(toolBundle.tools.map(tool=>[tool.tool_id,tool]));for(const id of module.tool_ids){const tool=byId.get(id);if(!tool)continue;const article=textElement('article','','tool-card');article.dataset.toolId=id;article.append(textElement('h3',tool.title),textElement('p',tool.purpose),textElement('p',`Служба: ${tool.provider_id} · Первичная база: ${tool.primary_data_source||'Указана в инструкции'}`));const implementations={implemented:'Реализован',planned:'Планируется',retired:'Выведен из эксплуатации',historical:'Исторический справочник'};article.append(textElement('p',`${implementations[tool.implementation_state]||tool.implementation_state} · Выполнение: ${tool.execution_kind||'См. инструкцию'} · Доступ источника: не проверялся этим окном`,'tool-technical'));if(tool.limitations?.length)article.append(textElement('p',tool.limitations.join(' · ')));const details=document.createElement('details');details.append(textElement('summary','Вызов, параметры и мини-инструкция'),textElement('pre',JSON.stringify({invocation:tool.invocation,input_fields:tool.input_fields,input_schema_ref:tool.input_schema_ref,input_schema:tool.input_schema||toolBundle.native_schemas?.[tool.input_schema_ref],outputs:tool.outputs,errors:tool.errors,example:tool.example,invalid_example:tool.invalid_example},null,2)),toolInstruction(tool),toolReference(tool));article.append(details);const row=textElement('div','','tool-status-row'),dot=textElement('span','','tool-status-dot'),label=textElement('span',''),statusLabel=textElement('span','','tool-status-label');statusLabel.append(dot,label);const select=document.createElement('select');select.setAttribute('aria-label',`Ручная отметка: ${tool.title}`);for(const [value,[_color,title]]of Object.entries(toolStates))select.add(new Option(title,value));const save=textElement('button','Сохранить отметку');save.type='button';const conflict=textElement('button','Перечитать состояние');conflict.type='button';conflict.hidden=true;row.append(statusLabel,select,save,conflict);article.append(row);const meta=textElement('p','','tool-technical'),error=textElement('p','','tool-status-error');error.setAttribute('role','status');article.append(meta,error);const prior=toolDrafts.get(toolIntentKey(id)),card={article,dot,label,select,save,conflict,meta,error,dirty:Boolean(prior),pending:false,versionConflict:false};if(prior)select.value=prior.state;toolCards.set(id,card);select.addEventListener('change',()=>{card.dirty=true;toolDrafts.set(toolIntentKey(id),{state:select.value});cardMessage(card,'Выбор ещё не сохранён');updateToolCard(id,card)});save.addEventListener('click',()=>saveToolStatus(id,card));conflict.addEventListener('click',()=>rereadToolStatus(id,card));$('toolCards').append(article);updateToolCard(id,card)}}
function toolDialogFailure(error){
  $('toolDialogMeta').textContent='Каталог не загружен';
  $('toolDialogMeta').title='';
  toolDialogMessage(error.message||'Не удалось загрузить каталог.');
  toolCatalogRetry.hidden=!sessionIdentity()||[401,403].includes(error.status);
  toolCatalogRetry.disabled=toolCatalogRetry.hidden;
}
async function retryToolDialog(){
  const intent=toolDialogIntent,token=sessionIdentity();
  if(!$('toolDialog').open||!toolDialogModule||!token)return;
  toolCatalogRetry.disabled=true;
  try{
    await readDiagram();
    if(intent!==toolDialogIntent||token!==sessionIdentity())return;
    const node=diagram.elements.find(item=>item.id===toolDialogModule);
    if(!node){closeToolDialog();return}
    await openToolDialog(node);
  }catch(error){if(intent===toolDialogIntent&&token===sessionIdentity())toolDialogFailure(error)}
}
async function openToolDialog(node,focusClose=true){
  toolOpenerId=node.id;
  toolDialogModule=node.id;
  toolDialogCatalogKey=catalogKey(diagram.catalog_metadata);
  toolDialogSession=sessionIdentity();
  toolDialogNodeKey=moduleInstructionKey(node);
  const intent=++toolDialogIntent;
  toolDialogMessage('');
  toolCatalogRetry.hidden=true;
  toolCatalogRetry.disabled=false;
  $('toolDialogTitle').textContent=`${node.id} · ${node.title}`;
  $('toolDialogMeta').textContent='Загрузка каталога…';
  $('toolDialogMeta').title='';
  $('toolDialogPurpose').textContent='';
  $('toolDialogInstruction').textContent='';
  $('toolCards').replaceChildren();
  toolCards.clear();
  const automotive=/^E(?:[1-9]|1[0-5])$/.test(node.id);
  $('toolStatusHelp').hidden=!automotive;
  $('toolDialogScroll').setAttribute('aria-busy',String(automotive));
  if(!$('toolDialog').open)$('toolDialog').showModal();
  if(focusClose)$('closeToolDialog').focus();
  if(!automotive){
    const reference=moduleDocumentRef(node.id),metadata=diagram.catalog_metadata;
    $('toolDialogPurpose').textContent=node.description||'';
    if(metadata?.available)instructionMetadata($('toolDialogMeta'),reference,metadata);
    else $('toolDialogMeta').textContent=reference||'Инструкция сохранена в CRM';
    instructionLinks($('toolDialogInstruction'),node.instruction||node.description||'',reference,metadata?.source_revision);
    $('toolDialogScroll').scrollTop=0;
    return;
  }
  try{
    const bundle=await catalogBundle();
    if(intent!==toolDialogIntent)return;
    const module=bundle.modules.find(item=>item.element_id===node.id);
    if(!module){
      toolDialogFailure(new Error('Для этого модуля нет автомобильного каталога.'));
      return;
    }
    $('toolDialogTitle').textContent=`${module.element_id} · ${module.title}`;
    $('toolDialogPurpose').textContent=module.purpose;
    instructionMetadata($('toolDialogMeta'),module.instruction_ref,bundle);
    instructionLinks($('toolDialogInstruction'),module.instruction_text,module.instruction_ref,bundle.source_revision);
    buildToolCards(module);
    $('toolDialogScroll').scrollTop=0;
  }catch(error){if(intent===toolDialogIntent)toolDialogFailure(error)}
  finally{if(intent===toolDialogIntent)$('toolDialogScroll').setAttribute('aria-busy','false')}
}
function refreshToolStatuses(){
  if(!$('toolDialog').open)return;
  const node=diagram.elements.find(item=>item.id===toolDialogModule);
  if(!node){closeToolDialog();return}
  for(const [id,card]of toolCards)updateToolCard(id,card);
  if(!sessionIdentity()){
    toolDialogMessage('Сессия завершена. Откройте CRM и войдите заново.');
    toolCatalogRetry.hidden=true;
    toolCatalogRetry.disabled=true;
    return;
  }
  if(toolDialogSession!==sessionIdentity()||toolDialogCatalogKey!==catalogKey(diagram.catalog_metadata)||(!/^E(?:[1-9]|1[0-5])$/.test(toolDialogModule)&&toolDialogNodeKey!==moduleInstructionKey(node))){
    const scroll=$('toolDialogScroll').scrollTop,token=sessionIdentity(),moduleId=node.id,sameSession=toolDialogSession===token,view=captureToolDialogView();
    const opening=openToolDialog(node,!sameSession),intent=toolDialogIntent,focusRevision=toolDialogFocusRevision;
    opening.then(()=>{
      if(intent!==toolDialogIntent||token!==sessionIdentity()||toolDialogModule!==moduleId||!$('toolDialog').open)return;
      restoreToolDialogView(view,sameSession&&focusRevision===toolDialogFocusRevision);
      $('toolDialogScroll').scrollTop=scroll;
    });
  }
}
