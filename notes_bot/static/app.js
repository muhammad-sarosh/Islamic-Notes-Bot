document.querySelector('[data-back]')?.addEventListener('click', () => history.back());
const editor = document.querySelector('#notes-editor');
const statusText = document.querySelector('#editor-status');
let dirty = false;
editor?.addEventListener('input', () => { dirty = true; statusText.textContent = 'Unsaved changes'; });
window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
document.querySelector('#editor-form')?.addEventListener('submit', event => {
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
