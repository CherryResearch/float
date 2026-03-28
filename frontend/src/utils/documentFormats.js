export const PLAIN_TEXT_DOC_EXTENSIONS = new Set(["txt", "text"]);
export const MARKDOWN_DOC_EXTENSIONS = new Set([
  "md",
  "markdown",
  "mdown",
  "mkd",
]);
export const EDITABLE_TEXT_DOC_EXTENSIONS = new Set([
  ...PLAIN_TEXT_DOC_EXTENSIONS,
  ...MARKDOWN_DOC_EXTENSIONS,
]);

export const getDocumentExtension = (value) => {
  if (typeof value !== "string") return "";
  const clean = value.split("?")[0].split("#")[0].trim().toLowerCase();
  const dot = clean.lastIndexOf(".");
  if (dot < 0 || dot >= clean.length - 1) return "";
  return clean.slice(dot + 1);
};
