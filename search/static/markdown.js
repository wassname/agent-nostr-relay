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
  // Fixed screen cells keep fallback Unicode fonts inside the TV frame. -- PI/OpenAI
  for (const code of article.querySelectorAll('pre > code')) {
    if (!/ \(Y\) ╭─+╮ \(Y\)/u.test(code.textContent)) continue;
    const lines = code.textContent.split('\n');
    code.replaceChildren();
    lines.forEach((line, index) => {
      const screen = line.match(/^(   ╲─┤|     │)(.*)(├─╱|│)(.*)$/u);
      const held = line.includes('˚╲ ╱˚') && line.match(/^(.*?)(\p{Extended_Pictographic}\uFE0F?)(.*)$/u);
      if (held) {  // emoji widths vary by font; a fixed cell keeps the claw item from shifting the row
        const item = document.createElement('span');
        item.className = 'tv-held';
        item.textContent = held[2];
        code.append(held[1], item, held[3]);
      } else if (screen) {
        const face = document.createElement('span');
        face.className = 'tv-screen';
        face.textContent = screen[2];
        code.append(screen[1], face, screen[3], screen[4]);
      } else {
        code.append(line);
      }
      if (index < lines.length - 1) code.append('\n');
    });
  }
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
