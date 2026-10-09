document.querySelector('[data-back]')?.addEventListener('click', () => history.back());
document.querySelector('[data-open-publish]')?.addEventListener('click', () => {
  const panel = document.querySelector('#publish-options');
  panel.open = true;
  document.querySelector('.notes-menu').open = false;
  panel.querySelector('select,button').focus();
});
document.querySelectorAll('[data-job-href]').forEach(row => {
  row.addEventListener('click', event => {
    if (event.target.closest('a,button,input,select') || window.getSelection().toString()) return;
    if (event.ctrlKey || event.metaKey) window.open(row.dataset.jobHref, '_blank', 'noopener');
    else location.href = row.dataset.jobHref;
  });
});
const editor = document.querySelector('#notes-editor');
const statusText = document.querySelector('#editor-status');
let dirty = false;
let richEditor;
const richHost = document.querySelector('#rich-editor');
const notesForm = document.querySelector('#editor-form');
const fullscreenButton = document.querySelector('#notes-fullscreen');
const notesReading = document.querySelector('#notes-reading');
let saveTimer = null;
let saveInFlight = null;
let closingFullscreen = false;

function editorContent() {
  return richEditor ? richEditor.getMarkdown() : editor.value;
}

function scheduleFullscreenSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { void saveDraftQuietly(); }, 900);
}

async function sendDraftSave(content) {
  const data = new FormData(notesForm);
  data.set('content', content);
  const response = await fetch(notesForm.action, {
    method: 'POST', body: data, headers: {Accept: 'application/json'}
  });
  let result;
  try { result = await response.json(); } catch { result = null; }
  if (!response.ok || response.redirected || !Number.isInteger(result?.revision)) {
    throw new Error(result?.error || 'Save failed. Your edits are still here; try saving again.');
  }
  notesForm.querySelector('input[name="revision"]').value = result.revision;
  if (editorContent() === content) {
    dirty = false;
    statusText.textContent = 'Saved';
  } else {
    dirty = true;
    statusText.textContent = 'Saving latest changes…';
  }
}

async function saveDraftQuietly() {
  clearTimeout(saveTimer);
  saveTimer = null;
  if (saveInFlight) {
    const saved = await saveInFlight;
    if (!saved) return false;
    return dirty ? saveDraftQuietly() : true;
  }
  if (!dirty) return true;

  const content = editorContent();
  editor.value = content;
  statusText.textContent = 'Saving…';
  const request = (async () => {
    try {
      await sendDraftSave(content);
      return true;
    } catch (error) {
      statusText.textContent = error.message;
      return false;
    }
  })();
  saveInFlight = request;
  const saved = await request;
  if (saveInFlight === request) saveInFlight = null;
  if (!saved) return false;
  return dirty ? saveDraftQuietly() : true;
}

function markEditorDirty() {
  dirty = true;
  statusText.textContent = 'Unsaved changes';
  if (richEditor && notesReading?.open) scheduleFullscreenSave();
}

async function closeFullscreenAfterSave() {
  if (closingFullscreen) return;
  closingFullscreen = true;
  try {
    if (richEditor && dirty && !(await saveDraftQuietly())) return;
    notesReading.close();
  } finally {
    closingFullscreen = false;
  }
}

if (richHost && window.toastui?.Editor) {
  const original = editor.value;
  let ready = false;
  richEditor = new toastui.Editor({
    el: richHost, height: '700px', initialEditType: 'wysiwyg', previewStyle: 'tab',
    initialValue: original, theme: 'dark', hideModeSwitch: true, usageStatistics: false,
    customHTMLSanitizer: html => DOMPurify.sanitize(html),
    events: { change: () => { if (ready) markEditorDirty(); } }
  });
  document.querySelector('#source-fallback').hidden = true;
  ready = true;
  function mode(type) {
    ready = false;
    richEditor.changeMode(type);
    document.querySelectorAll('[data-editor-mode]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.editorMode === type));
    });
    ready = true;
  }
  document.querySelectorAll('[data-editor-mode]').forEach(button => {
    button.addEventListener('click', () => mode(button.dataset.editorMode));
  });
  document.addEventListener('keydown', event => {
    if ((event.target.closest('#editor-form') || document.querySelector('#notes-reading')?.open) &&
        (event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'v') {
      event.preventDefault(); event.stopPropagation();
      mode(richEditor.isMarkdownMode() ? 'wysiwyg' : 'markdown');
      richEditor.focus();
    }
  }, true);
}
editor?.addEventListener('input', markEditorDirty);
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && notesReading?.open) {
    event.preventDefault();
    event.stopPropagation();
    void closeFullscreenAfterSave();
  }
}, true);
notesReading?.addEventListener('cancel', event => {
  if (richEditor) {
    event.preventDefault();
    void closeFullscreenAfterSave();
  }
});
const editorHome = richHost?.parentNode;
const editorNext = richHost?.nextSibling;
fullscreenButton?.addEventListener('click', async () => {
  fullscreenButton.disabled = true;
  try {
    if (richEditor) {
      notesReading.querySelector('article').hidden = true;
      notesReading.append(richHost);
      notesReading.classList.add('editable-reading');
      notesReading.showModal();
      document.body.classList.add('notes-reading-open');
      richEditor.setHeight('calc(100dvh - 64px)');
      richEditor.focus();
      if (dirty) scheduleFullscreenSave();
      return;
    }
    const data = new FormData();
    data.set('content', richEditor && dirty ? richEditor.getMarkdown() : editor.value);
    data.set('csrf', notesForm.querySelector('input[name=csrf]').value);
    const response = await fetch('/preview', {method: 'POST', body: data});
    if (!response.ok || response.redirected) throw new Error('Unable to open notes. Sign in again or retry.');
    notesReading.querySelector('article').innerHTML = await response.text();
    notesReading.scrollTop = 0;
    notesReading.showModal();
    document.body.classList.add('notes-reading-open');
  } catch (error) {
    statusText.textContent = error.message;
  } finally {
    fullscreenButton.disabled = false;
  }
});
notesReading?.addEventListener('close', () => {
  if (richEditor && richHost.parentNode === notesReading) {
    editorHome.insertBefore(richHost, editorNext);
    richEditor.setHeight('700px');
    notesReading.classList.remove('editable-reading');
    notesReading.querySelector('article').hidden = false;
  }
  document.body.classList.remove('notes-reading-open');
  fullscreenButton.focus();
});
window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
document.querySelector('#editor-form')?.addEventListener('submit', event => {
  if (richEditor && dirty) editor.value = richEditor.getMarkdown();
  if (event.submitter?.hasAttribute('data-publish')) {
    if (!confirm('Publish these notes to the configured course channel?')) {
      event.preventDefault(); return;
    }
    clearTimeout(saveTimer);
    dirty = false;
    return;
  }
  event.preventDefault();
  void saveDraftQuietly();
});
document.querySelector('#preview-button')?.addEventListener('click', async () => {
  const button = document.querySelector('#preview-button'); button.disabled = true;
  try {
    const data = new FormData(); data.set('content', editor.value);
    data.set('csrf', document.querySelector('#editor-form input[name=csrf]').value);
    const response = await fetch('/preview', { method: 'POST', body: data });
    if (!response.ok || response.redirected) throw new Error('Preview failed. Save or sign in again.');
    document.querySelector('#preview').innerHTML = await response.text();
    statusText.textContent = dirty ? 'Preview updated · unsaved changes' : 'Preview updated';
  } catch (error) { statusText.textContent = error.message; }
  finally { button.disabled = false; }
});
const jobPanel = document.querySelector('[data-job]');
if (jobPanel && ['queued', 'running'].includes(jobPanel.dataset.status)) {
  let stopped = false;
  async function poll() {
    if (stopped) return;
    try {
      const response = await fetch(`/api/jobs/${jobPanel.dataset.job}`);
      if (!response.ok || response.redirected) { stopped = true; return; }
      const job = await response.json();
      if ((job.control || '') !== jobPanel.dataset.control || job.status !== jobPanel.dataset.status) {
        stopped = true; location.reload(); return;
      }
      document.querySelector('#job-status').textContent = job.status.replaceAll('_', ' ');
      document.querySelector('#job-stage').textContent = job.stage;
      document.querySelector('#job-error').textContent = job.error || '';
      if (!['queued', 'running'].includes(job.status)) { stopped = true; location.reload(); return; }
    } catch { /* Transient network failures don't replace saved progress. */ }
    setTimeout(poll, 4000);
  }
  setTimeout(poll, 4000);
}
const publicationPanel = document.querySelector('[data-publication]');
if (publicationPanel && ['queued', 'running'].includes(publicationPanel.dataset.status)) {
  async function pollPublication() {
    try {
      const response = await fetch(`/api/jobs/${publicationPanel.dataset.publication}`);
      if (!response.ok || response.redirected) return;
      const job = await response.json();
      document.querySelector('#publication-status').textContent = job.status.replaceAll('_', ' ');
      document.querySelector('#publication-stage').textContent = job.stage;
      if (!['queued', 'running'].includes(job.status)) { location.reload(); return; }
    } catch { /* Retry after transient network failures. */ }
    setTimeout(pollPublication, 4000);
  }
  setTimeout(pollPublication, 4000);
}
