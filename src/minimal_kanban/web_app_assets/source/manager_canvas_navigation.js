function fit() {
  if (!diagram) return;
  const box = $('viewport').getBoundingClientRect(), points = [];
  for (const node of diagram.elements) points.push({x:node.x,y:node.y},{x:node.x+node.width,y:node.y+node.height});
  for (const edge of diagram.relations) {
    for (const [a,b] of routeSegments(edge.path)) points.push(a,b);
    points.push({x:edge.label_x-70,y:edge.label_y-18},{x:edge.label_x+70,y:edge.label_y+18});
  }
  if (!points.length) points.push({x:0,y:0},{x:diagram.canvas.width,y:diagram.canvas.height});
  let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  for (const point of points) {
    minX=Math.min(minX,point.x); minY=Math.min(minY,point.y);
    maxX=Math.max(maxX,point.x); maxY=Math.max(maxY,point.y);
  }
  minX=Math.max(0,minX-36); minY=Math.max(0,minY-36);
  maxX=Math.min(diagram.canvas.width,maxX+36); maxY=Math.min(diagram.canvas.height,maxY+36);
  const width=Math.max(1,maxX-minX),height=Math.max(1,maxY-minY),clean=document.body.classList.contains('clean');
  const top=clean?Math.max(0,$('modeToggle').getBoundingClientRect().bottom-box.top+8):0;
  const bottom=clean?Math.max(0,box.bottom-$('mapNavigation').getBoundingClientRect().top+8):0;
  const availableHeight=Math.max(1,box.height-top-bottom);
  scale=Math.min(Math.max(1,box.width-42)/width,Math.max(1,availableHeight-42)/height,2.2);
  offsetX=(box.width-width*scale)/2-minX*scale;
  offsetY=top+(availableHeight-height*scale)/2-minY*scale;
  transform();
}

function updateCanvasNavigation() {
  const busy = !diagram || Boolean(gesture || pan);
  $('mapScale').textContent = `${Math.round(scale * 100)}%`;
  $('mapZoomOut').disabled = busy || scale <= .12;
  $('mapZoomIn').disabled = busy || scale >= 4;
  $('mapFit').disabled = busy;
}

function zoomCanvas(factor, x, y) {
  if (!diagram || gesture || pan) return;
  if (factor <= 1 && scale <= .12) return;
  const rect = $('diagram').getBoundingClientRect();
  x ??= rect.width / 2;
  y ??= rect.height / 2;
  const next = Math.min(4, Math.max(.12, scale * factor));
  offsetX = x - (x - offsetX) * next / scale;
  offsetY = y - (y - offsetY) * next / scale;
  scale = next;
  transform();
}

function showCanvasLoadError(error) {
  const auth = [401, 403].includes(error.status);
  $('canvasLoadMessage').textContent = auth
    ? 'Войдите в CRM, затем откройте конструктор.'
    : `Не удалось загрузить схему. ${error.message || 'Повторите загрузку.'}`;
  $('canvasLoadRetry').hidden = auth;
  $('canvasLogin').hidden = !auth;
  $('canvasLoadError').hidden = false;
}

function clearCanvasLoadError() {
  $('canvasLoadError').hidden = true;
  $('canvasLoadRetry').disabled = false;
}

$('canvasLoadRetry').addEventListener('click', async () => {
  $('canvasLoadRetry').disabled = true;
  try { await load(!diagram); } finally { $('canvasLoadRetry').disabled = false; }
});

function finishPan(event, suppressClick = false) {
  if (!pan || pan.id !== event.pointerId) return;
  const active = pan;
  pan = null;
  $('diagram').classList.remove('dragging');
  updateCanvasNavigation();
  if (suppressClick && active.moved) {
    ignoreNextClick = true;
    setTimeout(() => { ignoreNextClick = false; }, 100);
  }
  if ($('diagram').hasPointerCapture(active.id)) $('diagram').releasePointerCapture(active.id);
}

$('diagram').addEventListener('wheel', event => {
  event.preventDefault();
  if (gesture || pan) return;
  const rect = $('diagram').getBoundingClientRect(), x = event.clientX - rect.left, y = event.clientY - rect.top;
  zoomCanvas(Math.exp(-event.deltaY * .0015), x, y);
}, {passive: false});

$('diagram').addEventListener('pointerdown', event => {
  if (event.button !== 0 || gesture || pan || event.target.closest('[data-id],[data-action]')) return;
  pan = {id: event.pointerId, x: event.clientX, y: event.clientY, ox: offsetX, oy: offsetY, moved: false};
  updateCanvasNavigation();
  $('diagram').setPointerCapture(event.pointerId);
});
$('diagram').addEventListener('pointermove', event => {
  if (!pan || pan.id !== event.pointerId) return;
  if (!(event.buttons & 1)) { finishPan(event); return; }
  const dx = event.clientX - pan.x, dy = event.clientY - pan.y;
  if (Math.abs(dx) + Math.abs(dy) > 4) pan.moved = true;
  if (pan.moved) {
    offsetX = pan.ox + dx;
    offsetY = pan.oy + dy;
    $('diagram').classList.add('dragging');
    transform();
  }
});
$('diagram').addEventListener('pointerup', event => finishPan(event, true));
$('diagram').addEventListener('pointercancel', event => finishPan(event));
$('diagram').addEventListener('lostpointercapture', event => finishPan(event));

function cancelGesture(event) {
  if (!gesture || gesture.pointerId !== event.pointerId) return;
  const active = gesture;
  gesture = null;
  clearTimeout(active.timer);
  previewSeq++;
  diagram = active.original;
  render();
  if ($('diagram').hasPointerCapture(active.pointerId)) $('diagram').releasePointerCapture(active.pointerId);
}
$('diagram').addEventListener('pointercancel', cancelGesture);
$('diagram').addEventListener('lostpointercapture', cancelGesture);

$('mapZoomOut').addEventListener('click', () => zoomCanvas(1 / 1.25));
$('mapZoomIn').addEventListener('click', () => zoomCanvas(1.25));
$('mapFit').addEventListener('click', () => { if (!gesture && !pan) fit(); });
$('diagram').addEventListener('keydown', event => {
  if (event.altKey || event.ctrlKey || event.metaKey || gesture || pan) return;
  if (['+', '=', '-'].includes(event.key)) {
    event.preventDefault();
    zoomCanvas(event.key === '-' ? 1 / 1.25 : 1.25);
  } else if (['0', 'Home'].includes(event.key)) {
    event.preventDefault();
    fit();
  }
});

window.addEventListener('resize', () => {
  if (!diagram) return;
  const obj = (selection?.type === 'element' ? diagram.elements : diagram.relations).find(item => item.id === selection?.id);
  if (obj) focusItem(selection.type, obj); else fit();
});
load(true);
