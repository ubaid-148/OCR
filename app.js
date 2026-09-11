const form = document.querySelector('form');
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = form.querySelector('button');
  const status = document.querySelector('#progress');
  const result = document.querySelector('#result');
  const id = crypto.randomUUID();
  const started = Date.now();
  let polling = false;
  let finished = false;
  button.disabled = true;
  result.textContent = '';
  status.textContent = 'Uploading PDF…';
  const timer = setInterval(async () => {
    if (polling) return;
    polling = true;
    try {
      const response = await fetch('/progress?id=' + id, {cache: 'no-store'});
      const data = await response.json();
      if (finished) return;
      status.textContent = data.stage + ' — ' + Math.floor((Date.now() - started) / 1000) + ' seconds';
    } catch (_) {
      if (finished) return;
      status.textContent = 'Waiting for server connection…';
    } finally { polling = false; }
  }, 2000);
  try {
    const response = await fetch('/', {method: 'POST', body: new FormData(form), headers: {'X-Progress-ID': id}});
    const data = await response.json();
    result.textContent = JSON.stringify(data, null, 2);
    status.textContent = (response.ok ? 'Completed' : 'Could not process PDF') + ' in ' + Math.floor((Date.now() - started) / 1000) + ' seconds';
  } catch (_) {
    status.textContent = 'Connection lost. Check the Colab runtime and OCR server log before retrying.';
  } finally {
    finished = true;
    clearInterval(timer);
    button.disabled = false;
  }
});
