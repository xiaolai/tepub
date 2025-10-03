(function () {
  const data = window.BOOK_DATA || { spine: [], toc: [] };
  const contentRoot = "content/";
  const contentEl = document.getElementById("content");
  const tocEl = document.getElementById("toc");
  const titleEl = document.getElementById("book-title");
  const toggleOriginal = document.getElementById("toggle-original");
  const toggleTranslation = document.getElementById("toggle-translation");
  const prevBtn = document.getElementById("prev-chapter");
  const nextBtn = document.getElementById("next-chapter");

  const chapters = data.spine || [];
  const tocEntries = data.toc || chapters;
  const documents = data.documents || {};

  let currentIndex = 0;

  titleEl.textContent = data.title || document.title;

  function normaliseHref(href) {
    if (!href) return "";
    return href.replace(/^\.\//, "").replace(/^\//, "");
  }

  function updateNavButtons() {
    if (!prevBtn || !nextBtn) return;
    prevBtn.disabled = currentIndex <= 0;
    nextBtn.disabled = currentIndex >= chapters.length - 1;
  }

  function renderToc() {
    const ul = document.createElement("ul");
    tocEntries.forEach((entry, index) => {
      const li = document.createElement("li");
      li.style.marginLeft = `${(entry.level || 0) * 12}px`;
      const link = document.createElement("a");
      const rawHref = entry.href || chapters[index]?.href || "";
      const targetHref = normaliseHref(rawHref);
      link.href = targetHref ? contentRoot + targetHref : "#";
      link.dataset.href = targetHref;
      link.textContent = entry.title || entry.href || targetHref || "(untitled)";
      link.addEventListener("click", (event) => {
        event.preventDefault();
        if (!targetHref) return;
        openChapter(targetHref);
      });
      li.appendChild(link);
      ul.appendChild(li);
    });
    tocEl.innerHTML = "";
    tocEl.appendChild(ul);
  }

  function setActiveLink(activeLink) {
    tocEl.querySelectorAll("a").forEach((link) => link.classList.remove("active"));
    if (activeLink) {
      activeLink.classList.add("active");
    }
  }

  function renderChapter(htmlString, anchor) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(htmlString, "text/html");
    const body = doc.querySelector("body");
    const wrapper = document.createElement("article");
    wrapper.className = "chapter";
    wrapper.innerHTML = body ? body.innerHTML : htmlString;
    contentEl.innerHTML = "";
    contentEl.appendChild(wrapper);
    applyVisibility();
    if (anchor) {
      const node = contentEl.querySelector(`#${CSS.escape(anchor)}`);
      if (node) {
        node.scrollIntoView({ block: "start" });
      }
    }
  }

  async function loadChapter(target, anchor) {
    if (!target) return;
    const inline = documents[target];
    if (inline) {
      renderChapter(inline, anchor);
      return;
    }
    try {
      const response = await fetch(contentRoot + target);
      const text = await response.text();
      renderChapter(text, anchor);
    } catch (error) {
      console.error("Failed to load chapter", target, error);
    }
  }

  async function openChapter(targetHref) {
    const target = normaliseHref(targetHref);
    if (!target) return;
    const [file, hash] = target.split("#");
    await loadChapter(file, hash);
    const idx = chapters.findIndex((ch) => normaliseHref(ch.href).split("#")[0] === file);
    if (idx >= 0) {
      currentIndex = idx;
    }
    updateNavButtons();
    const tocLink = tocEl.querySelector(`a[data-href="${CSS.escape(target)}"]`) ||
      tocEl.querySelector(`a[data-href="${CSS.escape(file)}"]`);
    if (tocLink) {
      setActiveLink(tocLink);
      tocLink.scrollIntoView({ block: "nearest" });
    }
  }

  function applyVisibility() {
    document.body.classList.toggle("hide-original", !toggleOriginal.checked);
    document.body.classList.toggle("hide-translation", !toggleTranslation.checked);
  }

  toggleOriginal.addEventListener("change", applyVisibility);
  toggleTranslation.addEventListener("change", applyVisibility);

  contentEl.addEventListener("click", (event) => {
    const anchor = event.target.closest("a");
    if (!anchor) return;
    const href = anchor.getAttribute("href");
    if (!href) return;
    if (href.startsWith(contentRoot)) {
      event.preventDefault();
      const target = href.slice(contentRoot.length);
      openChapter(target);
    }
  });

  if (prevBtn && nextBtn) {
    prevBtn.addEventListener("click", () => {
      const nextIdx = currentIndex - 1;
      if (nextIdx >= 0) {
        openChapter(chapters[nextIdx].href);
      }
    });
    nextBtn.addEventListener("click", () => {
      const nextIdx = currentIndex + 1;
      if (nextIdx < chapters.length) {
        openChapter(chapters[nextIdx].href);
      }
    });
  }

  renderToc();
  applyVisibility();
  updateNavButtons();
  if (chapters.length) {
    openChapter(chapters[0].href);
  }
})();
