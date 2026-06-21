export function exportToPDF(title: string, contentHtml: string): void {
  const win = window.open("", "_blank");
  if (!win) return;
  win.document.write(`<!DOCTYPE html><html><head>
    <title>${title}</title>
    <style>
      body { font-family: monospace; padding: 2rem; max-width: 800px; margin: 0 auto; color: #111; }
      h1 { font-size: 1.4rem; border-bottom: 2px solid #333; padding-bottom: 0.5rem; }
      h2 { font-size: 1.1rem; margin-top: 1.5rem; }
      pre, p { white-space: pre-wrap; font-size: 0.85rem; line-height: 1.6; }
      .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; }
    </style>
    </head><body>${contentHtml}
    <script>window.onload = () => { window.print(); }<\/script>
    </body></html>`);
  win.document.close();
}
