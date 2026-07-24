// Minimal, safe markdown → HTML. Escapes first, then applies a small subset:
// code fences/spans, bold/italic, headings, and unordered/ordered lists.

function escape(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!,
  );
}

export function renderMarkdown(src: string): string {
  const blocks: string[] = [];
  let s = escape(src);
  s = s.replace(/```(\w*)\n?([\s\S]*?)```/g, (_m, _lang, code) => {
    blocks.push(`<pre><code>${code.replace(/\n$/, "")}</code></pre>`);
    return `%%CB${blocks.length - 1}%%`;
  });
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");

  const out: string[] = [];
  let list: "ul" | "ol" | null = null;
  const shut = () => {
    if (list) {
      out.push(`</${list}>`);
      list = null;
    }
  };
  for (const line of s.split("\n")) {
    const h = line.match(/^#{1,4}\s+(.*)$/);
    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (h) {
      shut();
      out.push(`<h3>${h[1]}</h3>`);
    } else if (ul) {
      if (list !== "ul") {
        shut();
        out.push("<ul>");
        list = "ul";
      }
      out.push(`<li>${ul[1]}</li>`);
    } else if (ol) {
      if (list !== "ol") {
        shut();
        out.push("<ol>");
        list = "ol";
      }
      out.push(`<li>${ol[1]}</li>`);
    } else if (!line.trim()) {
      shut();
      out.push("");
    } else if (/^%%CB\d+%%$/.test(line.trim())) {
      shut();
      out.push(line.trim());
    } else {
      shut();
      out.push(`<p>${line}</p>`);
    }
  }
  shut();
  return out.join("\n").replace(/%%CB(\d+)%%/g, (_m, i) => blocks[+i]);
}
