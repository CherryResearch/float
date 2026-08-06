export const captionGenerationErrorMessage = (error) => {
  if (error?.response?.status !== 409) {
    return "Automatic captioning failed. You can still write and save a caption manually.";
  }
  const detail = String(error?.response?.data?.detail || "").trim().toLowerCase();
  if (detail.includes("manual caption")) {
    return "This caption was written manually. Edit it directly instead of replacing it automatically.";
  }
  if (detail.includes("disabled")) {
    return "Automatic captioning is off. Turn it on in Settings or write and save a caption manually.";
  }
  if (detail.includes("not ready")) {
    return "The saved caption engine is not ready. Check its status in Settings or write a caption manually.";
  }
  return "Automatic captioning could not continue because the saved caption state changed. Refresh and try again, or write a caption manually.";
};
