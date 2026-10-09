"use client";

import { useMemo } from "react";

// Static rich-document capability set. No URLs, styles, forms, custom elements,
// SVG/MathML or executable content cross this boundary. Parsing in a template is
// inert (including resource elements); rebuild instead of modifying untrusted DOM.
const elements = new Set(
  "h1 h2 h3 h4 h5 h6 p div span section article header footer main aside blockquote pre code strong b em i u s small sub sup br hr ul ol li dl dt dd table caption thead tbody tfoot tr th td a".split(
    " ",
  ),
);
const discard = new Set(
  "script style iframe frame object embed template noscript svg math form input button textarea select option link meta base audio video source img picture".split(
    " ",
  ),
);

export function artifactPreviewDocument(content: string): string {
  if (typeof document === "undefined") return "";
  const input = document.createElement("template");
  input.innerHTML = content;
  const output = document.createElement("div");
  const copy = (source: Node, parent: Node) => {
    if (source.nodeType === Node.TEXT_NODE) {
      parent.appendChild(document.createTextNode(source.textContent ?? ""));
      return;
    }
    if (!(source instanceof Element) || discard.has(source.localName)) return;
    const target = elements.has(source.localName)
      ? document.createElement(source.localName === "a" ? "span" : source.localName)
      : document.createDocumentFragment();
    for (const child of source.childNodes) copy(child, target);
    parent.appendChild(target);
  };
  for (const child of input.content.childNodes) copy(child, output);
  return (
    '<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; script-src &#39;none&#39;; style-src &#39;none&#39;; img-src &#39;none&#39;; connect-src &#39;none&#39;; frame-src &#39;none&#39;; object-src &#39;none&#39;; base-uri &#39;none&#39;; form-action &#39;none&#39;"></head><body>' +
    output.innerHTML +
    "</body></html>"
  );
}

/** Shared by execution detail, legacy workbench and existing public share view. */
export function SafeArtifactPreview({
  content,
  title,
  className,
  notice,
}: {
  content: string;
  title: string;
  className?: string;
  notice: string;
}) {
  const srcDoc = useMemo(() => artifactPreviewDocument(content), [content]);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <p className="text-muted-foreground p-2 text-xs">{notice}</p>
      <iframe
        title={title}
        srcDoc={srcDoc}
        sandbox=""
        referrerPolicy="no-referrer"
        className={className ?? "h-96 w-full border bg-white"}
      />
    </div>
  );
}
