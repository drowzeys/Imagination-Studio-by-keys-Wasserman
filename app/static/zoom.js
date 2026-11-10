"use strict";
// A picture shown larger, over the page. A click on a picture marked data-zoom (the Library's grid marks its own) or in
// Cast & Places opens it; a click on it, the close button or Escape closes it. A picture inside a button or a
// link keeps what its button does: it is not taken over.
const ZOOM_IN = '[data-room-view="cast"] img, img[data-zoom]';
let zoomLayer = null;
function zoomClose() { if (zoomLayer) { zoomLayer.remove(); zoomLayer = null; } }
function zoomOpen(src, alt) {
  zoomClose();
  const picture = document.createElement("img");
  picture.src = src; picture.alt = alt || "";
  const shut = document.createElement("button");
  shut.type = "button"; shut.className = "zoom-close"; shut.textContent = "×"; shut.setAttribute("aria-label", "Close");
  zoomLayer = document.createElement("div");
  zoomLayer.className = "zoom-layer"; zoomLayer.setAttribute("role", "dialog"); zoomLayer.setAttribute("aria-label", "Picture");
  zoomLayer.append(picture, shut);
  zoomLayer.addEventListener("click", zoomClose);
  document.body.append(zoomLayer);
}
const zoomable = target => {
  const picture = target && target.closest ? target.closest("img") : null;
  return picture && picture.matches(ZOOM_IN) && !picture.closest("button, a, .zoom-layer") ? picture : null;
};
if (typeof document !== "undefined" && document.addEventListener) {
  document.addEventListener("click", e => { const picture = zoomable(e.target); if (picture) { e.preventDefault(); zoomOpen(picture.currentSrc || picture.src, picture.alt); } });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && zoomLayer) zoomClose(); });
}
