import React, { useMemo } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";

marked.setOptions({
  breaks: true,
  gfm: true,
});

const MarkdownPreview = ({ text = "" }) => {
  const html = useMemo(() => {
    const rendered = marked.parse(String(text || ""));
    return DOMPurify.sanitize(rendered);
  }, [text]);

  if (!String(text || "").trim()) {
    return <div className="status-note">(empty document)</div>;
  }

  return <div className="doc-markdown-preview" dangerouslySetInnerHTML={{ __html: html }} />;
};

export default MarkdownPreview;
