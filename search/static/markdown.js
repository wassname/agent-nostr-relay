// Browser enhancement of the Markdown source. -- Codex/GPT-6
const source = document.getElementById('markdown');
fetch('/render-markdown', {
  method: 'POST',
  headers: {'Content-Type': 'text/plain'},
  body: source.textContent,
}).then(async response => {
  if (!response.ok) throw new Error(`Markdown rendering failed: ${response.status}`);
  const article = document.createElement('article');
  article.innerHTML = await response.text();
  source.replaceWith(article);
  const form = document.createElement('form');
  form.action = '/search';
  const input = document.createElement('input');
  input.name = 'q';
  input.placeholder = 'Search the relay';
  input.value = new URLSearchParams(location.search).get('q') || '';
  const button = document.createElement('button');
  button.textContent = 'Search';
  form.append(input, button);
  article.prepend(form);
});
