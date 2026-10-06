document.querySelector('[data-back]')?.addEventListener('click', () => history.back());
const editor = document.querySelector('#notes-editor');
const statusText = document.querySelector('#editor-status');
let dirty = false;
let richEditor;
const richHost = document.querySelector('#rich-editor');
if (richHost && window.toastui?.Editor) {
  const original = editor.value;
  let ready = false;
  richEditor = new toastui.Editor({
    el: richHost, height: '700px', initialEditType: 'wysiwyg', previewStyle: 'tab',
    initialValue: original, theme: 'dark', hideModeSwitch: true, usageStatistics: false,
    customHTMLSanitizer: html => DOMPurify.sanitize(html),
    events: { change: () => { if (ready) { dirty = true; statusText.textContent = 'Unsaved changes'; } } }
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
  document.querySelector('#editor-form').addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'v') {
      event.preventDefault(); event.stopPropagation();
      mode(richEditor.isMarkdownMode() ? 'wysiwyg' : 'markdown');
    }
  }, true);
}
editor?.addEventListener('input', () => { dirty = true; statusText.textContent = 'Unsaved changes'; });
const notesForm = document.querySelector('#editor-form');
const fullscreenButton = document.querySelector('#notes-fullscreen');
function resizeNotes() {
  const active = document.fullscreenElement === notesForm || notesForm?.classList.contains('notes-fullscreen');
  if (!fullscreenButton) return;
  fullscreenButton.textContent = active ? 'Exit fullscreen' : 'Fullscreen notes';
  fullscreenButton.setAttribute('aria-pressed', String(active));
  document.body.classList.toggle('notes-fullscreen-open', active);
  if (richEditor) {
    const available = window.innerHeight - (richHost.getBoundingClientRect().top - notesForm.getBoundingClientRect().top) - 65;
    richEditor.setHeight(active ? `${Math.max(250, available)}px` : '700px');
  }
}
fullscreenButton?.addEventListener('click', async () => {
  if (document.fullscreenElement === notesForm) {
    await document.exitFullscreen();
  } else if (notesForm.classList.contains('notes-fullscreen')) {
    notesForm.classList.remove('notes-fullscreen');
  } else {
    try {
      if (!notesForm.requestFullscreen) throw new Error('Fullscreen unavailable');
      await notesForm.requestFullscreen();
    } catch {
      notesForm.classList.add('notes-fullscreen');
    }
  }
  resizeNotes();
});
document.addEventListener('fullscreenchange', resizeNotes);
window.addEventListener('resize', resizeNotes);
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && notesForm?.classList.contains('notes-fullscreen')) {
    notesForm.classList.remove('notes-fullscreen');
    resizeNotes();
  }
});
window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
document.querySelector('#editor-form')?.addEventListener('submit', event => {
  if (richEditor && dirty) editor.value = richEditor.getMarkdown();
  if (event.submitter?.hasAttribute('data-publish') && !confirm('Publish these notes to the configured course channel?')) {
    event.preventDefault(); return;
  }
  dirty = false;
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
