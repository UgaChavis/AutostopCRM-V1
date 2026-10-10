function managerDocumentUrl(reference,revision,source=''){
  const value=String(reference||'');
  if(/[\u0000-\u001f\u007f\\]/.test(value)||value.startsWith('//'))return null;
  if(/^https?:/i.test(value)){
    try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)?url.href:null}catch(_error){return null}
  }
  if(!/^[a-f\d]{40}$/.test(revision||''))return null;
  let path,fragment='';
  try{const hash=value.indexOf('#');path=decodeURIComponent((hash<0?value:value.slice(0,hash)).split('?')[0]);if(hash>=0)fragment=decodeURIComponent(value.slice(hash+1))}catch(_error){return null}
  if(/[\u0000-\u001f\u007f\\]/.test(path)||path.startsWith('/'))return null;
  let line=null;const hint=/^(.+):([+-]?\p{Decimal_Number}+)$/u.exec(path);
  if(hint){
    const bareExtension=/\.(?:md|markdown|jsonl?|pyi?|toml|yaml|yml|txt|rst|sh|bash|jsx?|tsx?|html?|css|scss|sql|xml|csv|ini|cfg|conf)$/i.test(hint[1]);
    if(!hint[1].includes('/')&&!bareExtension)return null;
    line=Number(hint[2]);if(!/^[0-9]+$/.test(hint[2])||!Number.isSafeInteger(line)||line<=0)return null;path=hint[1];
  }
  if(/^[a-z][a-z\d+.-]*:/i.test(path))return null;
  let sourcePath;try{sourcePath=decodeURIComponent(source||'')}catch(_error){return null}
  if(/[\u0000-\u001f\u007f\\:?#]/.test(sourcePath)||sourcePath.startsWith('/'))return null;
  const parts=[];
  for(const part of sourcePath.split('/')){
    if(!part||part==='.')continue;
    if(part==='..'){if(!parts.length)return null;parts.pop()}else parts.push(part);
  }
  const sourceFile=parts.pop();
  if(!path&&sourceFile)parts.push(sourceFile);
  for(const part of path.split('/')){
    if(!part||part==='.')continue;
    if(part==='..'){if(!parts.length)return null;parts.pop()}else parts.push(part);
  }
  if(!parts.length)return null;
  const url=new URL(`https://github.com/UgaChavis/AutostopManager/blob/${revision}/${parts.map(encodeURIComponent).join('/')}`);
  if(line!==null)url.hash=`L${line}`;else if(fragment)url.hash=fragment;
  return url.href;
}
function documentLink(label,reference,revision,source=''){
  const href=managerDocumentUrl(reference,revision,source);
  if(!href)return document.createTextNode(label);
  const anchor=textElement('a',label);anchor.href=href;anchor.target='_blank';anchor.rel='noopener noreferrer';return anchor;
}
const instructionMarkdown=window.markdownit('commonmark').enable('table');
const instructionTags=new Set(['p','h1','h2','h3','h4','h5','h6','blockquote','ul','ol','li','table','thead','tbody','tr','th','td','em','strong']);
function instructionMarkdownBody(text){
  const source=String(text||'').replace(/\r\n?/g,'\n'),lines=source.split('\n');
  if(lines[0].replace(/[ \t]+$/,'')!=='---')return {body:source,metadata:''};
  for(let index=1;index<lines.length;index++){
    if(lines[index].replace(/[ \t]+$/,'')==='---')return {body:'\n'.repeat(index+1)+lines.slice(index+1).join('\n'),metadata:lines.slice(0,index+1).join('\n')};
  }
  return {body:source,metadata:''};
}
function appendInstructionTokens(target,tokens,reference,revision){
  const stack=[target];
  for(const token of tokens){
    const parent=stack[stack.length-1];
    if(token.type==='inline'){appendInstructionTokens(parent,token.children||[],reference,revision);continue}
    if(token.type==='link_open'){
      const destination=token.attrGet('href')||'';
      // A custom CRM module has no canonical document base. Only explicit
      // HTTP(S) destinations may navigate until a source is known.
      const href=reference||/^https?:/i.test(destination)?managerDocumentUrl(destination,revision,reference):null;
      const element=textElement(href?'a':'span','');
      if(href){element.href=href;element.target='_blank';element.rel='noopener noreferrer'}
      parent.append(element);stack.push(element);continue;
    }
    if(token.nesting===-1){if(stack.length>1)stack.pop();continue}
    if(token.nesting===1){
      const element=textElement(instructionTags.has(token.tag)?token.tag:'span','');
      if(token.tag==='ol'&&/^[0-9]+$/.test(token.attrGet('start')||''))element.start=Number(token.attrGet('start'));
      parent.append(element);stack.push(element);continue;
    }
    if(token.type==='fence'||token.type==='code_block'){
      const pre=textElement('pre',''),code=textElement('code',token.content);pre.append(code);parent.append(pre);continue;
    }
    if(token.type==='code_inline'){
      parent.append(textElement('code',token.content));continue;
    }
    if(token.type==='softbreak'){parent.append(document.createTextNode('\n'));continue}
    if(token.type==='hardbreak'){parent.append(document.createElement('br'));continue}
    if(token.type==='hr'){parent.append(document.createElement('hr'));continue}
    // HTML and image children are opaque literal text; their URLs/attributes
    // are never inserted into the DOM or traversed as document links.
    if(token.type==='html_block'||token.type==='html_inline'||token.type==='image'){
      parent.append(textElement('span',token.content,'instruction-literal'));continue;
    }
    if(token.content)parent.append(document.createTextNode(token.content));
  }
}
function instructionLinks(target,text,reference,revision){
  target.replaceChildren();
  const source=instructionMarkdownBody(text);
  if(source.metadata)target.append(textElement('pre',source.metadata));
  try{appendInstructionTokens(target,instructionMarkdown.parse(source.body,{}),reference,revision)}
  catch(_error){target.replaceChildren(document.createTextNode(String(text||'')))}
}
