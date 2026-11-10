/* Keep unknown JSON properties intact; form editing changes only known fields. */
(function (root) {
  'use strict';
  const groups = {applicant: ['name','email','phone','location','linkedin','portfolio'], recipient: ['company','name','job_title','address']};
  const fields = ['date','subject','greeting','closing','signature'];
  function validate(data) {
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw Error('Document must be an object');
    for (const [group, keys] of Object.entries(groups)) {
      if (!data[group] || typeof data[group] !== 'object' || Array.isArray(data[group])) throw Error(`${group} must be an object`);
      for (const key of keys) if (key in data[group] && typeof data[group][key] !== 'string') throw Error(`${group}.${key} must be text`);
    }
    for (const key of fields) if (typeof data[key] !== 'string') throw Error(`${key} must be text`);
    if (!data.applicant.name?.trim()) throw Error('Applicant name is required');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(data.date) || !Number.isFinite(Date.parse(data.date)) || new Date(data.date).toISOString().slice(0,10) !== data.date) throw Error('Date must be a valid YYYY-MM-DD date');
    if (!Array.isArray(data.paragraphs) || !data.paragraphs.length || data.paragraphs.some(p => typeof p !== 'string') || !data.paragraphs.some(p => p.trim())) throw Error('Add at least one text paragraph');
    return data;
  }
  function mergeForm(data, values) {
    const result = JSON.parse(JSON.stringify(data));
    for (const [group, keys] of Object.entries(groups)) for (const key of keys) result[group][key] = values[group][key];
    for (const key of fields) result[key] = values[key];
    result.paragraphs = values.paragraphs.slice();
    return result;
  }
  root.CoverLetterDocument = {validate, mergeForm};
  if (typeof document === 'undefined') return;
  const $ = id => document.getElementById(id), base = '/api/builder/cover-letter';
  let data = null, activeId = null, view = 'form', blobUrl = null, pdfRequest = 0;
  const status = (message, error=false) => { $('status').textContent = message; $('status').className = error ? 'error' : ''; };
  let toastTimer = null;
  function showToast(message, type='success') {
    const toast=$('toast'); clearTimeout(toastTimer);
    toast.textContent=message; toast.className=type; toast.style.display='block'; toast.showPopover();
    toastTimer=setTimeout(()=>{toast.hidePopover();toast.style.display='none';},3500);
  }
  const guard = fn => async () => { try { await fn(); } catch (e) { status(e.message, true); showToast(e.message,'error'); } };
  async function api(url, options={}) {
    let response;
    try { response=await fetch(url,options); }
    catch (_) { throw Error('Could not reach the server. Your edits are intact. Please try again.'); }
    let body;
    try { body=await response.json(); }
    catch (_) { throw Error(`The server returned an unexpected response (HTTP ${response.status}). Your edits are intact. Please try again.`); }
    if (!response.ok || !body.ok) {
      const error=Error(body.error || 'Request failed'); error.details=body; error.status=response.status; throw error;
    }
    return body;
  }
  function filename() {
    const slug = s => s.replace(/[^a-zA-Z0-9]+/g, '_').replace(/^_|_$/g, '');
    $('filename').value = `${slug(data?.applicant.name || 'Applicant')}_${slug($('company').value) || 'Company'}_Cover_Letter.pdf`;
  }
  function addIcon(button, name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'builder-icon'); svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.75'); svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round'); svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', `/static/builder-icons.svg#${name}`);
    svg.append(use); button.prepend(svg);
  }
  function field(container, key, value, prefix='') {
    const wrapper = document.createElement('div'); wrapper.className = `input-group field-${key}`;
    const label = document.createElement('label'), input = document.createElement(key === 'address' ? 'textarea' : 'input');
    input.className = key === 'address' ? 'styled-textarea' : 'styled-input'; input.id = `field-${prefix}${key}`; input.value = value || ''; if (key === 'date') input.type = 'date';
    label.htmlFor = input.id; label.textContent = key.replaceAll('_',' ').replace(/^./, c=>c.toUpperCase());
    wrapper.append(label,input); $(container).append(wrapper);
  }
  function render() {
    for (const [group, keys] of Object.entries(groups)) {
      $(`${group}-fields`).replaceChildren();
      for (const key of keys) field(`${group}-fields`,key,data[group][key],`${group}-`);
    }
    $('letter-fields').replaceChildren(); for (const key of fields) field('letter-fields',key,data[key]);
    renderParagraphs(data.paragraphs);
    $('company').value = data.recipient.company || ''; $('raw').value = JSON.stringify(data,null,2);
    $('field-recipient-company').addEventListener('input', () => { $('company').value = $('field-recipient-company').value; filename(); });
    $('field-applicant-name').addEventListener('input', () => { data.applicant.name = $('field-applicant-name').value; filename(); });
  }
  function readForm() {
    const values = {paragraphs: [...$('paragraphs').querySelectorAll('textarea')].map(x=>x.value)};
    for (const [group, keys] of Object.entries(groups)) { values[group]={}; for (const key of keys) values[group][key]=$(`field-${group}-${key}`).value; }
    for (const key of fields) values[key]=$(`field-${key}`).value;
    return mergeForm(data,values);
  }
  function current() {
    const next = validate(view === 'json' ? JSON.parse($('raw').value) : readForm());
    const changed = next.recipient.company !== data.recipient.company;
    data = next; $('company').value = data.recipient.company || ''; if (changed) filename();
    return data;
  }
  function renderParagraphs(paragraphs) {
    $('paragraphs').replaceChildren();
    paragraphs.forEach((text,index) => {
      const block=document.createElement('div'); block.className='paragraph exp-block';
      const label=document.createElement('label'); label.textContent=`Paragraph ${index+1}`;
      const area=document.createElement('textarea'); area.className='styled-textarea'; area.value=text; area.id=`paragraph-${index}`; label.htmlFor=area.id; label.className='paragraph-label';
      const actions=document.createElement('div'); actions.className='actions';
      for (const [name,delta] of [['Move up',-1],['Move down',1],['Remove',0]]) {
        const button=document.createElement('button'); button.textContent=name; button.className=delta ? 'btn-secondary' : 'remove-btn'; addIcon(button, delta === -1 ? 'up' : delta === 1 ? 'down' : 'delete');
        button.disabled=delta ? index+delta<0 || index+delta>=paragraphs.length : false;
        button.onclick=()=>{ const list=[...$('paragraphs').querySelectorAll('textarea')].map(x=>x.value); if(delta) [list[index],list[index+delta]]=[list[index+delta],list[index]]; else list.splice(index,1); renderParagraphs(list); };
        actions.append(button);
      }
      block.append(label,area,actions); $('paragraphs').append(block);
    });
  }
  function switchView(next) {
    if(next===view) return;
    // Parse before hiding the current editor: invalid JSON never blanks the page.
    data = view==='json' ? validate(JSON.parse($('raw').value)) : readForm();
    const oldCompany=$('company').value; render(); if(oldCompany!==$('company').value) filename();
    view=next; $('form-view').hidden=next!=='form'; $('json-view').hidden=next!=='json';
    $('form-tab').setAttribute('aria-pressed',next==='form'); $('json-tab').setAttribute('aria-pressed',next==='json'); status('');
  }
  async function reset() {
    const body=await api(`${base}/base`,{cache:'no-store'}); data=validate(body.cover_letter); activeId=null; render(); filename(); status('Default cover letter loaded.');
  }
  function payload() {
    return {cover_letter_data:current(),file_name:$('filename').value, id:activeId,
      font_size:Number($('font-size').value), top_margin:Number($('vertical-margin').value),bottom_margin:Number($('vertical-margin').value),side_margin:Number($('side-margin').value)};
  }
  async function pdf(preview) {
    const requestId=++pdfRequest;
    try {
      const body=payload();
      $('pdf-status').hidden=true;
      status('Generating cover letter PDF…');
      const response=await fetch(`${base}/pdf${preview?'?preview=true':''}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      if (requestId!==pdfRequest) return;
      if(!response.ok) throw Error((await response.json()).error || 'PDF generation failed');
      const blob=await response.blob();
      if(requestId!==pdfRequest) return;
      const url=URL.createObjectURL(blob);
      if(preview) { if(blobUrl) URL.revokeObjectURL(blobUrl); blobUrl=url; $('pdf-frame').src=url; }
      else { const a=document.createElement('a'); a.href=url; a.download=body.file_name || 'Cover_Letter.pdf'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); }
      status(preview?'Preview ready.':'Cover letter downloaded.');
    } catch (e) {
      if (requestId!==pdfRequest) return;
      // Never leave a stale successful preview displayed after a rejected document.
      $('pdf-frame').removeAttribute('src');
      if(blobUrl) { URL.revokeObjectURL(blobUrl); blobUrl=null; }
      $('pdf-status').textContent=e.message; $('pdf-status').hidden=false;
      status(e.message,true);
    }
  }
  async function listSaved() {
    $('saved-list').textContent='Loading…';
    try {
      const body=await api(`${base}/saved`); $('saved-count').textContent=body.total; $('saved-list').replaceChildren();
      if(!body.cover_letters.length) { const empty=document.createElement('div'); empty.className='saved-document-empty'; empty.textContent='No saved cover letters yet.'; $('saved-list').append(empty); }
      for(const item of body.cover_letters) {
        const block=document.createElement('div'); block.className='saved-item history-item saved-document-item'+(activeId===item.id?' active':'');
        const heading=document.createElement('div'); heading.className='saved-document-heading';
        const title=document.createElement('strong'); title.className='saved-document-title'; title.textContent=item.company_name;
        const date=document.createElement('span'); date.className='saved-document-date'; date.textContent=item.created_at ? new Date(item.created_at).toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'}) : '';
        heading.append(title,date);
        const filename=document.createElement('div'); filename.className='saved-document-filename'; filename.textContent=item.file_name;
        const load=document.createElement('button'); load.textContent='Load'; load.className='btn-secondary saved-document-load'; addIcon(load, 'document'); load.onclick=guard(async()=>{
          if(!confirm('Load this letter and replace the current editor contents?')) return;
          const result=await api(`${base}/saved/${item.id}`); data=validate(result.cover_letter.cover_letter_data); activeId=result.cover_letter.id; render(); $('filename').value=result.cover_letter.file_name; $('saved').close(); status('Saved letter loaded.');
        });
        const remove=document.createElement('button'); remove.textContent='Delete'; remove.className='remove-btn'; remove.onclick=guard(async()=>{
          if(!confirm(`Delete the saved letter for ${item.company_name}?`)) return;
          await api(`${base}/saved/${item.id}`,{method:'DELETE'}); if(activeId===item.id) activeId=null; status('Cover letter deleted successfully!'); showToast('Cover letter deleted successfully!'); await listSaved();
        });
        const actions=document.createElement('div'); actions.className='saved-document-actions'; actions.append(load,remove); block.append(heading,filename,actions); $('saved-list').append(block);
      }
    } catch(e) { $('saved-count').textContent='—'; $('saved-list').textContent=e.message; showToast(e.message,'error'); }
  }
  $('form-tab').onclick=guard(()=>switchView('form')); $('json-tab').onclick=guard(()=>switchView('json'));
  $('format-json').onclick=guard(()=>{ current(); $('raw').value=JSON.stringify(data,null,2); });
  $('copy-json').onclick=guard(async()=>{ current(); await navigator.clipboard.writeText(JSON.stringify(data,null,2)); status('JSON copied.'); });
  // A valid pasted JSON document is authoritative for its recipient company.
  $('raw').addEventListener('input',()=>{try { const parsed=validate(JSON.parse($('raw').value)); const changed=$('company').value!==(parsed.recipient.company || ''); data=parsed; $('company').value=data.recipient.company || ''; if(changed) filename(); } catch (_) { /* Keep incomplete JSON editable. */ } });
  $('company').addEventListener('input',()=>{try { if(view==='json') {const parsed=validate(JSON.parse($('raw').value)); parsed.recipient.company=$('company').value; data=parsed; $('raw').value=JSON.stringify(data,null,2);} else {data.recipient.company=$('company').value; $('field-recipient-company').value=$('company').value;} filename();} catch(e) { $('company').value=data.recipient.company || ''; status('Fix the JSON before changing Target Company.',true);} });
  $('add-paragraph').onclick=()=>renderParagraphs([...$('paragraphs').querySelectorAll('textarea')].map(x=>x.value).concat(''));
  $('preview-open').onclick=guard(async()=>{current(); $('preview').showModal(); await pdf(true);});
  $('preview-close').onclick=()=>$('preview').close(); $('preview-refresh').onclick=guard(()=>pdf(true));
  for(const id of ['font-size','vertical-margin','side-margin']) $(id).onchange=guard(()=>pdf(true));
  $('download').onclick=$('preview-download').onclick=guard(()=>pdf(false));
  $('saved-open').onclick=()=>{$('saved').showModal(); listSaved();}; $('saved-close').onclick=()=>$('saved').close();
  function duplicateChoice(conflict) {
    return new Promise(resolve=>{
      const dialog=$('duplicate-dialog');
      $('duplicate-message').textContent=`A letter for "${conflict.company_name}" named "${conflict.file_name}" already exists. Choose what to do. No changes have been saved.`;
      let choice='cancel';
      for(const [id,value] of [['duplicate-replace','replace'],['duplicate-rename','rename'],['duplicate-cancel','cancel']]) {
        $(id).onclick=()=>{choice=value;dialog.close();};
      }
      dialog.addEventListener('close',()=>resolve(choice),{once:true}); dialog.showModal();
    });
  }
  async function saveLetter() {
    const button=$('save'); button.disabled=true;
    // Clear any old success notification while this operation is pending.
    clearTimeout(toastTimer); $('toast').hidePopover(); $('toast').style.display='none';
    try {
      const request=payload();
      status('Saving cover letter…');
      let result;
      try {
        result=await api(`${base}/save`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
      } catch(error) {
        if(error.details?.code!=='duplicate_document') throw error;
        const conflict=error.details.conflict;
        const choice=await duplicateChoice(conflict);
        if(choice==='rename') { status('Choose a different Output PDF Filename, then save again. Your edits are intact.'); $('filename').focus(); $('filename').select(); return; }
        if(choice!=='replace' || !confirm(`Replace the saved letter "${conflict.file_name}" for ${conflict.company_name}? Its saved content will be overwritten.`)) {
          status('Save cancelled. Your edits are intact.'); return;
        }
        result=await api(`${base}/save`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...request,replace_id:conflict.id,replace_updated_at:conflict.updated_at})});
      }
      activeId=result.saved.id;
      const message=result.action==='updated' ? 'Cover letter updated successfully!' : 'Cover letter saved successfully!';
      status(message); showToast(message); await listSaved();
    } catch(error) {
      if(error.details?.code==='stale_record') activeId=null;
      status(error.message,true); showToast(error.message,'error');
    } finally {button.disabled=false;}
  }
  $('save').onclick=saveLetter;
  $('reset').onclick=guard(async()=>{if(confirm('Reset to the default cover letter? Unsaved edits will be replaced.')) await reset();});
  for(const id of ['preview-open','download','save','reset','json-tab']) $(id).disabled=true;
  listSaved();
  reset().then(()=>{for(const id of ['preview-open','download','save','reset','json-tab']) $(id).disabled=false;}).catch(e=>status(e.message,true));
})(typeof globalThis !== 'undefined' ? globalThis : window);
