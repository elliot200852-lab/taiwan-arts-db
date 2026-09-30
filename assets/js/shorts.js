/*
 * YouTube 短片 click-to-load facade（2026-09-30）。
 * 頁面只放縮圖與 <a href="https://www.youtube.com/shorts/ID">（無 JS 也能點去 YouTube）；
 * 點縮圖時才把 .yt-facade 內換成 youtube.com/embed iframe（不用 nocookie，要算觀看數）。
 * 事件委派，頁面上有幾個 facade 都只掛一個 listener；不依賴其他腳本。
 */
(function () {
  "use strict";
  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var t = e.target;
    if (!t || !t.closest) return;
    var link = t.closest(".yt-facade .yt-thumb");
    if (!link) return;
    var box = link.closest(".yt-facade");
    var id = box && box.getAttribute("data-yt");
    if (!id || !/^[A-Za-z0-9_-]{6,20}$/.test(id)) return;
    e.preventDefault();
    var f = document.createElement("iframe");
    f.src = "https://www.youtube.com/embed/" + id + "?autoplay=1&playsinline=1";
    f.title = box.getAttribute("data-title") || "YouTube 短片";
    f.setAttribute("allow", "autoplay; encrypted-media; picture-in-picture");
    f.setAttribute("allowfullscreen", "");
    while (box.firstChild) box.removeChild(box.firstChild);
    box.appendChild(f);
    box.classList.add("is-playing");
  });
})();
